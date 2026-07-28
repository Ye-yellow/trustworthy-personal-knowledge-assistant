from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest

from trustworthy_kb.persistence.types import UTCDateTime


def test_utc_datetime_serializes_and_restores_canonical_utc() -> None:
    column_type = UTCDateTime()
    original = datetime(2026, 7, 28, 12, 30, 45, 123456, tzinfo=timezone(timedelta(hours=8)))

    stored = column_type.process_bind_param(original, None)
    restored = column_type.process_result_value(stored, None)

    assert stored == "2026-07-28T04:30:45.123456Z"
    assert restored == datetime(2026, 7, 28, 4, 30, 45, 123456, tzinfo=UTC)


def test_utc_datetime_rejects_naive_values_and_invalid_storage() -> None:
    column_type = UTCDateTime()

    with pytest.raises(ValueError, match="timezone-aware"):
        column_type.process_bind_param(datetime(2026, 7, 28), None)
    with pytest.raises(ValueError, match="invalid UTC datetime"):
        column_type.process_result_value("not-a-date", None)


def test_utc_datetime_preserves_none() -> None:
    column_type = UTCDateTime()

    assert column_type.process_bind_param(None, None) is None
    assert column_type.process_result_value(None, None) is None
