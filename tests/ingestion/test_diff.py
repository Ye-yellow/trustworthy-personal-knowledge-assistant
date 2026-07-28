from __future__ import annotations

from trustworthy_kb.ingestion import MarkdownBlockParser, structural_diff


def test_structural_diff_reports_added_removed_modified_and_unique_moves() -> None:
    parser = MarkdownBlockParser()
    before = parser.parse("# A\n\nKeep\n\nModify old\n\nMove me\n\nRemove me\n").blocks
    after = parser.parse("# A\n\nKeep\n\nModify new\n\nAdded\n\n# B\n\nMove me\n").blocks

    result = structural_diff(before, after)

    assert result.counts.added >= 1
    assert result.counts.removed >= 1
    assert result.counts.modified >= 1
    assert result.counts.moved == 1
    assert len(result.diff_hash) == 64
    serialized = str(result.as_json())
    assert "Move me" not in serialized
    assert "Modify old" not in serialized


def test_structural_diff_does_not_guess_moves_for_duplicate_hashes() -> None:
    parser = MarkdownBlockParser()
    before = parser.parse("# A\n\nRepeated\n\nRepeated\n").blocks
    after = parser.parse("# B\n\nRepeated\n\nRepeated\n").blocks

    result = structural_diff(before, after)

    assert result.counts.moved == 0
