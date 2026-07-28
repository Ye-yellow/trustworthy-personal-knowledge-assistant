from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from trustworthy_kb.domain import (
    ContentBlockId,
    ContentBlockRecord,
    Sensitivity,
    SourceId,
    SourceVersionId,
    SourceVersionRecord,
    SourceVersionStatus,
)
from trustworthy_kb.governance import (
    ClaimDraft,
    ClaimExtractionOutput,
    ClaimExtractor,
    ClaimObject,
    ClaimOriginSpan,
    ClaimScope,
    SnapshotContentResolver,
)
from trustworthy_kb.governance.errors import EvidencePackIntegrityError, GovernanceError
from trustworthy_kb.ingestion import ContentAddressedSnapshotStore, MarkdownBlockParser
from trustworthy_kb.llm import ModelPurpose


class FakeGateway:
    def __init__(self, output: ClaimExtractionOutput) -> None:
        self.output = output
        self.calls: list[dict[str, Any]] = []

    async def invoke_structured(self, messages: object, **kwargs: Any) -> ClaimExtractionOutput:
        self.calls.append({"messages": messages, **kwargs})
        return self.output


def _draft(anchor: str, *, end: int = 5) -> ClaimDraft:
    from trustworthy_kb.domain import ClaimType

    return ClaimDraft(
        claim_type=ClaimType.FACT,
        subject="Python",
        predicate="is",
        object=ClaimObject(value="a language", value_type="text"),
        scope=ClaimScope(domain="software"),
        sensitivity=Sensitivity.PUBLIC,
        origins=(ClaimOriginSpan(block_anchor=anchor, start=0, end=end),),
    )


async def _resolved_fixture(
    tmp_path: Path,
) -> tuple[SnapshotContentResolver, SourceVersionRecord, tuple[ContentBlockRecord, ...]]:
    raw = b"# Title\n\nPython is a language.\n"
    digest = hashlib.sha256(raw).hexdigest()
    parsed = MarkdownBlockParser().parse(raw.decode())
    timestamp = datetime.now(UTC)
    version = SourceVersionRecord(
        id=SourceVersionId.generate(),
        source_id=SourceId.generate(),
        version_number=1,
        content_hash=digest,
        byte_size=len(raw),
        media_type="text/markdown",
        captured_at=timestamp,
        original_path="synthetic.md",
        status=SourceVersionStatus.READY,
        revision=1,
        created_at=timestamp,
        updated_at=timestamp,
    )
    records = tuple(
        ContentBlockRecord(
            id=ContentBlockId.generate(),
            source_version_id=version.id,
            ordinal=block.ordinal,
            block_type=block.block_type,
            anchor=block.anchor,
            text_hash=block.text_hash,
            character_count=block.character_count,
            created_at=timestamp,
        )
        for block in parsed.blocks
    )
    await ContentAddressedSnapshotStore(tmp_path).put(raw, digest)
    return SnapshotContentResolver(tmp_path), version, records


@pytest.mark.asyncio
async def test_snapshot_resolution_and_claim_extraction_are_anchor_locked(tmp_path: Path) -> None:
    resolver, version, records = await _resolved_fixture(tmp_path)
    content = await resolver.resolve(version, records)
    gateway = FakeGateway(ClaimExtractionOutput(claims=(_draft(records[1].anchor),)))
    extractor = ClaimExtractor(
        gateway,  # type: ignore[arg-type]
        prompt_version="extract-v1",
        max_claims=10,
        max_characters=1000,
    )

    claims = await extractor.extract(content)

    assert claims == gateway.output.claims
    assert gateway.calls[0]["purpose"] is ModelPurpose.CLAIM_EXTRACTION
    assert gateway.calls[0]["metadata"]["input_hash"] == version.content_hash
    assert "Python is a language" in str(gateway.calls[0]["messages"])


@pytest.mark.asyncio
async def test_snapshot_resolution_rejects_metadata_or_content_tampering(tmp_path: Path) -> None:
    resolver, version, records = await _resolved_fixture(tmp_path)
    mismatched = records[0].model_copy(update={"text_hash": "f" * 64})
    with pytest.raises(EvidencePackIntegrityError, match="does not reconcile"):
        await resolver.resolve(version, (mismatched, *records[1:]))

    target = tmp_path / "sha256" / version.content_hash[:2] / f"{version.content_hash[2:]}.md"
    target.write_bytes(b"tampered")
    with pytest.raises(EvidencePackIntegrityError, match="integrity"):
        await resolver.resolve(version, records)


@pytest.mark.asyncio
async def test_extractor_rejects_unknown_anchor_and_configured_limits(tmp_path: Path) -> None:
    resolver, version, records = await _resolved_fixture(tmp_path)
    content = await resolver.resolve(version, records)
    invalid_gateway = FakeGateway(ClaimExtractionOutput(claims=(_draft("missing"),)))
    extractor = ClaimExtractor(
        invalid_gateway,  # type: ignore[arg-type]
        prompt_version="extract-v1",
        max_claims=1,
        max_characters=1000,
    )
    with pytest.raises(GovernanceError, match="origin span"):
        await extractor.extract(content)

    limited = ClaimExtractor(
        invalid_gateway,  # type: ignore[arg-type]
        prompt_version="extract-v1",
        max_claims=1,
        max_characters=5,
    )
    with pytest.raises(GovernanceError, match="extraction limit"):
        await limited.extract(content)
