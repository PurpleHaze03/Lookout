"""Non-price monitors: stock availability, keyword presence, page changes.

All three follow the same shape: derive a compact *state* from the fetched
page, compare it with the state stored from the previous run, and decide
whether the transition is alert-worthy. First runs only record state --
lookout never mails you about a page it has seen for the first time.
"""

from __future__ import annotations

import difflib
import json
import re
from dataclasses import dataclass

from bs4 import BeautifulSoup

from .summarizer import _extract_main_text

# --- stock availability -----------------------------------------------------------

IN_STOCK = "in_stock"
OUT_OF_STOCK = "out_of_stock"
UNKNOWN = "unknown"

_LD_IN = re.compile(r"InStock|LimitedAvailability|OnlineOnly|InStoreOnly|PreSale", re.I)
_LD_OUT = re.compile(r"OutOfStock|SoldOut|Discontinued", re.I)

_TEXT_OUT = re.compile(
    r"ausverkauft|out of stock|sold out|derzeit nicht verf|nicht verf(?:ü|u)gbar"
    r"|currently unavailable|nicht auf lager|vergriffen|nicht lieferbar"
    r"|momentan nicht verf|no longer available|leider ausverkauft",
    re.IGNORECASE,
)
_TEXT_IN = re.compile(
    r"auf lager|in stock|sofort lieferbar|sofort verf(?:ü|u)gbar|lieferbar in"
    r"|in den warenkorb|add to cart|add to basket|in den einkaufswagen|buy now",
    re.IGNORECASE,
)


def detect_availability(html: str) -> str:
    """Classify a product page as in_stock / out_of_stock / unknown.

    Structured data wins over text markers: an out-of-stock page still
    contains the words "add to cart" in its recommendation carousel.
    """
    soup = BeautifulSoup(html, "lxml")

    # 1. JSON-LD offers.availability
    for script in soup.find_all("script", type="application/ld+json"):
        blob = script.string or ""
        if "availability" not in blob:
            continue
        for m in re.finditer(r'"availability"\s*:\s*"([^"]+)"', blob):
            if _LD_OUT.search(m.group(1)):
                return OUT_OF_STOCK
            if _LD_IN.search(m.group(1)):
                return IN_STOCK

    # 2. microdata: <link itemprop="availability" href="...InStock">
    for tag in soup.find_all(attrs={"itemprop": "availability"}):
        value = tag.get("href", "") + " " + tag.get("content", "")
        if _LD_OUT.search(value):
            return OUT_OF_STOCK
        if _LD_IN.search(value):
            return IN_STOCK

    # 3. visible text markers (out-markers are far more specific than in-markers)
    text = soup.get_text(" ", strip=True)[:20000]
    if _TEXT_OUT.search(text):
        return OUT_OF_STOCK
    if _TEXT_IN.search(text):
        return IN_STOCK
    return UNKNOWN


@dataclass(frozen=True)
class StockResult:
    availability: str
    should_alert: bool
    reason: str


def evaluate_stock(html: str, previous_state: dict | None) -> StockResult:
    """Alert exactly on the out-of-stock -> in-stock transition."""
    availability = detect_availability(html)
    prev = (previous_state or {}).get("availability")

    if prev is None:
        return StockResult(availability, False, f"first check ({availability})")
    if prev == OUT_OF_STOCK and availability == IN_STOCK:
        return StockResult(availability, True, "back in stock")
    return StockResult(availability, False, f"{prev} -> {availability}")


# --- keyword watch ---------------------------------------------------------------


@dataclass(frozen=True)
class KeywordResult:
    present: bool
    should_alert: bool
    reason: str


def evaluate_keyword(html: str, keyword: str, trigger: str,
                     previous_state: dict | None) -> KeywordResult:
    """trigger='appears': alert when the keyword shows up on the page.
    trigger='disappears': alert when it vanishes (e.g. 'sold out' removed)."""
    text = BeautifulSoup(html, "lxml").get_text(" ", strip=True)
    present = keyword.lower() in text.lower()
    prev = (previous_state or {}).get("present")

    if prev is None:
        return KeywordResult(present, False, f"first check (present={present})")
    if trigger == "appears" and not prev and present:
        return KeywordResult(present, True, f"keyword {keyword!r} appeared")
    if trigger == "disappears" and prev and not present:
        return KeywordResult(present, True, f"keyword {keyword!r} disappeared")
    return KeywordResult(present, False, "no transition")


# --- page-change monitor ------------------------------------------------------------

_SNAPSHOT_CAP = 100_000  # chars stored per watched page


@dataclass(frozen=True)
class ChangeResult:
    change_percent: float
    should_alert: bool
    diff_excerpt: str
    new_snapshot: str
    reason: str


def _normalized_text(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    return _extract_main_text(soup)[:_SNAPSHOT_CAP]


def _diff_excerpt(old: str, new: str, max_lines: int = 14) -> str:
    """Human-readable excerpt of what changed, on sentence-ish chunks."""
    split = re.compile(r"(?<=[.!?:])\s+")
    old_lines, new_lines = split.split(old), split.split(new)
    out: list[str] = []
    for line in difflib.unified_diff(old_lines, new_lines, lineterm="", n=0):
        if line.startswith(("---", "+++", "@@")):
            continue
        marker = "+ " if line.startswith("+") else "- "
        content = line[1:].strip()
        if content:
            out.append(marker + (content[:220] + ("..." if len(content) > 220 else "")))
        if len(out) >= max_lines:
            out.append("... (more changes truncated)")
            break
    return "\n".join(out)


def evaluate_change(html: str, previous_state: dict | None,
                    min_change_percent: float = 5.0) -> ChangeResult:
    """Compare the page's main text with the stored snapshot.

    The snapshot only advances when an alert fires, so many small edits
    accumulate until they cross the threshold together -- a 1%-per-day
    drift can't stay forever below a 5% threshold.
    """
    new_text = _normalized_text(html)
    old_text = (previous_state or {}).get("snapshot")

    if old_text is None:
        return ChangeResult(0.0, False, "", new_text, "first check (snapshot stored)")

    ratio = difflib.SequenceMatcher(None, old_text.split(), new_text.split()).ratio()
    change_percent = round((1.0 - ratio) * 100.0, 1)

    if change_percent >= min_change_percent:
        return ChangeResult(
            change_percent, True, _diff_excerpt(old_text, new_text), new_text,
            f"content changed by ~{change_percent:.0f}%",
        )
    return ChangeResult(change_percent, False, "", old_text,
                        f"only ~{change_percent:.1f}% changed (threshold {min_change_percent:.0f}%)")


# --- shared state (de)serialization helpers ------------------------------------------


def dumps_state(**kwargs) -> str:
    return json.dumps(kwargs, ensure_ascii=False)


def loads_state(raw: str | None) -> dict | None:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None
