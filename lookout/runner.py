"""One polling cycle: prices, stock, keywords, page changes, summaries."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from . import alerts, emailer, watchers
from .config import (ChangeItem, KeywordItem, SmtpConfig, StockItem,
                     SummaryItem, WatchItem, Watchlist)
from .fetcher import fetch_html, playwright_available
from .price_parser import PriceNotFoundError, extract_price
from .storage import Storage
from .summarizer import PageSummary, summarize_html

log = logging.getLogger(__name__)


@dataclass
class CycleReport:
    checked: int = 0
    price_alerts: int = 0
    stock_alerts: int = 0
    keyword_alerts: int = 0
    change_alerts: int = 0
    digests_sent: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def alerts_sent(self) -> int:
        return (self.price_alerts + self.stock_alerts
                + self.keyword_alerts + self.change_alerts)


# --- price watch ------------------------------------------------------------------


def check_watch_item(item: WatchItem, storage: Storage,
                     smtp: SmtpConfig | None, dry_run: bool = False) -> tuple[float, bool]:
    """Scrape one item, record history, maybe alert. Returns (price, alerted)."""
    html = fetch_html(item.url, render_js=item.render_js)
    try:
        price, method = extract_price(html, selector=item.selector)
    except PriceNotFoundError:
        # The static HTML may lack the price (JS-injected). One rendered retry.
        if item.render_js or not playwright_available():
            raise
        log.info("%s: no price in static HTML; retrying with browser rendering", item.name)
        html = fetch_html(item.url, render_js=True)
        price, method = extract_price(html, selector=item.selector)

    history = storage.history(item.key, limit=1)
    prev_price = history[0].price if history else None
    lowest_before = storage.lowest_price(item.key)
    storage.record_price(item.key, price, method)
    log.info("%s -> %.2f %s (via %s, target %.2f)",
             item.name, price, item.currency, method, item.target_price)

    decision = alerts.evaluate(item, price, storage)
    if not decision.should_alert:
        log.debug("No alert for %s: %s", item.name, decision.reason)
        return price, False

    if dry_run or smtp is None:
        # A dry run must not mutate alert state, or it would "consume" this drop
        # and silence the next real run for the same item.
        log.info("[dry-run] Would alert for %s: %s", item.name, decision.reason)
        return price, True
    emailer.send_price_alert(smtp, item, price,
                             lowest_seen=lowest_before,
                             reason=decision.reason,
                             prev_price=prev_price)
    storage.record_alert(item.key, price)
    return price, True


# --- stock / keyword / change watches ------------------------------------------------


def check_stock_item(item: StockItem, storage: Storage,
                     smtp: SmtpConfig | None, dry_run: bool = False) -> bool:
    html = fetch_html(item.url, render_js=item.render_js)
    prev = watchers.loads_state(storage.get_watch_state(item.key))
    result = watchers.evaluate_stock(html, prev)
    # Don't persist during a dry run (would consume the transition), and never
    # overwrite a known state with UNKNOWN -- a transient glitch/maintenance
    # page must not wipe a stored OUT_OF_STOCK and hide the coming restock.
    if not (dry_run or smtp is None) and result.availability != watchers.UNKNOWN:
        storage.set_watch_state(item.key, watchers.dumps_state(availability=result.availability))
    log.info("%s -> %s (%s)", item.name, result.availability, result.reason)

    if result.should_alert:
        if dry_run or smtp is None:
            log.info("[dry-run] Would send back-in-stock alert for %s", item.name)
        else:
            emailer.send_stock_alert(smtp, item.name, item.url)
        return True
    return False


def check_keyword_item(item: KeywordItem, storage: Storage,
                       smtp: SmtpConfig | None, dry_run: bool = False) -> bool:
    html = fetch_html(item.url, render_js=item.render_js)
    prev = watchers.loads_state(storage.get_watch_state(item.key))
    result = watchers.evaluate_keyword(html, item.keyword, item.trigger, prev)
    if not (dry_run or smtp is None):  # don't consume the transition on a dry run
        storage.set_watch_state(item.key, watchers.dumps_state(present=result.present))
    log.info("%s -> keyword present=%s (%s)", item.name, result.present, result.reason)

    if result.should_alert:
        if dry_run or smtp is None:
            log.info("[dry-run] Would send keyword alert for %s: %s", item.name, result.reason)
        else:
            emailer.send_keyword_alert(smtp, item.name, item.url,
                                       item.keyword, item.trigger)
        return True
    return False


def check_change_item(item: ChangeItem, storage: Storage,
                      smtp: SmtpConfig | None, dry_run: bool = False) -> bool:
    html = fetch_html(item.url, render_js=item.render_js)
    prev = watchers.loads_state(storage.get_watch_state(item.key))
    result = watchers.evaluate_change(html, prev, item.min_change_percent)
    if not (dry_run or smtp is None):  # don't advance the snapshot on a dry run
        storage.set_watch_state(item.key, watchers.dumps_state(snapshot=result.new_snapshot))
    log.info("%s -> %s", item.name, result.reason)

    if result.should_alert:
        if dry_run or smtp is None:
            log.info("[dry-run] Would send change alert for %s (~%.0f%%)",
                     item.name, result.change_percent)
        else:
            emailer.send_change_alert(smtp, item.name, item.url,
                                      result.change_percent, result.diff_excerpt)
        return True
    return False


# --- summaries ------------------------------------------------------------------------


def send_summary(item: SummaryItem, smtp: SmtpConfig | None,
                 dry_run: bool = False) -> PageSummary:
    """Scrape one page, summarize, mail as a single-page digest (ad-hoc use)."""
    html = fetch_html(item.url)
    summary = summarize_html(html, max_sentences=item.max_sentences)
    if dry_run or smtp is None:
        log.info("[dry-run] Would mail digest for %s (%d sentences)",
                 item.name, len(summary.sentences))
    else:
        emailer.send_summary_digest(smtp, item.name, item.url, summary)
    return summary


# --- the full cycle ---------------------------------------------------------------------


def run_cycle(watchlist: Watchlist, storage: Storage, smtp: SmtpConfig | None,
              dry_run: bool = False, include_summaries: bool = True) -> CycleReport:
    """Process the whole watchlist once. Errors on one item never block the rest."""
    report = CycleReport()

    def guarded(label: str, fn) -> bool | None:
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 -- isolate failures per item
            msg = f"{label}: {exc}"
            log.error("Item failed -- %s", msg)
            report.errors.append(msg)
            return None

    for item in watchlist.watches:
        result = guarded(item.name, lambda i=item: check_watch_item(i, storage, smtp, dry_run))
        if result is not None:
            report.checked += 1
            if result[1]:
                report.price_alerts += 1

    for item in watchlist.stock_watches:
        alerted = guarded(item.name, lambda i=item: check_stock_item(i, storage, smtp, dry_run))
        if alerted is not None:
            report.checked += 1
            report.stock_alerts += int(alerted)

    for item in watchlist.keyword_watches:
        alerted = guarded(item.name, lambda i=item: check_keyword_item(i, storage, smtp, dry_run))
        if alerted is not None:
            report.checked += 1
            report.keyword_alerts += int(alerted)

    for item in watchlist.change_watches:
        alerted = guarded(item.name, lambda i=item: check_change_item(i, storage, smtp, dry_run))
        if alerted is not None:
            report.checked += 1
            report.change_alerts += int(alerted)

    if include_summaries and watchlist.summaries:
        entries: list[tuple[str, str, PageSummary]] = []
        for item in watchlist.summaries:
            def collect(i=item):
                html = fetch_html(i.url)
                entries.append((i.name, i.url, summarize_html(html, i.max_sentences)))
                return True
            guarded(item.name, collect)

        if entries:
            def dispatch():
                if dry_run or smtp is None:
                    log.info("[dry-run] Would mail one digest covering %d page(s)", len(entries))
                else:
                    emailer.send_digest(smtp, entries)
                return True
            if guarded("digest", dispatch):
                report.digests_sent = 1

    return report
