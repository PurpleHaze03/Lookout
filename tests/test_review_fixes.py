"""Regression tests for issues found in the code-review pass."""

import pytest

from lookout import watchers
from lookout.config import ChangeItem, KeywordItem, StockItem, WatchItem
from lookout.price_parser import extract_price
from lookout.fetcher import _looks_blocked
from lookout.runner import (check_change_item, check_keyword_item,
                            check_stock_item, check_watch_item)
from lookout.storage import Storage


# --- dry-run must not consume alert / watch state --------------------------

def test_dry_run_price_does_not_burn_next_real_alert():
    item = WatchItem(name="x", url="http://h/p", target_price=100.0)
    with Storage(":memory:") as s:
        s.record_price(item.key, 90.0, "test")  # a prior reading so evaluate has history
        # dry-run: would alert, but must not record_alert
        import unittest.mock as mock
        html = "<html><body><span class='price'>90,00 €</span></body></html>"
        with mock.patch("lookout.runner.fetch_html", return_value=html), \
             mock.patch("lookout.runner.extract_price", return_value=(90.0, "test")):
            _, alerted = check_watch_item(item, s, smtp=None, dry_run=True)
        assert alerted
        # alert state must be untouched -> a real run would still fire
        assert s.get_alert_state(item.key).last_alert_price is None


def test_dry_run_stock_does_not_consume_transition():
    item = StockItem(name="s", url="http://h/s")
    with Storage(":memory:") as s:
        s.set_watch_state(item.key, watchers.dumps_state(availability=watchers.OUT_OF_STOCK))
        import unittest.mock as mock
        in_stock = "<html><body>sofort lieferbar in den warenkorb</body></html>"
        with mock.patch("lookout.runner.fetch_html", return_value=in_stock):
            check_stock_item(item, s, smtp=None, dry_run=True)
        # still OUT_OF_STOCK -> the real run will see the OUT->IN transition
        state = watchers.loads_state(s.get_watch_state(item.key))
        assert state["availability"] == watchers.OUT_OF_STOCK


# --- a transient UNKNOWN must not wipe a stored OUT_OF_STOCK ----------------

def test_unknown_reading_does_not_erase_out_of_stock():
    item = StockItem(name="s", url="http://h/s")
    with Storage(":memory:") as s:
        s.set_watch_state(item.key, watchers.dumps_state(availability=watchers.OUT_OF_STOCK))
        import unittest.mock as mock
        glitch = "<html><body>maintenance, nothing useful here</body></html>"
        with mock.patch("lookout.runner.fetch_html", return_value=glitch):
            check_stock_item(item, s, smtp=None, dry_run=False)  # persisting run
        state = watchers.loads_state(s.get_watch_state(item.key))
        assert state["availability"] == watchers.OUT_OF_STOCK  # not overwritten by UNKNOWN


# --- selector / meta paths use currency-adjacent parsing -------------------

def test_selector_skips_discount_badge():
    html = "<html><body><div class='p'>-23% Rabatt 999,00 €</div></body></html>"
    price, method = extract_price(html, selector=".p")
    assert price == pytest.approx(999.0)  # not 23
    assert method == "css-selector"


def test_meta_tag_skips_percentage():
    html = ('<html><head><meta itemprop="price" content="Save 30% - 49.99 EUR">'
            '</head><body></body></html>')
    price, _ = extract_price(html)
    assert price == pytest.approx(49.99)  # not 30


def test_meta_bare_numeric_still_works():
    html = '<html><head><meta property="product:price:amount" content="59.99"></head><body></body></html>'
    price, _ = extract_price(html)
    assert price == pytest.approx(59.99)


# --- JS-state reads application/json (Next.js __NEXT_DATA__) ----------------

def test_js_state_reads_next_data_json():
    html = ('<html><body><div id="root"></div>'
            '<script id="__NEXT_DATA__" type="application/json">'
            '{"props":{"product":{"salePrice":{"value":34.90,"currency":"EUR"}}}}'
            '</script></body></html>')
    price, method = extract_price(html)
    assert price == pytest.approx(34.90)
    assert method == "js-state"


# --- installment / "from" prices excluded by the heuristic -----------------

def test_heuristic_skips_installment_price():
    html = ("<html><body>"
            "<span class='price-monthly'>ab 25,00 €/Monat</span>"
            "<span class='product-price'>599,00 €</span>"
            "</body></html>")
    price, method = extract_price(html)
    assert price == pytest.approx(599.0)
    assert method == "heuristic"


# --- _looks_blocked no longer false-positives on age gates -----------------

def test_age_gate_is_not_treated_as_bot_wall():
    page = "<html><body><h1>verify you are over 18 to view this product</h1>" \
           "<span class='price'>19,99 €</span></body></html>"
    assert not _looks_blocked(page)


def test_real_captcha_still_detected():
    page = "<html><title>Robot Check</title><body>verify you are a human</body></html>"
    assert _looks_blocked(page)
