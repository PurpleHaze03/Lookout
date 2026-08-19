"""Alert decision logic: when does a price check trigger an email?

Rules (per watch item):

* Price above target        -> no alert; any previous alert state is cleared,
                               so the next drop below target alerts again.
* Price at/below target,
  never alerted before      -> ALERT.
* Already alerted           -> re-alert only if the price dropped a further
                               `re_alert_drop_percent` below the last alerted
                               price, OR `re_alert_cooldown_hours` have passed.
                               This prevents an hourly poll from mailing the
                               user 24 times a day about the same deal.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from .config import WatchItem
from .storage import Storage

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class AlertDecision:
    should_alert: bool
    reason: str


def evaluate(item: WatchItem, price: float, storage: Storage,
             now: datetime | None = None) -> AlertDecision:
    now = now or datetime.now(timezone.utc)

    if price > item.target_price:
        state = storage.get_alert_state(item.key)
        if state.last_alert_price is not None:
            storage.clear_alert(item.key)
            log.info("%s back above target (%.2f > %.2f); alert state reset",
                     item.name, price, item.target_price)
        return AlertDecision(False, f"price {price:.2f} above target {item.target_price:.2f}")

    state = storage.get_alert_state(item.key)

    if state.last_alert_price is None:
        return AlertDecision(True, f"first drop to/below target ({price:.2f} <= {item.target_price:.2f})")

    drop_needed = state.last_alert_price * (1 - item.re_alert_drop_percent / 100.0)
    if price <= drop_needed:
        return AlertDecision(
            True,
            f"price fell a further {item.re_alert_drop_percent:.0f}%+ "
            f"({price:.2f} vs last alert {state.last_alert_price:.2f})",
        )

    if state.last_alert_at is not None:
        hours_since = (now - state.last_alert_at).total_seconds() / 3600.0
        if hours_since >= item.re_alert_cooldown_hours:
            return AlertDecision(
                True,
                f"still below target after {hours_since:.0f}h cooldown",
            )

    return AlertDecision(False, "below target but already alerted recently")
