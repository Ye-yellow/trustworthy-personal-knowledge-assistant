"""Deterministic Markdown-to-block parser for Obsidian notes."""

from __future__ import annotations

import re
import unicodedata
from collections import Counter

import yaml
from markdown_it import MarkdownIt
from markdown_it.token import Token

from trustworthy_kb.ingestion.errors import MarkdownParseError
from trustworthy_kb.ingestion.hashing import sha256_text
from trustworthy_kb.ingestion.types import ParsedBlock, ParsedDocument

_FENCE_OPEN = re.compile(r"^[ \t]{0,3}(`{3,}|~{3,})[^\n]*$")
_BLOCK_ID = re.compile(r"(?:^|\s)\^([A-Za-z0-9-]+)\s*$")
_BLOCK_TOKEN_TYPES = {
    "blockquote_open": "blockquote",
    "bullet_list_open": "list",
    "fence": "code_fence",
    "heading_open": "heading",
    "hr": "thematic_break",
    "ordered_list_open": "list",
    "paragraph_open": "paragraph",
    "table_open": "table",
}


class MarkdownBlockParser:
    """Parse normalized structural blocks without persisting their text."""

    def __init__(self) -> None:
        self._parser = MarkdownIt("commonmark").enable("table")

    def parse(self, text: str) -> ParsedDocument:
        """Return deterministic blocks or a redacted parse error."""

        normalized_document = _normalize_line_endings(text)
        _require_closed_fences(normalized_document)
        lines = normalized_document.splitlines(keepends=True)
        frontmatter_end = _frontmatter_end(lines)
        blocks: list[ParsedBlock] = []
        if frontmatter_end:
            frontmatter_text = "".join(lines[:frontmatter_end])
            _validate_frontmatter(frontmatter_text)
            blocks.append(_build_block(0, "frontmatter", "frontmatter:1", frontmatter_text))

        body_lines = lines[frontmatter_end:]
        tokens = self._parser.parse("".join(body_lines))
        heading_path: list[tuple[int, str]] = []
        heading_counts: Counter[tuple[tuple[str, ...], int, str]] = Counter()
        block_counts: Counter[tuple[tuple[str, ...], str]] = Counter()
        anchors: Counter[str] = Counter(block.anchor for block in blocks)

        for index, token in enumerate(tokens):
            block_type = _top_level_block_type(token)
            if block_type is None or token.map is None:
                continue
            start, end = token.map
            block_text = "".join(body_lines[start:end])
            if block_type == "heading":
                heading_level = int(token.tag.removeprefix("h"))
                heading_text = _heading_text(tokens, index)
                while heading_path and heading_path[-1][0] >= heading_level:
                    heading_path.pop()
                parent = tuple(anchor for _, anchor in heading_path)
                base_slug = _heading_slug(heading_text)
                heading_count_key = (parent, heading_level, base_slug)
                heading_counts[heading_count_key] += 1
                suffix = (
                    ""
                    if heading_counts[heading_count_key] == 1
                    else f"~{heading_counts[heading_count_key]}"
                )
                segment = f"h{heading_level}:{base_slug}{suffix}"
                heading_path.append((heading_level, segment))
                anchor = "/".join(anchor for _, anchor in heading_path)
            else:
                parent = tuple(anchor for _, anchor in heading_path)
                explicit_id = _explicit_block_id(block_text)
                if explicit_id:
                    candidate = "/".join((*parent, f"^{explicit_id}"))
                    anchors[candidate] += 1
                    duplicate_suffix = "" if anchors[candidate] == 1 else f"~{anchors[candidate]}"
                    anchor = f"{candidate}{duplicate_suffix}"
                else:
                    block_count_key = (parent, block_type)
                    block_counts[block_count_key] += 1
                    anchor = "/".join((*parent, f"{block_type}:{block_counts[block_count_key]}"))
            blocks.append(_build_block(len(blocks), block_type, anchor, block_text))

        return ParsedDocument(blocks=tuple(blocks))


def normalize_block_text(text: str) -> str:
    """Normalize block text for hashing without altering the raw snapshot."""

    normalized = unicodedata.normalize("NFC", _normalize_line_endings(text))
    return "\n".join(line.rstrip() for line in normalized.split("\n")).strip("\n")


def _normalize_line_endings(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _frontmatter_end(lines: list[str]) -> int:
    if not lines or lines[0].rstrip("\n") != "---":
        return 0
    for index, line in enumerate(lines[1:], start=1):
        if line.rstrip("\n") == "---":
            return index + 1
    raise MarkdownParseError("Markdown frontmatter is malformed")


def _validate_frontmatter(frontmatter_text: str) -> None:
    payload = "\n".join(frontmatter_text.splitlines()[1:-1])
    try:
        parsed = yaml.safe_load(payload)
    except yaml.YAMLError as error:
        raise MarkdownParseError("Markdown frontmatter is malformed") from error
    if parsed is not None and not isinstance(parsed, dict):
        raise MarkdownParseError("Markdown frontmatter must be a mapping")


def _require_closed_fences(text: str) -> None:
    active_character: str | None = None
    active_length = 0
    for line in text.splitlines():
        match = _FENCE_OPEN.match(line)
        if match is None:
            continue
        fence = match.group(1)
        if active_character is None:
            active_character = fence[0]
            active_length = len(fence)
            continue
        if (
            fence[0] == active_character
            and len(fence) >= active_length
            and line.strip().strip(active_character) == ""
        ):
            active_character = None
            active_length = 0
    if active_character is not None:
        raise MarkdownParseError("Markdown code fence is malformed")


def _top_level_block_type(token: Token) -> str | None:
    if token.level != 0:
        return None
    return _BLOCK_TOKEN_TYPES.get(token.type)


def _heading_text(tokens: list[Token], index: int) -> str:
    if index + 1 < len(tokens) and tokens[index + 1].type == "inline":
        return tokens[index + 1].content
    return "untitled"


def _heading_slug(text: str) -> str:
    normalized = unicodedata.normalize("NFC", " ".join(text.split())).strip().lower()
    return normalized or "untitled"


def _explicit_block_id(text: str) -> str | None:
    match = _BLOCK_ID.search(text)
    return match.group(1).lower() if match else None


def _build_block(ordinal: int, block_type: str, anchor: str, text: str) -> ParsedBlock:
    normalized = normalize_block_text(text)
    return ParsedBlock(
        ordinal=ordinal,
        block_type=block_type,
        anchor=anchor,
        text_hash=sha256_text(normalized),
        character_count=len(normalized),
        text=normalized,
    )


__all__ = ["MarkdownBlockParser", "normalize_block_text"]
