from datetime import datetime, timedelta, timezone

import pytest

from lookout.alerts import evaluate
from lookout.config import WatchItem
from lookout.storage import Storage


@pytest.fixture
def storage():
    with Storage(":memory:") as s:
        yield s


@pytest.fixture
def item():
    return WatchItem(
        name="Test item", url="https://shop.example/p/1", target_price=100.0,
        re_alert_drop_percent=5.0, re_alert_cooldown_hours=24.0,
    )


NOW = datetime(2026, 8, 19, 12, 0, tzinfo=timezone.utc)


def test_above_target_never_alerts(item, storage):
    assert not evaluate(item, 120.0, storage, now=NOW).should_alert


def test_first_drop_below_target_alerts(item, storage):
    decision = evaluate(item, 99.0, storage, now=NOW)
    assert decision.should_alert


def test_no_repeat_alert_on_next_poll(item, storage):
    storage.record_alert(item.key, 99.0, at=NOW)
    decision = evaluate(item, 99.0, storage, now=NOW + timedelta(hours=1))
    assert not decision.should_alert


def test_re_alerts_after_further_drop(item, storage):
    storage.record_alert(item.key, 99.0, at=NOW)
    # 5% below 99.0 is 94.05 -> 94.0 re-alerts
    decision = evaluate(item, 94.0, storage, now=NOW + timedelta(hours=1))
    assert decision.should_alert


def test_small_further_drop_stays_quiet(item, storage):
    storage.record_alert(item.key, 99.0, at=NOW)
    decision = evaluate(item, 97.0, storage, now=NOW + timedelta(hours=1))
    assert not decision.should_alert


def test_re_alerts_after_cooldown(item, storage):
    storage.record_alert(item.key, 99.0, at=NOW)
    decision = evaluate(item, 99.0, storage, now=NOW + timedelta(hours=25))
    assert decision.should_alert


def test_state_resets_when_price_recovers(item, storage):
    storage.record_alert(item.key, 99.0, at=NOW)
    # Price climbs back above target -> state cleared...
    assert not evaluate(item, 130.0, storage, now=NOW + timedelta(hours=2)).should_alert
    # ...so the next dip below target alerts immediately again.
    assert evaluate(item, 98.0, storage, now=NOW + timedelta(hours=3)).should_alert
