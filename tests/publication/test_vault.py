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
from trustworthy_kb.publication.errors import VaultPublicationError
from trustworthy_kb.publication.vault import AtomicVaultPublisher


def _artifact(note_id: KnowledgeNoteId, version: CuratedVersionId, value: str):
    claim = CurationClaim(
        id=ClaimId.generate(),
        claim_type=ClaimType.FACT,
        subject="System",
        predicate="value",
        object_json={"value": value},
        status=ClaimStatus.VERIFIED,
        sensitivity=Sensitivity.PRIVATE,
    )
    return CuratedMarkdownRenderer().render(
        note_id=note_id,
        curated_version_id=version,
        based_on_change_id=KnowledgeChangeId.generate(),
        version_number=1,
        plan=CurationPlan(
            title="System",
            groups=(CurationGroup(heading="Facts", claim_ids=(claim.id,)),),
        ),
        claims=(claim,),
        source_ids=(SourceId.generate(),),
        source_version_ids=(SourceVersionId.generate(),),
        model_name="gpt-5.5",
        prompt_version="v1",
        quality_policy_version="v1",
        created_at=datetime.now(UTC),
    )


async def test_vault_stage_publish_and_replace_are_verified(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    publisher = AtomicVaultPublisher(vault)
    note_id = KnowledgeNoteId.generate()
    first = _artifact(note_id, CuratedVersionId.generate(), "one")

    stage_path = await publisher.stage(first)
    assert await publisher.stage(first) == stage_path
    final_path = await publisher.publish(first, "40-Concepts/System.md")
    metadata = await publisher.verify(final_path, expected_hash=first.content_hash)
    assert metadata["curated_version_id"] == str(first.curated_version_id)

    second = _artifact(note_id, CuratedVersionId.generate(), "two")
    await publisher.stage(second)
    await publisher.publish(
        second,
        final_path,
        expected_current_version_id=first.curated_version_id,
        expected_current_hash=first.content_hash,
    )
    assert (vault / "_AI" / "Versions" / str(note_id) / f"{first.curated_version_id}.md").is_file()
    assert (await publisher.verify(final_path, expected_hash=second.content_hash))[
        "curated_version_id"
    ] == str(second.curated_version_id)


async def test_vault_recycle_and_restore_are_verified_and_idempotent(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    publisher = AtomicVaultPublisher(vault)
    note_id = KnowledgeNoteId.generate()
    artifact = _artifact(note_id, CuratedVersionId.generate(), "recoverable")
    await publisher.stage(artifact)
    final_path = await publisher.publish(artifact, "40-Concepts/Recoverable.md")

    recycled = await publisher.recycle(
        final_path,
        note_id=note_id,
        version_id=artifact.curated_version_id,
        expected_hash=artifact.content_hash,
    )
    assert (
        await publisher.recycle(
            final_path,
            note_id=note_id,
            version_id=artifact.curated_version_id,
            expected_hash=artifact.content_hash,
        )
        == recycled
    )
    assert not (vault / final_path).exists()
    assert (vault / recycled).is_file()

    assert (
        await publisher.restore_recycled(
            final_path,
            note_id=note_id,
            version_id=artifact.curated_version_id,
            expected_hash=artifact.content_hash,
        )
        == final_path
    )
    assert (
        await publisher.restore_recycled(
            final_path,
            note_id=note_id,
            version_id=artifact.curated_version_id,
            expected_hash=artifact.content_hash,
        )
        == final_path
    )
    assert (vault / final_path).is_file()
    assert not (vault / recycled).exists()


async def test_vault_refuses_path_escape_and_manual_conflict(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    publisher = AtomicVaultPublisher(vault)
    note_id = KnowledgeNoteId.generate()
    first = _artifact(note_id, CuratedVersionId.generate(), "one")
    await publisher.stage(first)
    await publisher.publish(first, "40-Concepts/System.md")

    second = _artifact(note_id, CuratedVersionId.generate(), "two")
    await publisher.stage(second)
    target = vault / "40-Concepts" / "System.md"
    target.write_text(target.read_text(encoding="utf-8") + "manual change\n", encoding="utf-8")
    with pytest.raises(VaultPublicationError, match="verification"):
        await publisher.publish(
            second,
            "40-Concepts/System.md",
            expected_current_version_id=first.curated_version_id,
            expected_current_hash=first.content_hash,
        )
    with pytest.raises(VaultPublicationError, match="relative"):
        await publisher.publish(second, "../outside.md")


@pytest.mark.skipif(not hasattr(Path, "symlink_to"), reason="symlinks unavailable")
async def test_vault_refuses_symlink_parent(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    outside = tmp_path / "outside"
    vault.mkdir()
    outside.mkdir()
    try:
        (vault / "40-Concepts").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation is not permitted")
    publisher = AtomicVaultPublisher(vault)
    artifact = _artifact(KnowledgeNoteId.generate(), CuratedVersionId.generate(), "value")
    await publisher.stage(artifact)
    with pytest.raises(VaultPublicationError, match=r"unsafe|symlink"):
        await publisher.publish(artifact, "40-Concepts/System.md")
