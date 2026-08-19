from datetime import datetime, timedelta, timezone

from lookout.storage import Storage

KEY = "https://shop.example/p/1"
T0 = datetime(2026, 8, 19, 8, 0, tzinfo=timezone.utc)


def test_history_is_recorded_and_ordered():
    with Storage(":memory:") as s:
        s.record_price(KEY, 120.0, "json-ld", at=T0)
        s.record_price(KEY, 110.0, "json-ld", at=T0 + timedelta(hours=1))
        s.record_price(KEY, 115.0, "heuristic", at=T0 + timedelta(hours=2))

        rows = s.history(KEY)
        assert [r.price for r in rows] == [115.0, 110.0, 120.0]  # newest first
        assert rows[0].method == "heuristic"


def test_lowest_price():
    with Storage(":memory:") as s:
        assert s.lowest_price(KEY) is None
        s.record_price(KEY, 120.0, "json-ld", at=T0)
        s.record_price(KEY, 99.5, "json-ld", at=T0 + timedelta(hours=1))
        assert s.lowest_price(KEY) == 99.5


def test_alert_state_roundtrip_and_clear():
    with Storage(":memory:") as s:
        assert s.get_alert_state(KEY).last_alert_price is None

        s.record_alert(KEY, 95.0, at=T0)
        state = s.get_alert_state(KEY)
        assert state.last_alert_price == 95.0
        assert state.last_alert_at == T0

        s.record_alert(KEY, 90.0, at=T0 + timedelta(hours=5))  # upsert
        assert s.get_alert_state(KEY).last_alert_price == 90.0

        s.clear_alert(KEY)
        assert s.get_alert_state(KEY).last_alert_price is None


def test_items_are_isolated():
    with Storage(":memory:") as s:
        s.record_price(KEY, 100.0, "json-ld", at=T0)
        assert s.history("https://other.example") == []
