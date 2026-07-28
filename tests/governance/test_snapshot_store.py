from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from trustworthy_kb.governance.errors import EvidencePackIntegrityError
from trustworthy_kb.governance.snapshot_store import EvidenceSnapshotStore


def test_snapshot_store_writes_once_and_verifies_content(tmp_path: Path) -> None:
    store = EvidenceSnapshotStore(tmp_path / "evidence")

    digest, reference = store.put_json("packs", {"claim": "synthetic", "rank": 1})
    second_digest, second_reference = store.put_json("packs", {"rank": 1, "claim": "synthetic"})

    assert digest == second_digest
    assert reference == second_reference
    assert reference.startswith(f"packs/sha256/{digest[:2]}/{digest}")
    assert store.load_json(reference, digest) == {"claim": "synthetic", "rank": 1}


def test_snapshot_store_rejects_traversal_invalid_hash_and_tampering(tmp_path: Path) -> None:
    root = tmp_path / "evidence"
    store = EvidenceSnapshotStore(root)
    digest, reference = store.put_bytes("raw", b"synthetic", suffix="bin")

    with pytest.raises(EvidencePackIntegrityError, match=r"invalid.*hash"):
        store.load_bytes(reference, "bad")
    outside = tmp_path / "outside.bin"
    outside.write_bytes(b"outside")
    with pytest.raises(EvidencePackIntegrityError, match=r"unsafe.*reference"):
        store.load_bytes("../outside.bin", hashlib.sha256(b"outside").hexdigest())

    (root / reference).write_bytes(b"tampered")
    with pytest.raises(EvidencePackIntegrityError, match="integrity"):
        store.load_bytes(reference, digest)


@pytest.mark.parametrize(("category", "suffix"), [("unknown", "bin"), ("raw", "../bin")])
def test_snapshot_store_rejects_unsafe_names(tmp_path: Path, category: str, suffix: str) -> None:
    store = EvidenceSnapshotStore(tmp_path / "evidence")

    with pytest.raises(EvidencePackIntegrityError):
        store.put_bytes(category, b"synthetic", suffix=suffix)
