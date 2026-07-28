"""Inventory protocol shared by Obsidian and synthetic adapters."""

from __future__ import annotations

from typing import Protocol

from trustworthy_kb.ingestion.types import IngestionValue, VaultFileObservation


class VaultInventoryResult(IngestionValue):
    complete: bool
    files: tuple[VaultFileObservation, ...]


class VaultInventory(Protocol):
    async def inventory(self) -> VaultInventoryResult:
        """Return a complete, path-sorted Vault inventory."""


__all__ = ["VaultInventory", "VaultInventoryResult"]
