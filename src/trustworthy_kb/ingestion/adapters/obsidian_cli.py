"""Read-only Obsidian CLI inventory adapter."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path

from trustworthy_kb.ingestion.errors import (
    ObsidianCliUnavailableError,
    ObsidianCommandError,
)
from trustworthy_kb.ingestion.hashing import file_key, path_key
from trustworthy_kb.ingestion.inventory import VaultInventoryResult
from trustworthy_kb.ingestion.paths import path_is_in_scope, resolve_vault_markdown
from trustworthy_kb.ingestion.types import VaultFileObservation

type CommandExecutor = Callable[[tuple[str, ...]], Awaitable[str]]


class ObsidianCliInventory:
    """List Markdown through Obsidian CLI and validate each local path."""

    def __init__(
        self,
        *,
        executable: str,
        vault_id: str,
        vault_root: Path,
        allowed_roots: tuple[str, ...] = (".",),
        excluded_roots: tuple[str, ...] = (".obsidian", ".trash", "_AI", "attachments"),
        timeout_seconds: float = 30,
        output_limit_bytes: int = 4 * 1024 * 1024,
        command_executor: CommandExecutor | None = None,
    ) -> None:
        if not executable.strip() or not vault_id.strip():
            raise ValueError("Obsidian executable and Vault ID are required")
        if timeout_seconds <= 0 or output_limit_bytes < 1:
            raise ValueError("Obsidian command limits must be positive")
        self._executable = executable
        self._vault_id = vault_id
        self._vault_root = vault_root
        self._allowed_roots = allowed_roots
        self._excluded_roots = excluded_roots
        self._timeout_seconds = timeout_seconds
        self._output_limit_bytes = output_limit_bytes
        self._command_executor = command_executor or self._execute

    async def inventory(self) -> VaultInventoryResult:
        """Return a complete inventory or fail without partial deletion input."""

        output = await self._command_executor(
            (
                self._executable,
                f"vault={self._vault_id}",
                "files",
                "ext=md",
            )
        )
        observations: list[VaultFileObservation] = []
        seen: set[str] = set()
        for raw_line in output.splitlines():
            raw_path = raw_line.strip()
            if not raw_path:
                continue
            normalized, resolved = resolve_vault_markdown(self._vault_root, raw_path)
            if not path_is_in_scope(
                normalized,
                allowed_roots=self._allowed_roots,
                excluded_roots=self._excluded_roots,
            ):
                continue
            identity = path_key(normalized)
            if identity in seen:
                raise ObsidianCommandError("Obsidian inventory contains duplicate path identity")
            seen.add(identity)
            stat = await asyncio.to_thread(resolved.stat, follow_symlinks=False)
            observations.append(
                VaultFileObservation(
                    relative_path=normalized,
                    path_key=identity,
                    size=stat.st_size,
                    mtime_ns=stat.st_mtime_ns,
                    file_key=file_key(stat.st_dev, stat.st_ino),
                )
            )
        return VaultInventoryResult(
            complete=True,
            files=tuple(sorted(observations, key=lambda item: item.path_key)),
        )

    async def _execute(self, arguments: tuple[str, ...]) -> str:
        try:
            process = await asyncio.create_subprocess_exec(
                *arguments,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except (FileNotFoundError, PermissionError, OSError) as error:
            raise ObsidianCliUnavailableError("Obsidian CLI is unavailable") from error
        assert process.stdout is not None
        assert process.stderr is not None
        stdout_task = asyncio.create_task(_drain_limited(process.stdout, self._output_limit_bytes))
        stderr_task = asyncio.create_task(_drain_limited(process.stderr, self._output_limit_bytes))
        try:
            await asyncio.wait_for(process.wait(), timeout=self._timeout_seconds)
        except TimeoutError as error:
            process.kill()
            await process.wait()
            await asyncio.gather(stdout_task, stderr_task)
            raise ObsidianCommandError("Obsidian command timed out") from error
        stdout, stderr = await asyncio.gather(stdout_task, stderr_task)
        if stdout[1] or stderr[1]:
            raise ObsidianCommandError("Obsidian command output exceeded the configured limit")
        if process.returncode != 0:
            raise ObsidianCommandError("Obsidian command failed")
        try:
            return stdout[0].decode("utf-8")
        except UnicodeDecodeError as error:
            raise ObsidianCommandError("Obsidian command output encoding is invalid") from error


async def _drain_limited(
    stream: asyncio.StreamReader,
    limit: int,
) -> tuple[bytes, bool]:
    chunks: list[bytes] = []
    captured = 0
    overflow = False
    while chunk := await stream.read(64 * 1024):
        remaining = max(0, limit - captured)
        if remaining:
            chunks.append(chunk[:remaining])
            captured += min(len(chunk), remaining)
        if len(chunk) > remaining:
            overflow = True
    return b"".join(chunks), overflow


__all__ = ["ObsidianCliInventory"]
