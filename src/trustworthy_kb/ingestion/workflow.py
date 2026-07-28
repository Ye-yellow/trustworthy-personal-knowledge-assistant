"""LangGraph checkpoint orchestration for the manual ingestion runner."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, START, StateGraph

from trustworthy_kb.domain import IngestionRunId
from trustworthy_kb.ingestion.runner import IngestionReport, ManualIngestionRunner


class IngestionWorkflowState(TypedDict, total=False):
    """Checkpoint-safe state containing no paths, content, or credentials."""

    run_id: str
    inventory_count: int
    captured_items: int
    manifest_hash: str
    planned_items: int
    applied_items: int
    status: str
    total: int
    succeeded: int
    skipped: int
    quarantined: int
    failed: int


def build_ingestion_workflow(
    runner: ManualIngestionRunner,
    checkpointer: BaseCheckpointSaver[str],
) -> Any:
    """Compile the fixed ingestion graph with an explicit checkpointer."""

    async def inventory(state: IngestionWorkflowState) -> IngestionWorkflowState:
        run_id = IngestionRunId(state["run_id"])
        await runner.begin_run(run_id)
        return {"inventory_count": await runner.inventory_count()}

    async def stable_read_and_snapshot(
        state: IngestionWorkflowState,
    ) -> IngestionWorkflowState:
        del state
        manifest = await runner.capture_inventory()
        return {
            "captured_items": len(manifest.entries),
            "manifest_hash": manifest.manifest_hash,
        }

    async def plan(state: IngestionWorkflowState) -> IngestionWorkflowState:
        return {"planned_items": await runner.plan_run(IngestionRunId(state["run_id"]))}

    async def apply_items(state: IngestionWorkflowState) -> IngestionWorkflowState:
        return {"applied_items": await runner.apply_pending(IngestionRunId(state["run_id"]))}

    async def reconcile_run(state: IngestionWorkflowState) -> IngestionWorkflowState:
        report = await runner.reconcile_run(IngestionRunId(state["run_id"]))
        return {
            "status": report.status.value,
            "total": report.total,
            "succeeded": report.succeeded,
            "skipped": report.skipped,
            "quarantined": report.quarantined,
            "failed": report.failed,
        }

    graph: StateGraph[
        IngestionWorkflowState,
        None,
        IngestionWorkflowState,
        IngestionWorkflowState,
    ] = StateGraph(IngestionWorkflowState)
    graph.add_node("inventory", inventory, input_schema=IngestionWorkflowState)
    graph.add_node(
        "stable_read_and_snapshot",
        stable_read_and_snapshot,
        input_schema=IngestionWorkflowState,
    )
    graph.add_node("plan", plan, input_schema=IngestionWorkflowState)
    graph.add_node("apply_items", apply_items, input_schema=IngestionWorkflowState)
    graph.add_node("reconcile_run", reconcile_run, input_schema=IngestionWorkflowState)
    graph.add_edge(START, "inventory")
    graph.add_edge("inventory", "stable_read_and_snapshot")
    graph.add_edge("stable_read_and_snapshot", "plan")
    graph.add_edge("plan", "apply_items")
    graph.add_edge("apply_items", "reconcile_run")
    graph.add_edge("reconcile_run", END)
    return graph.compile(checkpointer=checkpointer, name="trustworthy-ingestion")


async def run_ingestion_workflow(
    runner: ManualIngestionRunner,
    checkpoint_path: Path,
    *,
    run_id: IngestionRunId | None = None,
) -> IngestionReport:
    """Start or resume one run using a dedicated SQLite checkpoint file."""

    selected_run_id = run_id or IngestionRunId.generate()
    await asyncio.to_thread(checkpoint_path.parent.mkdir, parents=True, exist_ok=True)
    config: RunnableConfig = {"configurable": {"thread_id": str(selected_run_id)}}
    try:
        async with AsyncSqliteSaver.from_conn_string(str(checkpoint_path)) as saver:
            await saver.setup()
            graph = build_ingestion_workflow(runner, saver)
            checkpoint = await saver.aget(config)
            graph_input: IngestionWorkflowState | None = (
                {"run_id": str(selected_run_id)} if checkpoint is None else None
            )
            await graph.ainvoke(graph_input, config=config)
    except Exception:
        await runner.fail_planning_run(selected_run_id)
        raise
    return await runner.reconcile_run(selected_run_id)


__all__ = [
    "IngestionWorkflowState",
    "build_ingestion_workflow",
    "run_ingestion_workflow",
]
