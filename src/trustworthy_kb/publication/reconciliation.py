"""Fail-closed reconciliation between the control plane, Vault, and vector index."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

from trustworthy_kb.publication.contracts import (
    ExpectedPublication,
    IndexProbe,
    ReconciliationFinding,
    ReconciliationReport,
    ReconciliationSeverity,
)
from trustworthy_kb.publication.indexing import GenerationIndexer
from trustworthy_kb.publication.ports import VaultVerificationGateway, VectorIndexGateway


class PublicationReconciler:
    """Verify active publications and repair only deterministic index drift."""

    def __init__(
        self,
        *,
        vault: VaultVerificationGateway,
        index: VectorIndexGateway,
        indexer: GenerationIndexer,
        repair_index: bool = True,
    ) -> None:
        self._vault = vault
        self._index = index
        self._indexer = indexer
        self._repair_index = repair_index

    async def reconcile(self, expected: Sequence[ExpectedPublication]) -> ReconciliationReport:
        findings: list[ReconciliationFinding] = []
        for publication in expected:
            vault_finding = await self._verify_vault(publication)
            if vault_finding is not None:
                findings.append(vault_finding)
                continue
            findings.append(await self._verify_index(publication))
        return ReconciliationReport(
            findings=tuple(findings),
            checked_at=datetime.now(UTC),
        )

    async def _verify_vault(self, publication: ExpectedPublication) -> ReconciliationFinding | None:
        try:
            metadata = await self._vault.verify(
                publication.final_relative_path,
                expected_hash=publication.artifact.content_hash,
            )
            if metadata.get("curated_version_id") != str(publication.artifact.curated_version_id):
                raise ValueError("Vault version identity changed")
        except Exception:
            return self._finding(
                publication,
                code="vault_verification_failed",
                severity=ReconciliationSeverity.BLOCKED,
            )
        return None

    async def _verify_index(self, publication: ExpectedPublication) -> ReconciliationFinding:
        expected = {
            (chunk.chunk_id, chunk.curated_version_id, chunk.content_hash)
            for chunk in publication.chunks
        }
        try:
            probes = await self._index.list_probes_for_version(
                publication.generation_number,
                publication.artifact.curated_version_id,
            )
        except Exception:
            return self._finding(
                publication,
                code="index_verification_failed",
                severity=ReconciliationSeverity.BLOCKED,
            )
        if _probe_identities(probes) == expected:
            return self._finding(
                publication,
                code="publication_healthy",
                severity=ReconciliationSeverity.HEALTHY,
            )
        if not self._repair_index:
            return self._finding(
                publication,
                code="index_drift",
                severity=ReconciliationSeverity.REPAIRABLE,
            )
        try:
            expected_ids = {chunk.chunk_id for chunk in publication.chunks}
            unexpected_ids = [
                probe.chunk_id for probe in probes if probe.chunk_id not in expected_ids
            ]
            if unexpected_ids:
                await self._index.delete_chunks(
                    publication.generation_number,
                    unexpected_ids,
                )
            await self._indexer.index(publication.chunks)
            repaired = await self._index.list_probes_for_version(
                publication.generation_number,
                publication.artifact.curated_version_id,
            )
            if _probe_identities(repaired) != expected:
                raise RuntimeError("index repair did not converge")
        except Exception:
            return self._finding(
                publication,
                code="index_repair_failed",
                severity=ReconciliationSeverity.BLOCKED,
            )
        return self._finding(
            publication,
            code="index_repaired",
            severity=ReconciliationSeverity.REPAIRABLE,
            repaired=True,
        )

    @staticmethod
    def _finding(
        publication: ExpectedPublication,
        *,
        code: str,
        severity: ReconciliationSeverity,
        repaired: bool = False,
    ) -> ReconciliationFinding:
        return ReconciliationFinding(
            code=code,
            severity=severity,
            note_id=publication.artifact.note_id,
            curated_version_id=publication.artifact.curated_version_id,
            relative_path=publication.final_relative_path,
            repaired=repaired,
        )


def _probe_identities(probes: Sequence[IndexProbe]) -> set[tuple[str, object, str]]:
    return {(probe.chunk_id, probe.curated_version_id, probe.content_hash) for probe in probes}


__all__ = ["PublicationReconciler"]
