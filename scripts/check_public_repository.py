"""Fail when public-repository candidates contain common private artifacts."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_FORBIDDEN_NAMES = {".env", "credentials.json", "secrets.json"}
_FORBIDDEN_SUFFIXES = {".db", ".key", ".p12", ".pem", ".pfx", ".sqlite", ".sqlite3"}
_FORBIDDEN_PARTS = {
    ".obsidian",
    "attachments",
    "blobs",
    "fixtures/private",
    "logs",
    "obsidian-vault",
    "traces",
    "vault",
}
_CONTENT_RULES = {
    "cloud-style API token": re.compile(r"(?<![A-Za-z0-9])" + "s" + r"k-[A-Za-z0-9_-]{16,}"),
    "GitHub token": re.compile(r"g" + r"h[pousr]_[A-Za-z0-9]{20,}"),
    "authorization bearer": re.compile(r"Bearer[ \t]+[A-Za-z0-9._-]{16,}"),
    "private key": re.compile(r"BEGIN[ \t]+(?:RSA[ \t]+)?PRIVATE[ \t]+KEY"),
    "Windows user path": re.compile(r"[A-Za-z]:[\\/]+Users[\\/]+[^\\/\s]+"),
    "Unix user path": re.compile(r"/(?:home|Users)/[^/\s]+"),
}


def repository_candidates() -> list[Path]:
    """Return tracked and unignored untracked files relative to the repository root."""

    result = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=_ROOT,
        check=True,
        capture_output=True,
    )
    return [Path(item.decode()) for item in result.stdout.split(b"\0") if item]


def scan_repository(paths: list[Path]) -> list[str]:
    """Return privacy findings without exposing matched secret values."""

    findings: list[str] = []
    for relative_path in paths:
        normalized = relative_path.as_posix()
        lowered = normalized.lower()
        if _forbidden_path(relative_path, lowered):
            findings.append(f"{normalized}: forbidden private artifact path")
            continue
        content = (_ROOT / relative_path).read_text(encoding="utf-8", errors="ignore")
        for label, pattern in _CONTENT_RULES.items():
            if pattern.search(content):
                findings.append(f"{normalized}: possible {label}")
    return findings


def _forbidden_path(path: Path, lowered: str) -> bool:
    if path.name.lower() in _FORBIDDEN_NAMES or path.suffix.lower() in _FORBIDDEN_SUFFIXES:
        return True
    return any(part == lowered or lowered.startswith(f"{part}/") for part in _FORBIDDEN_PARTS)


def main() -> int:
    """Run the repository scan and return a process exit code."""

    findings = scan_repository(repository_candidates())
    if findings:
        print("Public repository privacy scan failed:")
        for finding in findings:
            print(f"- {finding}")
        return 1
    print("Public repository privacy scan passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
