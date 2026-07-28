from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from trustworthy_kb.config import FetchSettings, GovernanceSettings, SearchSettings


def test_governance_settings_redact_paths_and_expose_boundary_values(tmp_path: Path) -> None:
    settings = GovernanceSettings(
        evidence_snapshot_root=str(tmp_path / "evidence"),
        checkpoint_path=str(tmp_path / "checkpoints" / "governance.sqlite"),
    )

    assert settings.evidence_snapshot_root_value == tmp_path / "evidence"
    assert settings.checkpoint_path_value.name == "governance.sqlite"
    assert str(tmp_path) not in repr(settings)
    assert str(tmp_path) not in settings.model_dump_json()


def test_search_and_fetch_settings_normalize_provider_and_media_types() -> None:
    search = SearchSettings(provider=" Sub2API ", model="  ")
    fetch = FetchSettings(allowed_media_types=["TEXT/HTML", "text/html", "application/pdf"])

    assert search.provider == "sub2api"
    assert search.model is None
    assert fetch.allowed_media_types == ("application/pdf", "text/html")


def test_governance_settings_reject_unsafe_limits() -> None:
    with pytest.raises(ValidationError):
        GovernanceSettings(max_claims_per_document=0)
    with pytest.raises(ValidationError):
        FetchSettings(max_redirects=20)
