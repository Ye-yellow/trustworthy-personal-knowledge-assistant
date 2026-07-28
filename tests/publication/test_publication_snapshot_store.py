from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from trustworthy_kb.domain import (
    ClaimId,
    ClaimStatus,
    ClaimType,
    CuratedVersionId,
    KnowledgeChangeId,
    KnowledgeNoteId,
    Sensitivity,
    SourceId,
    SourceVersionId,
)
from trustworthy_kb.publication.contracts import CurationClaim, CurationGroup, CurationPlan
from trustworthy_kb.publication.curation import CuratedMarkdownRenderer
from trustworthy_kb.publication.errors import PublicationError
from trustworthy_kb.publication.snapshot_store import PublicationSnapshotStore


async def test_publication_snapshot_store_round_trips_and_rejects_tampering(
    tmp_path: Path,
) -> None:
    store = PublicationSnapshotStore(tmp_path / "snapshots")
    claim = CurationClaim(
        id=ClaimId.generate(),
        claim_type=ClaimType.FACT,
        subject="Snapshot",
        predicate="is",
        object_json={"value": "safe"},
        status=ClaimStatus.VERIFIED,
        sensitivity=Sensitivity.PRIVATE,
    )
    artifact = CuratedMarkdownRenderer().render(
        note_id=KnowledgeNoteId.generate(),
        curated_version_id=CuratedVersionId.generate(),
        based_on_change_id=KnowledgeChangeId.generate(),
        version_number=1,
        plan=CurationPlan(
            title="Snapshot",
            groups=(CurationGroup(heading="Facts", claim_ids=(claim.id,)),),
        ),
        claims=(claim,),
        source_ids=(SourceId.generate(),),
        source_version_ids=(SourceVersionId.generate(),),
        model_name="test/model",
        prompt_version="v1",
        quality_policy_version="v1",
        created_at=datetime.now(UTC),
    )
    await store.put(artifact, (claim,))
    await store.put(artifact, (claim,))

    snapshot = await store.get(artifact.content_hash)
    assert snapshot.artifact == artifact
    assert snapshot.claims == (claim,)
    target = next((tmp_path / "snapshots").rglob("*.json"))
    target.write_text("{}", encoding="utf-8")
    with pytest.raises(PublicationError, match="integrity"):
        await store.get(artifact.content_hash)
