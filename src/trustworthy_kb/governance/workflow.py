"""Checkpoint-safe LangGraph wrapper for one L3 knowledge change."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, START, StateGraph

from trustworthy_kb.domain import GovernanceRunId, GovernanceRunStatus, KnowledgeChangeId
from trustworthy_kb.governance.runner import ClaimGovernanceRunner, GovernanceReport


class GovernanceWorkflowState(TypedDict, total=False):
    """Only stable identifiers and aggregate counts are checkpointed."""

    change_id: str
    run_id: str
    status: str
    total: int
    decided: int
    review: int
    failed: int
    quarantined: int


def build_governance_workflow(
    runner: ClaimGovernanceRunner,
    checkpointer: BaseCheckpointSaver[str],
) -> Any:
    async def govern(state: GovernanceWorkflowState) -> GovernanceWorkflowState:
        report = await runner.run_change(KnowledgeChangeId(state["change_id"]))
        return _state(report)

    graph: StateGraph[
        GovernanceWorkflowState,
        None,
        GovernanceWorkflowState,
        GovernanceWorkflowState,
    ] = StateGraph(GovernanceWorkflowState)
    graph.add_node("govern", govern, input_schema=GovernanceWorkflowState)
    graph.add_edge(START, "govern")
    graph.add_edge("govern", END)
    return graph.compile(checkpointer=checkpointer, name="trustworthy-governance")


async def run_governance_workflow(
    runner: ClaimGovernanceRunner,
    checkpoint_path: Path,
    *,
    change_id: KnowledgeChangeId,
) -> GovernanceReport:
    await asyncio.to_thread(checkpoint_path.parent.mkdir, parents=True, exist_ok=True)
    config: RunnableConfig = {"configurable": {"thread_id": str(change_id)}}
    async with AsyncSqliteSaver.from_conn_string(str(checkpoint_path)) as saver:
        await saver.setup()
        graph = build_governance_workflow(runner, saver)
        checkpoint = await saver.aget(config)
        graph_input: GovernanceWorkflowState | None = (
            {"change_id": str(change_id)} if checkpoint is None else None
        )
        result = await graph.ainvoke(graph_input, config=config)
    return GovernanceReport(
        run_id=GovernanceRunId(result["run_id"]),
        change_id=change_id,
        status=GovernanceRunStatus(result["status"]),
        total=result["total"],
        decided=result["decided"],
        review=result["review"],
        failed=result["failed"],
        quarantined=result["quarantined"],
    )


def _state(report: GovernanceReport) -> GovernanceWorkflowState:
    return {
        "run_id": str(report.run_id),
        "change_id": str(report.change_id),
        "status": report.status.value,
        "total": report.total,
        "decided": report.decided,
        "review": report.review,
        "failed": report.failed,
        "quarantined": report.quarantined,
    }


__all__ = [
    "GovernanceWorkflowState",
    "build_governance_workflow",
    "run_governance_workflow",
]
