"""Persist independently fetched web evidence into the source-lineage model."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from urllib.parse import urlsplit

from trustworthy_kb.domain import (
    ContentBlockId,
    ContentBlockRecord,
    EvidenceFamilyId,
    EvidenceFamilyRecord,
    Sensitivity,
    SourceId,
    SourceRecord,
    SourceType,
    SourceVersionId,
    SourceVersionRecord,
    SourceVersionStatus,
    TrustTier,
)
from trustworthy_kb.governance.contracts import FetchedEvidenceDocument
from trustworthy_kb.persistence import SqliteUnitOfWork
from trustworthy_kb.persistence.base import utc_now


@dataclass(frozen=True, slots=True)
class PersistedEvidenceSource:
    version: SourceVersionRecord
    family: EvidenceFamilyRecord
    trust_tier: TrustTier


class DomainTrustResolver:
    """Resolve configured host allowlists to frozen trust tiers; unknown hosts are T4."""

    def __init__(
        self,
        *,
        t1_domains: tuple[str, ...] = (),
        t2_domains: tuple[str, ...] = (),
    ) -> None:
        self._t1 = frozenset(t1_domains)
        self._t2 = frozenset(t2_domains)

    def resolve(self, url: str) -> TrustTier:
        host = (urlsplit(url).hostname or "").lower().rstrip(".")
        if _matches_domain(host, self._t1):
            return TrustTier.T1
        if _matches_domain(host, self._t2):
            return TrustTier.T2
        return TrustTier.T4


class EvidenceSourceService:
    """Store web sources, versions, blocks, and independent-family identities atomically."""

    def __init__(
        self, trust_resolver: DomainTrustResolver, *, owner: str = "system:evidence"
    ) -> None:
        self._trust_resolver = trust_resolver
        self._owner = owner

    async def persist(
        self,
        unit_of_work: SqliteUnitOfWork,
        document: FetchedEvidenceDocument,
    ) -> PersistedEvidenceSource:
        canonical_uri = str(document.final_url)
        trust_tier = self._trust_resolver.resolve(canonical_uri)
        source_type = (
            SourceType.OFFICIAL_DOCUMENTATION if trust_tier is TrustTier.T1 else SourceType.WEB_PAGE
        )
        source = await unit_of_work.sources.find_source_by_identity(
            source_type, canonical_uri, self._owner
        )
        timestamp = utc_now()
        if source is None:
            source = await unit_of_work.sources.add_source(
                SourceRecord(
                    id=SourceId.generate(),
                    source_type=source_type,
                    canonical_uri=canonical_uri,
                    owner=self._owner,
                    trust_tier=trust_tier,
                    sensitivity=Sensitivity.PUBLIC,
                    revision=1,
                    created_at=timestamp,
                    updated_at=timestamp,
                )
            )

        version = await unit_of_work.sources.find_source_version_by_hash(
            source.id, document.raw_content_hash
        )
        if version is None:
            latest = await unit_of_work.sources.get_latest_source_version(source.id)
            version = await unit_of_work.sources.append_source_version(
                SourceVersionRecord(
                    id=SourceVersionId.generate(),
                    source_id=source.id,
                    version_number=1 if latest is None else latest.version_number + 1,
                    content_hash=document.raw_content_hash,
                    byte_size=document.byte_size,
                    media_type=document.media_type,
                    captured_at=document.captured_at,
                    original_path=canonical_uri,
                    status=SourceVersionStatus.CAPTURED,
                    revision=1,
                    created_at=timestamp,
                    updated_at=timestamp,
                )
            )
            await unit_of_work.sources.add_content_blocks(
                tuple(
                    ContentBlockRecord(
                        id=ContentBlockId.generate(),
                        source_version_id=version.id,
                        ordinal=ordinal,
                        block_type="evidence_excerpt",
                        anchor=block.anchor,
                        text_hash=block.text_hash,
                        character_count=len(block.text),
                        created_at=timestamp,
                    )
                    for ordinal, block in enumerate(document.blocks)
                )
            )
            parsed = await unit_of_work.sources.transition_source_version(
                version.id, SourceVersionStatus.PARSED, expected_revision=version.revision
            )
            version = await unit_of_work.sources.transition_source_version(
                version.id, SourceVersionStatus.READY, expected_revision=parsed.revision
            )
            source = await unit_of_work.sources.activate_source_version(
                source.id, version.id, expected_revision=source.revision
            )

        family_origin = _family_origin(canonical_uri)
        family_fingerprint = hashlib.sha256(family_origin.encode()).hexdigest()
        family = await unit_of_work.knowledge.find_evidence_family_by_fingerprint(
            family_fingerprint
        )
        if family is None:
            family = await unit_of_work.knowledge.add_evidence_family(
                EvidenceFamilyRecord(
                    id=EvidenceFamilyId.generate(),
                    canonical_origin=family_origin,
                    origin_fingerprint=family_fingerprint,
                    created_at=timestamp,
                )
            )
        return PersistedEvidenceSource(version=version, family=family, trust_tier=trust_tier)


def _matches_domain(host: str, configured: frozenset[str]) -> bool:
    return any(host == domain or host.endswith(f".{domain}") for domain in configured)


def _family_origin(url: str) -> str:
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower().rstrip(".")
    port = "" if parsed.port in {None, 443} else f":{parsed.port}"
    return f"https://{host}{port}"


__all__ = ["DomainTrustResolver", "EvidenceSourceService", "PersistedEvidenceSource"]
