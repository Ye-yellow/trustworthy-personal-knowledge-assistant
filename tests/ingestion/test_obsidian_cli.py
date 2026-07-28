from __future__ import annotations

from pathlib import Path

import pytest

from trustworthy_kb.ingestion.adapters import ObsidianCliInventory
from trustworthy_kb.ingestion.errors import ObsidianCommandError


@pytest.mark.asyncio
async def test_obsidian_inventory_uses_explicit_vault_and_filters_scope(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    (vault / "Inbox").mkdir(parents=True)
    (vault / "Archive").mkdir()
    (vault / "Inbox" / "note.md").write_text("synthetic", encoding="utf-8")
    (vault / "Archive" / "old.md").write_text("synthetic", encoding="utf-8")
    captured_arguments: tuple[str, ...] | None = None

    async def execute(arguments: tuple[str, ...]) -> str:
        nonlocal captured_arguments
        captured_arguments = arguments
        return "Archive/old.md\nInbox/note.md\n"

    adapter = ObsidianCliInventory(
        executable="obsidian-synthetic",
        vault_id="synthetic-vault-id",
        vault_root=vault,
        allowed_roots=("Inbox",),
        excluded_roots=("Archive",),
        command_executor=execute,
    )

    result = await adapter.inventory()

    assert result.complete
    assert [item.relative_path for item in result.files] == ["Inbox/note.md"]
    assert captured_arguments == (
        "obsidian-synthetic",
        "vault=synthetic-vault-id",
        "files",
        "ext=md",
    )


@pytest.mark.asyncio
async def test_obsidian_inventory_rejects_duplicate_paths_without_output_echo(
    tmp_path: Path,
) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "private.md").write_text("synthetic", encoding="utf-8")

    async def execute(_arguments: tuple[str, ...]) -> str:
        return "private.md\nprivate.md\n"

    adapter = ObsidianCliInventory(
        executable="obsidian-synthetic",
        vault_id="private-vault-id",
        vault_root=vault,
        command_executor=execute,
    )

    with pytest.raises(ObsidianCommandError) as captured:
        await adapter.inventory()

    assert "private.md" not in str(captured.value)
    assert "private-vault-id" not in str(captured.value)
