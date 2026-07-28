from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check_public_repository.py"


@pytest.fixture(scope="module")
def scanner() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_public_repository", _SCRIPT)
    if spec is None or spec.loader is None:
        pytest.fail("could not load public repository scanner")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_repository_candidates_include_source_files(scanner: ModuleType) -> None:
    candidates = scanner.repository_candidates()

    assert Path("pyproject.toml") in candidates
    assert Path(".env") not in candidates


def test_current_repository_passes_privacy_scan(scanner: ModuleType) -> None:
    assert scanner.scan_repository(scanner.repository_candidates()) == []
