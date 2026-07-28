from __future__ import annotations

import pytest

from trustworthy_kb.ingestion import MarkdownBlockParser, MarkdownParseError, normalize_block_text


def test_parser_builds_stable_structural_blocks_and_unicode_anchors() -> None:
    document = MarkdownBlockParser().parse(
        """---
title: Synthetic
---
# 项目 Overview

First paragraph.

- one
- two

## Details

| A | B |
| - | - |
| 1 | 2 |

```python
print("synthetic")
```
"""
    )

    assert [block.block_type for block in document.blocks] == [
        "frontmatter",
        "heading",
        "paragraph",
        "list",
        "heading",
        "table",
        "code_fence",
    ]
    assert document.blocks[1].anchor == "h1:项目 overview"
    assert document.blocks[4].anchor == "h1:项目 overview/h2:details"
    assert all(len(block.text_hash) == 64 for block in document.blocks)


def test_parser_disambiguates_headings_and_prefers_explicit_block_ids() -> None:
    document = MarkdownBlockParser().parse(
        """# Same

First ^stable-id

# Same

Second ^stable-id
"""
    )

    assert [block.anchor for block in document.blocks] == [
        "h1:same",
        "h1:same/^stable-id",
        "h1:same~2",
        "h1:same~2/^stable-id",
    ]


@pytest.mark.parametrize(
    "text",
    [
        "---\ntitle: [broken\n---\nbody",
        "---\n- not-a-mapping\n---\nbody",
        "```python\nprint('not closed')",
    ],
)
def test_parser_fails_closed_without_echoing_malformed_text(text: str) -> None:
    with pytest.raises(MarkdownParseError) as captured:
        MarkdownBlockParser().parse(text)

    assert "not closed" not in str(captured.value)
    assert "not-a-mapping" not in str(captured.value)


def test_block_normalization_preserves_content_but_stabilizes_hash_input() -> None:
    assert normalize_block_text("Café  \r\nLine\t \r\n") == "Café\nLine"
    assert normalize_block_text("Cafe\u0301\nLine") == "Café\nLine"
