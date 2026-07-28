from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from trustworthy_kb.domain import ClaimType, Sensitivity
from trustworthy_kb.governance import (
    ClaimDraft,
    ClaimObject,
    ClaimOriginSpan,
    ClaimScope,
    claim_family_key,
    claim_fingerprint,
)


def make_claim(**overrides: object) -> ClaimDraft:
    values: dict[str, object] = {
        "claim_type": ClaimType.FACT,
        "subject": "Python",
        "predicate": "has documentation at",
        "object": ClaimObject(value="https://docs.python.org", value_type="url"),
        "scope": ClaimScope(domain="software", version="3.13"),
        "freshness_at": datetime(2026, 7, 28, tzinfo=UTC),
        "sensitivity": Sensitivity.PUBLIC,
        "origins": (ClaimOriginSpan(block_anchor="intro", start=0, end=12),),
    }
    values.update(overrides)
    return ClaimDraft.model_validate(values)


def test_claim_fingerprint_is_stable_and_ignores_origins_and_model_hints() -> None:
    base = make_claim()
    moved = make_claim(
        origins=(ClaimOriginSpan(block_anchor="moved", start=10, end=22),),
        model_risk_hints=("current fact",),
    )

    assert claim_fingerprint(base) == claim_fingerprint(moved)
    assert len(claim_fingerprint(base)) == 64


def test_claim_family_key_groups_changed_values_but_exact_fingerprint_does_not() -> None:
    base = make_claim()
    changed = make_claim(object=ClaimObject(value="https://docs.python.org/3/", value_type="url"))

    assert claim_family_key(base) == claim_family_key(changed)
    assert claim_fingerprint(base) != claim_fingerprint(changed)


def test_claim_contract_rejects_invalid_spans_ranges_and_extra_fields() -> None:
    with pytest.raises(ValidationError, match="after start"):
        ClaimOriginSpan(block_anchor="x", start=2, end=1)
    with pytest.raises(ValidationError, match="valid_to"):
        make_claim(
            valid_from=datetime(2026, 7, 29, tzinfo=UTC),
            valid_to=datetime(2026, 7, 28, tzinfo=UTC),
        )
    with pytest.raises(ValidationError, match="Extra inputs"):
        ClaimObject.model_validate({"value": 1, "value_type": "int", "verdict": "VERIFIED"})
