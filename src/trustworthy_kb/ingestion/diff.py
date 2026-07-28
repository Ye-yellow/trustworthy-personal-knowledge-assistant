"""Text-free structural diff for parsed Markdown blocks."""

from __future__ import annotations

from collections import Counter
from typing import Any

from pydantic import Field

from trustworthy_kb.ingestion.hashing import canonical_json_hash
from trustworthy_kb.ingestion.types import IngestionValue, ParsedBlock


class BlockRef(IngestionValue):
    anchor: str
    text_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class ModifiedBlock(IngestionValue):
    anchor: str
    before_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    after_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class MovedBlock(IngestionValue):
    text_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    from_anchor: str
    to_anchor: str


class DiffCounts(IngestionValue):
    added: int = Field(ge=0)
    removed: int = Field(ge=0)
    modified: int = Field(ge=0)
    moved: int = Field(ge=0)


class StructuralDiff(IngestionValue):
    added: tuple[BlockRef, ...]
    removed: tuple[BlockRef, ...]
    modified: tuple[ModifiedBlock, ...]
    moved: tuple[MovedBlock, ...]
    counts: DiffCounts

    def as_json(self) -> dict[str, Any]:
        """Return a canonicalizable, text-free persistence value."""

        return self.model_dump(mode="json")

    @property
    def diff_hash(self) -> str:
        """Hash the canonical structural diff."""

        return canonical_json_hash(self.as_json())


def structural_diff(
    before: tuple[ParsedBlock, ...],
    after: tuple[ParsedBlock, ...],
) -> StructuralDiff:
    """Compare anchors first, then recognize only unique hash moves."""

    old_by_anchor = {block.anchor: block for block in before}
    new_by_anchor = {block.anchor: block for block in after}
    unchanged_anchors = {
        anchor
        for anchor in old_by_anchor.keys() & new_by_anchor.keys()
        if old_by_anchor[anchor].text_hash == new_by_anchor[anchor].text_hash
    }
    unmatched_old = [
        block for anchor, block in old_by_anchor.items() if anchor not in unchanged_anchors
    ]
    unmatched_new = [
        block for anchor, block in new_by_anchor.items() if anchor not in unchanged_anchors
    ]
    old_hash_counts = Counter(block.text_hash for block in unmatched_old)
    new_hash_counts = Counter(block.text_hash for block in unmatched_new)
    movable_hashes = {
        text_hash
        for text_hash, count in old_hash_counts.items()
        if count == 1 and new_hash_counts[text_hash] == 1
    }
    old_move = {
        block.text_hash: block for block in unmatched_old if block.text_hash in movable_hashes
    }
    new_move = {
        block.text_hash: block for block in unmatched_new if block.text_hash in movable_hashes
    }
    moved = tuple(
        MovedBlock(
            text_hash=text_hash,
            from_anchor=old_move[text_hash].anchor,
            to_anchor=new_move[text_hash].anchor,
        )
        for text_hash in sorted(movable_hashes)
        if old_move[text_hash].anchor != new_move[text_hash].anchor
    )
    moved_hashes = {block.text_hash for block in moved}
    remaining_old = [block for block in unmatched_old if block.text_hash not in moved_hashes]
    remaining_new = [block for block in unmatched_new if block.text_hash not in moved_hashes]
    remaining_old_by_anchor = {block.anchor: block for block in remaining_old}
    remaining_new_by_anchor = {block.anchor: block for block in remaining_new}
    modified_anchors = remaining_old_by_anchor.keys() & remaining_new_by_anchor.keys()
    modified = tuple(
        ModifiedBlock(
            anchor=anchor,
            before_hash=remaining_old_by_anchor[anchor].text_hash,
            after_hash=remaining_new_by_anchor[anchor].text_hash,
        )
        for anchor in sorted(modified_anchors)
    )
    removed = tuple(
        sorted(
            (
                BlockRef(anchor=block.anchor, text_hash=block.text_hash)
                for block in remaining_old
                if block.anchor not in modified_anchors
            ),
            key=lambda block: (block.anchor, block.text_hash),
        )
    )
    added = tuple(
        sorted(
            (
                BlockRef(anchor=block.anchor, text_hash=block.text_hash)
                for block in remaining_new
                if block.anchor not in modified_anchors
            ),
            key=lambda block: (block.anchor, block.text_hash),
        )
    )
    return StructuralDiff(
        added=added,
        removed=removed,
        modified=modified,
        moved=moved,
        counts=DiffCounts(
            added=len(added),
            removed=len(removed),
            modified=len(modified),
            moved=len(moved),
        ),
    )


__all__ = [
    "BlockRef",
    "DiffCounts",
    "ModifiedBlock",
    "MovedBlock",
    "StructuralDiff",
    "structural_diff",
]
