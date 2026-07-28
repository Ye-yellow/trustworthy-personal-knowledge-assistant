from __future__ import annotations

from pathlib import Path

import pytest

from trustworthy_kb.ingestion import (
    VaultPathPolicyError,
    canonical_json,
    canonical_json_hash,
    canonical_source_uri,
    normalize_vault_relative_path,
    path_is_in_scope,
    path_key,
    resolve_vault_markdown,
    sha256_bytes,
)


def test_hashes_and_source_uri_are_canonical() -> None:
    first = canonical_json({"z": 1, "a": ["知识", 2]})
    second = canonical_json({"a": ["知识", 2], "z": 1})

    assert first == second == '{"a":["知识",2],"z":1}'
    assert canonical_json_hash({"z": 1, "a": ["知识", 2]}) == sha256_bytes(first.encode("utf-8"))
    assert path_key("Projects/Éxample.md") == path_key("projects/E\u0301XAMPLE.md")
    assert canonical_source_uri("a" * 64, "项目/计划 1.md").endswith(
        "/%E9%A1%B9%E7%9B%AE/%E8%AE%A1%E5%88%92%201.md"
    )


@pytest.mark.parametrize(
    "candidate",
    [
        "../outside.md",
        "/absolute.md",
        "C:/private.md",
        "folder//note.md",
        "folder/CON.md",
        "folder/note.txt",
        "folder/note.md ",
    ],
)
def test_path_policy_rejects_unsafe_aliases_without_echo(candidate: str) -> None:
    with pytest.raises(VaultPathPolicyError) as captured:
        normalize_vault_relative_path(candidate)

    assert candidate not in str(captured.value)


def test_scope_and_resolution_use_normalized_relative_paths(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    note = vault / "Projects" / "note.md"
    note.parent.mkdir(parents=True)
    note.write_text("synthetic", encoding="utf-8")

    normalized, resolved = resolve_vault_markdown(vault, "Projects\\note.md")

    assert normalized == "Projects/note.md"
    assert resolved == note.resolve()
    assert path_is_in_scope(
        normalized,
        allowed_roots=("Projects",),
        excluded_roots=("Projects/Archive",),
    )
    assert not path_is_in_scope(
        "Projects/Archive/old.md",
        allowed_roots=("Projects",),
        excluded_roots=("Projects/Archive",),
    )


def test_resolution_rejects_symbolic_links(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    outside = tmp_path / "outside.md"
    outside.write_text("synthetic", encoding="utf-8")
    link = vault / "linked.md"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symbolic links are unavailable in this environment")

    with pytest.raises(VaultPathPolicyError, match="symbolic link"):
        resolve_vault_markdown(vault, "linked.md")
