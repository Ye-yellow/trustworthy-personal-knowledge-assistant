from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from trustworthy_kb.domain import (
    ClaimId,
    ClaimRecord,
    ClaimStatus,
    ClaimType,
    Sensitivity,
    SourceId,
    SourceRecord,
    SourceType,
    TrustTier,
)


def test_source_record_is_frozen_and_rejects_unknown_fields() -> None:
    source = SourceRecord(
        id=SourceId.generate(),
        source_type=SourceType.OBSIDIAN_MARKDOWN,
        canonical_uri="obsidian://vault/example",
        owner="local-user",
        trust_tier=TrustTier.T0,
        sensitivity=Sensitivity.PRIVATE,
        revision=1,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )

    with pytest.raises(ValidationError, match="frozen"):
        source.revision = 2
    with pytest.raises(ValidationError, match="Extra inputs"):
        SourceRecord.model_validate({**source.model_dump(), "unexpected": True})


def test_domain_records_require_aware_datetimes() -> None:
    with pytest.raises(ValidationError, match="timezone"):
        SourceRecord(
            id=SourceId.generate(),
            source_type=SourceType.WEB_PAGE,
            canonical_uri="https://example.invalid/source",
            owner="local-user",
            trust_tier=TrustTier.T3,
            sensitivity=Sensitivity.PRIVATE,
            revision=1,
            created_at=datetime.now(),
            updated_at=datetime.now(),
        )


def test_claim_record_validates_hashes_and_score_ranges() -> None:
    with pytest.raises(ValidationError):
        ClaimRecord(
            id=ClaimId.generate(),
            claim_fingerprint="0" * 64,
            claim_family_key="1" * 64,
            claim_type=ClaimType.FACT,
            subject="system",
            predicate="has",
            object_json={"value": "property"},
            scope_json={},
            sensitivity=Sensitivity.PRIVATE,
            status=ClaimStatus.PROPOSED,
            revision=0,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
