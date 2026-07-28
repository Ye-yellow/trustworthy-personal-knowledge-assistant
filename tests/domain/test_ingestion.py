from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from trustworthy_kb.domain import (
    IngestionAction,
    IngestionItemId,
    IngestionItemRecord,
    IngestionItemStatus,
    IngestionRunId,
    SourceId,
)


def test_ingestion_ids_use_declared_prefixes() -> None:
    assert IngestionRunId.generate().startswith("ingrun_")
    assert IngestionItemId.generate().startswith("ingitem_")


def test_ingestion_item_record_is_frozen_and_validates_signal_counts() -> None:
    now = datetime.now(UTC)
    item = IngestionItemRecord(
        id=IngestionItemId.generate(),
        run_id=IngestionRunId.generate(),
        source_id=SourceId.generate(),
        action=IngestionAction.UPDATED,
        relative_path="Projects/example.md",
        path_key="a" * 64,
        content_hash="b" * 64,
        status=IngestionItemStatus.PENDING,
        operation_id="synthetic-operation",
        safety_signals_json={"BIDI_CONTROL": 1},
        revision=1,
        created_at=now,
        updated_at=now,
    )

    with pytest.raises(ValidationError, match="frozen"):
        item.attempt = 2
    with pytest.raises(ValidationError):
        IngestionItemRecord.model_validate(
            {**item.model_dump(), "safety_signals_json": {"BIDI_CONTROL": -1}}
        )
