from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from trustworthy_kb.config import PublicationSettings, RetrievalSettings


def test_publication_settings_validate_private_nonoverlapping_paths(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    settings = PublicationSettings(
        vault_path=str(vault),
        snapshot_root=str(tmp_path / "snapshots"),
    )

    assert settings.vault_path_value == vault
    assert settings.staging_root == "_AI/Staging"
    assert str(vault) not in repr(settings)
    assert str(vault) not in settings.model_dump_json()


def test_publication_settings_reject_overlap_and_unsafe_roots(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    with pytest.raises(ValidationError, match="overlap"):
        PublicationSettings(vault_path=str(vault), snapshot_root=str(vault / "snapshots"))
    with pytest.raises(ValidationError, match="Vault-relative"):
        PublicationSettings(
            vault_path=str(vault),
            snapshot_root=str(tmp_path / "snapshots"),
            note_root="../outside",
        )


def test_retrieval_settings_are_provider_neutral_and_redact_token() -> None:
    settings = RetrievalSettings(
        milvus_uri="http://localhost:19530/",
        milvus_token="synthetic-secret",
        embedding_model="custom/embedding",
        reranker_provider="none",
    )

    assert settings.milvus_uri == "http://localhost:19530"
    assert settings.embedding_model == "custom/embedding"
    assert settings.model_cache_root_value.name == "model-cache"
    assert settings.milvus_token_value == "synthetic-secret"
    assert "synthetic-secret" not in repr(settings)
    assert "synthetic-secret" not in settings.model_dump_json()


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("vector_provider", "unknown", "vector provider"),
        ("collection_prefix", "unsafe-name", "collection prefix"),
        ("consistency", "maybe", "consistency"),
    ],
)
def test_retrieval_settings_reject_unsupported_values(field: str, value: str, message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        RetrievalSettings(**{field: value})  # type: ignore[arg-type]
