"""Safe CLI for L3 governance runs and explicit human-review decisions."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from typing import cast

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncEngine

from trustworthy_kb.config import (
    DatabaseSettings,
    FetchSettings,
    GovernanceSettings,
    LLMSettings,
    SearchSettings,
)
from trustworthy_kb.domain import KnowledgeChangeId, ReviewRequestId, ReviewRequestStatus
from trustworthy_kb.governance.adapters import create_search_gateway
from trustworthy_kb.governance.audit import AuditedEvidenceSearchGateway, AuditedModelGateway
from trustworthy_kb.governance.errors import GovernanceError
from trustworthy_kb.governance.evidence_pack import EvidencePackBuilder
from trustworthy_kb.governance.evidence_sources import (
    DomainTrustResolver,
    EvidenceSourceService,
)
from trustworthy_kb.governance.extraction import ClaimExtractor, SnapshotContentResolver
from trustworthy_kb.governance.fetch import SecureWebFetcher
from trustworthy_kb.governance.quality import QualityPolicyEngine
from trustworthy_kb.governance.review import ReviewService
from trustworthy_kb.governance.runner import ClaimGovernanceRunner
from trustworthy_kb.governance.snapshot_store import EvidenceSnapshotStore
from trustworthy_kb.governance.verifier import EvidenceVerifier
from trustworthy_kb.governance.workflow import run_governance_workflow
from trustworthy_kb.llm import ModelGateway, ModelGatewayError, ModelRouter
from trustworthy_kb.persistence import (
    SqliteUnitOfWorkFactory,
    create_database_engine,
    create_session_factory,
)
from trustworthy_kb.persistence.errors import PersistenceError
from trustworthy_kb.persistence.migrations import assert_schema_current


@dataclass(slots=True)
class _Runtime:
    engine: AsyncEngine
    factory: SqliteUnitOfWorkFactory
    runner: ClaimGovernanceRunner
    fetcher: SecureWebFetcher
    governance_settings: GovernanceSettings

    async def close(self) -> None:
        await self.fetcher.aclose()
        await self.engine.dispose()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="trustworthy-kb-governance")
    subcommands = parser.add_subparsers(dest="command")
    run = subcommands.add_parser("run", help="govern one change or every pending change")
    run.add_argument("--change-id")

    review = subcommands.add_parser("review", help="list or decide review requests")
    review_commands = review.add_subparsers(dest="review_command", required=True)
    review_commands.add_parser("list")
    for action in ("approve", "reject", "cancel"):
        decision = review_commands.add_parser(action)
        decision.add_argument("request_id")
        decision.add_argument("--reason", required=True)
    return parser


async def _build_runtime() -> _Runtime:
    database = DatabaseSettings(_env_file=".env")
    governance = GovernanceSettings(_env_file=".env")
    search = SearchSettings(_env_file=".env")
    fetch = FetchSettings(_env_file=".env")
    llm = LLMSettings(_env_file=".env")
    engine = create_database_engine(database)
    await assert_schema_current(engine)
    factory = SqliteUnitOfWorkFactory(create_session_factory(engine))
    store = EvidenceSnapshotStore(governance.evidence_snapshot_root_value)
    fetcher = SecureWebFetcher(fetch, store)
    gateway = ModelGateway(ModelRouter(llm))
    audited_gateway = AuditedModelGateway(gateway, factory, llm)
    search_gateway = AuditedEvidenceSearchGateway(
        create_search_gateway(search, llm),
        audited_gateway,
        model=search.model or llm.model,
        prompt_version=governance.search_policy_version,
    )
    runner = ClaimGovernanceRunner(
        unit_of_work_factory=factory,
        settings=governance,
        search_settings=search,
        resolver=SnapshotContentResolver(governance.source_snapshot_root_value),
        extractor=ClaimExtractor(
            cast(ModelGateway, audited_gateway),
            prompt_version=governance.extractor_version,
            max_claims=governance.max_claims_per_document,
            max_characters=governance.max_extraction_characters,
        ),
        search=search_gateway,
        fetcher=fetcher,
        evidence_sources=EvidenceSourceService(
            DomainTrustResolver(
                t1_domains=governance.t1_domains,
                t2_domains=governance.t2_domains,
            )
        ),
        pack_builder=EvidencePackBuilder(store, max_sources=search.max_candidates_per_claim),
        verifier=EvidenceVerifier(
            cast(ModelGateway, audited_gateway),
            prompt_version=governance.verifier_version,
        ),
        policy=QualityPolicyEngine(),
        snapshot_store=store,
    )
    return _Runtime(
        engine=engine,
        factory=factory,
        runner=runner,
        fetcher=fetcher,
        governance_settings=governance,
    )


async def _run(args: argparse.Namespace) -> object:
    runtime = await _build_runtime()
    try:
        if args.command in {None, "run"}:
            change_ids = (
                (KnowledgeChangeId(args.change_id),)
                if getattr(args, "change_id", None)
                else await runtime.runner.pending_change_ids()
            )
            return [
                (
                    await run_governance_workflow(
                        runtime.runner,
                        runtime.governance_settings.checkpoint_path_value,
                        change_id=change_id,
                    )
                ).model_dump(mode="json")
                for change_id in change_ids
            ]
        service = ReviewService(runtime.factory)
        if args.review_command == "list":
            return [item.model_dump(mode="json") for item in await service.list_pending()]
        target = {
            "approve": ReviewRequestStatus.APPROVED,
            "reject": ReviewRequestStatus.REJECTED,
            "cancel": ReviewRequestStatus.CANCELLED,
        }[args.review_command]
        decided = await service.decide(
            ReviewRequestId(args.request_id), target, reason_code=args.reason
        )
        return decided.model_dump(mode="json")
    finally:
        await runtime.close()


def main(argv: Sequence[str] | None = None) -> None:
    args = _parser().parse_args(argv)
    try:
        result = asyncio.run(_run(args))
    except Exception as error:
        print(_safe_error(error), file=sys.stderr)
        raise SystemExit(1) from None
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def _safe_error(error: Exception) -> str:
    if isinstance(error, ValidationError):
        return "governance configuration is invalid"
    if isinstance(error, (GovernanceError, ModelGatewayError, PersistenceError)):
        return str(error)
    return "governance command failed"


__all__ = ["main"]
