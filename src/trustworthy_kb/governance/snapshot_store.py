"""Local content-addressed storage for evidence artifacts."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from trustworthy_kb.governance.errors import EvidencePackIntegrityError

_CATEGORIES = frozenset({"search", "raw", "extracted", "packs"})


class EvidenceSnapshotStore:
    """Write immutable artifacts below one private local root."""

    def __init__(self, root: Path) -> None:
        self._root = root.expanduser().resolve(strict=False)

    def put_bytes(self, category: str, content: bytes, *, suffix: str) -> tuple[str, str]:
        """Store bytes once and return their SHA-256 and root-relative reference."""

        safe_category = _category(category)
        safe_suffix = _suffix(suffix)
        digest = hashlib.sha256(content).hexdigest()
        target = self._artifact_path(safe_category, digest, safe_suffix)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            if target.is_symlink() or target.read_bytes() != content:
                raise EvidencePackIntegrityError("evidence snapshot hash collision")
        else:
            temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
            try:
                with temporary.open("xb") as output:
                    output.write(content)
                    output.flush()
                    os.fsync(output.fileno())
                temporary.replace(target)
            except FileExistsError:
                if target.is_symlink() or target.read_bytes() != content:
                    raise EvidencePackIntegrityError("evidence snapshot write conflict") from None
            finally:
                temporary.unlink(missing_ok=True)
        return digest, target.relative_to(self._root).as_posix()

    def put_json(self, category: str, value: Any) -> tuple[str, str]:
        """Store canonical UTF-8 JSON."""

        content = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return self.put_bytes(category, content, suffix="json")

    def load_bytes(self, reference: str, expected_hash: str) -> bytes:
        """Load a local artifact and verify path confinement and content identity."""

        if len(expected_hash) != 64 or any(
            character not in "0123456789abcdef" for character in expected_hash
        ):
            raise EvidencePackIntegrityError("invalid evidence snapshot hash")
        target = (self._root / reference).resolve(strict=True)
        if not target.is_relative_to(self._root) or target.is_symlink() or not target.is_file():
            raise EvidencePackIntegrityError("unsafe evidence snapshot reference")
        content = target.read_bytes()
        if hashlib.sha256(content).hexdigest() != expected_hash:
            raise EvidencePackIntegrityError("evidence snapshot integrity check failed")
        return content

    def load_json(self, reference: str, expected_hash: str) -> Any:
        """Load and parse a verified JSON artifact."""

        try:
            return json.loads(self.load_bytes(reference, expected_hash))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise EvidencePackIntegrityError("evidence snapshot JSON is invalid") from None

    def _artifact_path(self, category: str, digest: str, suffix: str) -> Path:
        target = self._root / category / "sha256" / digest[:2] / f"{digest}.{suffix}"
        resolved_parent = target.parent.resolve(strict=False)
        if not resolved_parent.is_relative_to(self._root):
            raise EvidencePackIntegrityError("unsafe evidence snapshot path")
        return target


def _category(value: str) -> str:
    if value not in _CATEGORIES:
        raise EvidencePackIntegrityError("unsupported evidence snapshot category")
    return value


def _suffix(value: str) -> str:
    normalized = value.strip().lower()
    if not normalized or not normalized.isalnum() or len(normalized) > 10:
        raise EvidencePackIntegrityError("unsafe evidence snapshot suffix")
    return normalized


__all__ = ["EvidenceSnapshotStore"]
