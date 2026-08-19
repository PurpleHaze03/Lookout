"""Stock, keyword, and change-monitor logic."""

from lookout import watchers
from lookout.watchers import (IN_STOCK, OUT_OF_STOCK, UNKNOWN,
                                 detect_availability, evaluate_change,
                                 evaluate_keyword, evaluate_stock)

LD_OUT = """
<html><head><script type="application/ld+json">
{"@type":"Product","offers":{"@type":"Offer","price":"59.99",
 "availability":"https://schema.org/OutOfStock"}}
</script></head>
<body>In den Warenkorb (recommendations below)</body></html>
"""

LD_IN = LD_OUT.replace("OutOfStock", "InStock")

TEXT_OUT = "<html><body><h1>Sneaker</h1><p>Leider ausverkauft.</p></body></html>"
TEXT_IN = "<html><body><h1>Sneaker</h1><button>In den Warenkorb</button></body></html>"


# --- availability detection --------------------------------------------------

def test_json_ld_availability_beats_text_markers():
    # Page says "In den Warenkorb" in a carousel, but JSON-LD says OutOfStock.
    assert detect_availability(LD_OUT) == OUT_OF_STOCK


def test_json_ld_in_stock():
    assert detect_availability(LD_IN) == IN_STOCK


def test_german_text_markers():
    assert detect_availability(TEXT_OUT) == OUT_OF_STOCK
    assert detect_availability(TEXT_IN) == IN_STOCK


def test_unknown_when_no_signal():
    assert detect_availability("<html><body><p>Hello</p></body></html>") == UNKNOWN


# --- stock transition logic ------------------------------------------------------

def test_stock_first_check_never_alerts():
    result = evaluate_stock(LD_IN, previous_state=None)
    assert not result.should_alert
    assert result.availability == IN_STOCK


def test_stock_alerts_on_out_to_in_transition():
    result = evaluate_stock(LD_IN, previous_state={"availability": OUT_OF_STOCK})
    assert result.should_alert


def test_stock_stays_quiet_when_already_in_stock():
    result = evaluate_stock(LD_IN, previous_state={"availability": IN_STOCK})
    assert not result.should_alert


def test_stock_no_alert_on_in_to_out():
    result = evaluate_stock(LD_OUT, previous_state={"availability": IN_STOCK})
    assert not result.should_alert


# --- keyword logic ------------------------------------------------------------------

PAGE_WITH = "<html><body><p>Tickets available now for the summer tour!</p></body></html>"
PAGE_WITHOUT = "<html><body><p>Tour dates coming soon.</p></body></html>"


def test_keyword_first_check_records_only():
    r = evaluate_keyword(PAGE_WITH, "tickets available", "appears", None)
    assert r.present and not r.should_alert


def test_keyword_appears_alerts_on_transition():
    r = evaluate_keyword(PAGE_WITH, "Tickets Available", "appears", {"present": False})
    assert r.should_alert  # case-insensitive


def test_keyword_appears_quiet_when_still_present():
    r = evaluate_keyword(PAGE_WITH, "tickets available", "appears", {"present": True})
    assert not r.should_alert


def test_keyword_disappears_alerts():
    r = evaluate_keyword(PAGE_WITHOUT, "tickets available", "disappears", {"present": True})
    assert r.should_alert


# --- change monitor ---------------------------------------------------------------

BASE = ("<html><body><article><p>"
        + " ".join(f"Stable sentence number {i} stays exactly the same here." for i in range(30))
        + "</p></article></body></html>")

CHANGED = BASE.replace("number 3 stays", "number 3 was replaced entirely today and")\
              .replace("number 7 stays", "number 7 was also completely rewritten and")\
              .replace("number 11 stays", "number 11 got fresh new content and")\
              .replace("number 15 stays", "number 15 now reads differently and")\
              .replace("number 19 stays", "number 19 was swapped for something new and")\
              .replace("number 23 stays", "number 23 has brand new wording now and")\
              .replace("number 27 stays", "number 27 tells another story entirely and")


def test_change_first_check_stores_snapshot():
    r = evaluate_change(BASE, None)
    assert not r.should_alert
    assert r.new_snapshot  # snapshot recorded


def test_small_change_stays_quiet():
    prev = {"snapshot": watchers._normalized_text(BASE)}
    tiny = BASE.replace("number 3 stays", "number 3 still stays")
    r = evaluate_change(tiny, prev, min_change_percent=5.0)
    assert not r.should_alert
    # snapshot must NOT advance on quiet checks, so drift accumulates
    assert r.new_snapshot == prev["snapshot"]


def test_large_change_alerts_with_diff():
    prev = {"snapshot": watchers._normalized_text(BASE)}
    r = evaluate_change(CHANGED, prev, min_change_percent=5.0)
    assert r.should_alert
    assert r.change_percent >= 5.0
    assert "+" in r.diff_excerpt and "-" in r.diff_excerpt
    assert r.new_snapshot != prev["snapshot"]  # snapshot advances on alert


def test_state_roundtrip_helpers():
    raw = watchers.dumps_state(availability=IN_STOCK)
    assert watchers.loads_state(raw) == {"availability": IN_STOCK}
    assert watchers.loads_state(None) is None
    assert watchers.loads_state("not-json{") is None
