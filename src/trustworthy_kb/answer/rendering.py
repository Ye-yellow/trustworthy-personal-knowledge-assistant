"""Deterministic answer and Obsidian citation rendering."""

from __future__ import annotations

from collections.abc import Sequence

from trustworthy_kb.answer.contracts import AnswerCitation, AnswerDraft, AnswerEvidence
from trustworthy_kb.answer.errors import AnswerIntegrityError


def render_verified_answer(
    draft: AnswerDraft,
    evidence: Sequence[AnswerEvidence],
) -> tuple[str, tuple[AnswerCitation, ...]]:
    """Render only citations already validated against the retrieved closed set."""

    by_chunk = {item.chunk_id: item for item in evidence}
    ordered_ids: list[str] = []
    for claim in draft.claims:
        for chunk_id in claim.citation_chunk_ids:
            if chunk_id not in by_chunk:
                raise AnswerIntegrityError("answer rendering received an unknown citation")
            if chunk_id not in ordered_ids:
                ordered_ids.append(chunk_id)
    citations = tuple(
        _citation(number, by_chunk[chunk_id])
        for number, chunk_id in enumerate(ordered_ids, start=1)
    )
    number_by_id = {item.chunk_id: item.number for item in citations}
    lines = [
        f"{claim.statement}"
        + "".join(f"[{number_by_id[chunk_id]}]" for chunk_id in claim.citation_chunk_ids)
        for claim in draft.claims
    ]
    if draft.limitations:
        lines.extend(("", "限制:", *(f"- {item}" for item in draft.limitations)))
    lines.extend(("", "引用:"))
    for citation in citations:
        sources = ", ".join(str(item) for item in citation.source_version_ids)
        lines.append(
            f"[{citation.number}] {citation.wikilink} "
            f"(curated={citation.curated_version_id}; sources={sources}; "
            f"quality={citation.quality_status.value})"
        )
    return "\n".join(lines).rstrip() + "\n", citations


def _citation(number: int, evidence: AnswerEvidence) -> AnswerCitation:
    heading = " / ".join(_escape_wikilink(item) for item in evidence.heading_path)
    target = _escape_wikilink(evidence.vault_path.removesuffix(".md"))
    wikilink = f"[[{target}{'#' + heading if heading else ''}]]"
    return AnswerCitation(
        number=number,
        chunk_id=evidence.chunk_id,
        note_id=evidence.note_id,
        curated_version_id=evidence.curated_version_id,
        source_version_ids=evidence.source_version_ids,
        quality_status=evidence.quality_status,
        vault_path=evidence.vault_path,
        heading_path=evidence.heading_path,
        wikilink=wikilink,
    )


def _escape_wikilink(value: str) -> str:
    return value.replace("|", "\\|").replace("]", "\\]").replace("#", "\\#")


__all__ = ["render_verified_answer"]
