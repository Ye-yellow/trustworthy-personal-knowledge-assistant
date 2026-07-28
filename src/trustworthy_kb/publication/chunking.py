"""Deterministic heading-aware chunking for curated Markdown."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from datetime import datetime

from trustworthy_kb.domain import ClaimId, ClaimStatus, IndexGenerationId, Sensitivity
from trustworthy_kb.publication.contracts import CurationArtifact, CurationClaim, KnowledgeChunk
from trustworthy_kb.publication.errors import ChunkingError

_CLAIM_MARKER = re.compile(r"\[\^(claim_[0-9A-HJKMNP-TV-Z]{26})\]")
_QUALITY_ORDER = {
    ClaimStatus.VERIFIED: 0,
    ClaimStatus.USER_ASSERTED: 1,
    ClaimStatus.OPINION: 2,
}
_SENSITIVITY_ORDER = {
    Sensitivity.PUBLIC: 0,
    Sensitivity.RESTRICTED: 1,
    Sensitivity.PRIVATE: 2,
}


class MarkdownChunker:
    """Split only between rendered Claim lines and preserve heading context."""

    def __init__(
        self,
        *,
        version: str = "markdown-v1",
        target_characters: int = 900,
        overlap_characters: int = 120,
        hard_max_characters: int = 1600,
    ) -> None:
        if not version.strip():
            raise ValueError("chunker version must not be empty")
        if not 1 <= overlap_characters < target_characters <= hard_max_characters:
            raise ValueError("chunk sizes must satisfy overlap < target <= hard maximum")
        self.version = version.strip()
        self.target_characters = target_characters
        self.overlap_characters = overlap_characters
        self.hard_max_characters = hard_max_characters

    def chunk(
        self,
        artifact: CurationArtifact,
        claims: Sequence[CurationClaim],
        *,
        generation_id: IndexGenerationId,
        generation_number: int,
        embedding_model: str,
    ) -> tuple[KnowledgeChunk, ...]:
        """Return a stable complete Chunk set for one curated version."""

        if generation_number < 1 or not embedding_model.strip():
            raise ChunkingError("index generation metadata is invalid")
        claim_map = {claim.id: claim for claim in claims}
        if set(claim_map) != set(artifact.claim_ids):
            raise ChunkingError("chunk input claims do not match the curated artifact")
        sections = _claim_sections(artifact.body_markdown)
        chunks: list[KnowledgeChunk] = []
        ordinal = 0
        for heading_path, lines in sections:
            for batch in self._batches(heading_path, lines):
                ids = _claim_ids(batch)
                if not ids or any(claim_id not in claim_map for claim_id in ids):
                    raise ChunkingError("curated Chunk contains an unknown Claim marker")
                selected = tuple(claim_map[claim_id] for claim_id in ids)
                text = _chunk_text(heading_path, batch)
                valid_from, valid_to, freshness = _aggregate_times(selected)
                chunk_id = hashlib.sha256(
                    (
                        f"{artifact.curated_version_id}|{self.version}|{ordinal}|"
                        + _normalize_text(text)
                    ).encode("utf-8")
                ).hexdigest()
                chunks.append(
                    KnowledgeChunk(
                        chunk_id=chunk_id,
                        note_id=artifact.note_id,
                        curated_version_id=artifact.curated_version_id,
                        claim_ids=ids,
                        text=text,
                        heading_path=heading_path,
                        ordinal=ordinal,
                        quality_status=max(
                            (claim.status for claim in selected),
                            key=_QUALITY_ORDER.__getitem__,
                        ),
                        sensitivity=max(
                            (claim.sensitivity for claim in selected),
                            key=_SENSITIVITY_ORDER.__getitem__,
                        ),
                        valid_from=valid_from,
                        valid_to=valid_to,
                        freshness_at=freshness,
                        generation_id=generation_id,
                        generation_number=generation_number,
                        embedding_model=embedding_model,
                        chunker_version=self.version,
                        content_hash=artifact.content_hash,
                    )
                )
                ordinal += 1
        observed = {claim_id for chunk in chunks for claim_id in chunk.claim_ids}
        if observed != set(artifact.claim_ids):
            raise ChunkingError("Chunk set does not cover every curated Claim")
        return tuple(chunks)

    def _batches(
        self, heading_path: tuple[str, ...], lines: tuple[str, ...]
    ) -> tuple[tuple[str, ...], ...]:
        heading_text = "\n".join(
            f"{'#' * (index + 1)} {item}" for index, item in enumerate(heading_path)
        )
        prefix_length = len(heading_text) + 2
        batches: list[tuple[str, ...]] = []
        current: list[str] = []
        for line in lines:
            if prefix_length + len(line) > self.hard_max_characters:
                raise ChunkingError("an atomic Claim line exceeds the Chunk hard limit")
            projected = prefix_length + sum(len(item) + 1 for item in (*current, line))
            if current and projected > self.target_characters:
                batches.append(tuple(current))
                current = list(_overlap_tail(current, self.overlap_characters))
            current.append(line)
            if prefix_length + sum(len(item) + 1 for item in current) > self.hard_max_characters:
                if len(current) == 1:
                    raise ChunkingError("an atomic Claim line exceeds the Chunk hard limit")
                last = current.pop()
                batches.append(tuple(current))
                current = [last]
        if current:
            batches.append(tuple(current))
        return tuple(batches)


def _claim_sections(markdown: str) -> tuple[tuple[tuple[str, ...], tuple[str, ...]], ...]:
    title = "Curated knowledge"
    heading = "Knowledge"
    sections: list[tuple[tuple[str, ...], tuple[str, ...]]] = []
    claim_lines: list[str] = []

    def flush() -> None:
        if claim_lines:
            sections.append(((title, heading), tuple(claim_lines)))
            claim_lines.clear()

    for raw_line in markdown.splitlines():
        line = raw_line.strip()
        if line.startswith("# "):
            title = line[2:].strip()
        elif line.startswith("## "):
            flush()
            heading = line[3:].strip()
        elif line.startswith("- "):
            if not _CLAIM_MARKER.search(line):
                raise ChunkingError("curated Claim line is missing its marker")
            claim_lines.append(line)
    flush()
    if not sections:
        raise ChunkingError("curated artifact contains no Claim lines")
    return tuple(sections)


def _claim_ids(lines: Sequence[str]) -> tuple[ClaimId, ...]:
    ordered: list[ClaimId] = []
    for line in lines:
        ordered.extend(ClaimId(value) for value in _CLAIM_MARKER.findall(line))
    return tuple(dict.fromkeys(ordered))


def _chunk_text(heading_path: tuple[str, ...], lines: Sequence[str]) -> str:
    headings = "\n".join(
        f"{'#' * (index + 1)} {heading}" for index, heading in enumerate(heading_path)
    )
    return f"{headings}\n\n" + "\n".join(lines)


def _overlap_tail(lines: Sequence[str], budget: int) -> tuple[str, ...]:
    selected: list[str] = []
    used = 0
    for line in reversed(lines):
        cost = len(line) + 1
        if selected and used + cost > budget:
            break
        if cost > budget:
            break
        selected.append(line)
        used += cost
    return tuple(reversed(selected))


def _aggregate_times(
    claims: Sequence[CurationClaim],
) -> tuple[datetime | None, datetime | None, datetime | None]:
    starts = [claim.valid_from for claim in claims if claim.valid_from is not None]
    ends = [claim.valid_to for claim in claims if claim.valid_to is not None]
    freshness = [claim.freshness_at for claim in claims if claim.freshness_at is not None]
    valid_from = max(starts) if starts else None
    valid_to = min(ends) if ends else None
    if valid_from is not None and valid_to is not None and valid_from > valid_to:
        raise ChunkingError("Chunk Claim validity windows do not overlap")
    return valid_from, valid_to, min(freshness) if freshness else None


def _normalize_text(value: str) -> str:
    return "\n".join(line.rstrip() for line in value.replace("\r\n", "\n").split("\n")).strip()


__all__ = ["MarkdownChunker"]
