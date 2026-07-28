from trustworthy_kb.answer import (
    AnswerCitation,
    AnsweredResult,
    AnswerIntegrityError,
    AnswerSnapshotStore,
    DraftAnswerClaim,
)
from trustworthy_kb.domain import (
    AnswerRunId,
    ClaimStatus,
    CuratedVersionId,
    IndexGenerationId,
    KnowledgeNoteId,
    SourceVersionId,
)


async def test_answer_snapshot_store_is_content_addressed_and_detects_tampering(tmp_path) -> None:
    chunk_id = "a" * 64
    result = AnsweredResult(
        run_id=AnswerRunId.generate(),
        answer_markdown="Synthetic answer.[1]\n",
        claims=(
            DraftAnswerClaim(
                statement="Synthetic answer.",
                citation_chunk_ids=(chunk_id,),
            ),
        ),
        citations=(
            AnswerCitation(
                number=1,
                chunk_id=chunk_id,
                note_id=KnowledgeNoteId.generate(),
                curated_version_id=CuratedVersionId.generate(),
                source_version_ids=(SourceVersionId.generate(),),
                quality_status=ClaimStatus.VERIFIED,
                vault_path="40-Concepts/Synthetic.md",
                heading_path=("Synthetic",),
                wikilink="[[40-Concepts/Synthetic#Synthetic]]",
            ),
        ),
        generation_id=IndexGenerationId.generate(),
    )
    store = AnswerSnapshotStore(tmp_path)

    digest = await store.put(result)
    assert await store.put(result) == digest
    assert await store.get(digest) == result

    target = tmp_path / "sha256" / digest[:2] / f"{digest}.json"
    target.write_text("{}", encoding="utf-8")
    try:
        await store.get(digest)
    except AnswerIntegrityError as error:
        assert "validation" in str(error)
    else:
        raise AssertionError("tampered answer snapshot was accepted")


async def test_answer_snapshot_store_purges_only_invalidated_citations(tmp_path) -> None:
    first_chunk = "a" * 64
    second_chunk = "b" * 64

    def result(chunk_id: str) -> AnsweredResult:
        return AnsweredResult(
            run_id=AnswerRunId.generate(),
            answer_markdown="Synthetic answer.[1]\n",
            claims=(
                DraftAnswerClaim(
                    statement="Synthetic answer.",
                    citation_chunk_ids=(chunk_id,),
                ),
            ),
            citations=(
                AnswerCitation(
                    number=1,
                    chunk_id=chunk_id,
                    note_id=KnowledgeNoteId.generate(),
                    curated_version_id=CuratedVersionId.generate(),
                    source_version_ids=(SourceVersionId.generate(),),
                    quality_status=ClaimStatus.VERIFIED,
                    vault_path="40-Concepts/Synthetic.md",
                    heading_path=("Synthetic",),
                    wikilink="[[40-Concepts/Synthetic#Synthetic]]",
                ),
            ),
            generation_id=IndexGenerationId.generate(),
        )

    store = AnswerSnapshotStore(tmp_path)
    first_hash = await store.put(result(first_chunk))
    second_result = result(second_chunk)
    second_hash = await store.put(second_result)

    assert await store.purge_by_chunk_ids(frozenset({first_chunk})) == 1
    assert await store.purge_by_chunk_ids(frozenset({first_chunk})) == 0
    assert await store.get(second_hash) == second_result
    assert not (tmp_path / "sha256" / first_hash[:2] / f"{first_hash}.json").exists()
