from datetime import datetime, timedelta, timezone

import pytest

from harness_x.core.clock import FixedClock, SystemClock


def test_fixed_clock_normalizes_to_utc() -> None:
    source = datetime(2026, 8, 18, 14, 0, tzinfo=timezone(timedelta(hours=2)))
    assert FixedClock(source).now() == datetime(2026, 8, 18, 12, 0, tzinfo=timezone.utc)


def test_fixed_clock_rejects_naive_time() -> None:
    with pytest.raises(ValueError):
        FixedClock(datetime(2026, 8, 18, 12, 0))


def test_system_clock_is_timezone_aware() -> None:
    assert SystemClock().now().utcoffset() is not None
