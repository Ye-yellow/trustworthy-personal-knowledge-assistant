from trustworthy_kb.answer import (
    AnswerDraft,
    AnswerEvidence,
    AnswerIntegrityError,
    CitationSupportDecision,
    CitationVerificationOutput,
    DraftAnswerClaim,
    render_verified_answer,
    validate_citation_closed_set,
    validate_semantic_support,
)
from trustworthy_kb.domain import (
    ClaimId,
    ClaimStatus,
    CuratedVersionId,
    IndexGenerationId,
    KnowledgeNoteId,
    Sensitivity,
    SourceVersionId,
)


def _evidence(marker: str = "a") -> AnswerEvidence:
    return AnswerEvidence(
        chunk_id=marker * 64,
        text="Synthetic evidence says the supported fact.",
        claim_ids=(ClaimId.generate(),),
        quality_status=ClaimStatus.VERIFIED,
        sensitivity=Sensitivity.PRIVATE,
        note_id=KnowledgeNoteId.generate(),
        curated_version_id=CuratedVersionId.generate(),
        generation_id=IndexGenerationId.generate(),
        vault_path="40-Concepts/Synthetic.md",
        heading_path=("Synthetic", "Evidence"),
        source_version_ids=(SourceVersionId.generate(),),
    )


def test_closed_set_and_semantic_verification_render_deterministic_citations() -> None:
    evidence = _evidence()
    draft = AnswerDraft(
        claims=(
            DraftAnswerClaim(
                statement="The synthetic fact is supported.",
                citation_chunk_ids=(evidence.chunk_id,),
            ),
        ),
        limitations=("Only the supplied synthetic scope was checked.",),
    )
    verification = CitationVerificationOutput(
        decisions=(
            CitationSupportDecision(
                claim_index=0,
                supported=True,
                supporting_chunk_ids=(evidence.chunk_id,),
                reason_code="SUPPORTED",
            ),
        )
    )

    validate_citation_closed_set(draft, (evidence,))
    validate_semantic_support(draft, verification)
    markdown, citations = render_verified_answer(draft, (evidence,))

    assert markdown.startswith("The synthetic fact is supported.[1]")
    assert "[[40-Concepts/Synthetic#Synthetic / Evidence]]" in markdown
    assert citations[0].chunk_id == evidence.chunk_id
    assert str(evidence.source_version_ids[0]) in markdown


def test_closed_set_rejects_invented_citation() -> None:
    evidence = _evidence()
    draft = AnswerDraft(
        claims=(
            DraftAnswerClaim(
                statement="Invented support.",
                citation_chunk_ids=("b" * 64,),
            ),
        )
    )

    try:
        validate_citation_closed_set(draft, (evidence,))
    except AnswerIntegrityError as error:
        assert "outside" in str(error)
    else:
        raise AssertionError("invented citation was accepted")


def test_semantic_verification_requires_supported_subset_for_every_claim() -> None:
    evidence = _evidence()
    draft = AnswerDraft(
        claims=(
            DraftAnswerClaim(
                statement="Unsupported claim.",
                citation_chunk_ids=(evidence.chunk_id,),
            ),
        )
    )
    verification = CitationVerificationOutput(
        decisions=(
            CitationSupportDecision(
                claim_index=0,
                supported=False,
                supporting_chunk_ids=(),
                reason_code="NOT_ENTAILED",
            ),
        )
    )

    try:
        validate_semantic_support(draft, verification)
    except AnswerIntegrityError as error:
        assert "not supported" in str(error)
    else:
        raise AssertionError("unsupported answer claim was accepted")
