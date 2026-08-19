"""Price extraction from product pages.

Extraction ladder (first hit wins):

1. User-supplied CSS selector from the watchlist.
2. Structured data: JSON-LD (schema.org Product/Offer) -- most reliable,
   present on the vast majority of large shops (Amazon, Zalando, MediaMarkt...).
3. Price meta tags / microdata (og:price:amount, itemprop="price", ...).
4. Heuristic scan of elements whose class/id mentions "price".
5. JavaScript state objects: prices embedded in inline <script> data
   (AliExpress `runParams`, Temu/Shein-style app state...). These shops ship
   the price as JSON inside JS instead of rendering it into the static DOM.

Also handles localized number formats: "$1,299.99", "1.299,99 EUR", "1299,-".
"""

from __future__ import annotations

import json
import logging
import re

from bs4 import BeautifulSoup

log = logging.getLogger(__name__)


class PriceNotFoundError(Exception):
    """Raised when no price could be extracted from a page."""


# A number that looks like a price: 1299 | 1,299.99 | 1.299,99 | 1299,- | 12.99
_PRICE_NUM = re.compile(
    r"(?<![\d.,])(\d{1,3}(?:[.,\s ]\d{3})*|\d+)(?:([.,])(\d{1,2}|-))?(?![\d])"
)

_CURRENCY_HINT = re.compile(r"(€|\$|£|USD|EUR|GBP|CHF|kr\b)", re.IGNORECASE)


def parse_price(text: str) -> float:
    """Parse the first price-looking number out of *text*, handling both
    '1,299.99' (US) and '1.299,99' (EU) conventions.

    Raises PriceNotFoundError if nothing numeric is found.
    """
    text = text.strip()
    m = _PRICE_NUM.search(text)
    if not m:
        raise PriceNotFoundError(f"No numeric price in text: {text!r}")
    return _match_to_float(m)


def parse_price_currency_first(text: str) -> float | None:
    """Like parse_price, but only accept numbers adjacent to a currency mark.

    Shops love mixing prices with other numbers in one element
    ("Ende: 26. Aug. 23:59 173,99€"); requiring a currency neighbor picks the
    173,99 instead of the 26. Returns None when no currency-adjacent number
    exists.
    """
    for m in _PRICE_NUM.finditer(text):
        before = text[max(0, m.start() - 4):m.start()]
        after = text[m.end():m.end() + 4]
        if _CURRENCY_HINT.search(before) or _CURRENCY_HINT.search(after):
            value = _match_to_float(m)
            if value > 0:
                return value
    return None


def _match_to_float(m: re.Match) -> float:
    integer_part, dec_sep, decimal_part = m.group(1), m.group(2), m.group(3)

    # "1299,-" style: trailing dash means .00
    if decimal_part == "-":
        decimal_part = "00"

    # Normalize the integer part by stripping any grouping characters.
    integer_digits = re.sub(r"[.,\s ]", "", integer_part)

    if decimal_part is None:
        # No decimal separator matched. But "1.299" alone is ambiguous:
        # if the integer part contains exactly one separator followed by
        # 3 digits, it's a thousands group, which the regex already folded in.
        return float(integer_digits)

    # A two-or-one digit tail after . or , is a decimal part.
    if len(decimal_part) <= 2:
        return float(f"{integer_digits}.{decimal_part}")

    return float(integer_digits + decimal_part)


def extract_price(html: str, selector: str | None = None) -> tuple[float, str]:
    """Extract (price, method) from *html*.

    *method* names the ladder rung that matched -- useful for logging and
    for debugging flaky shops.
    """
    soup = BeautifulSoup(html, "lxml")

    if selector:
        node = soup.select_one(selector)
        if node is None:
            raise PriceNotFoundError(f"CSS selector matched nothing: {selector!r}")
        return parse_price(node.get_text(" ", strip=True)), "css-selector"

    for finder, method in (
        (_from_json_ld, "json-ld"),
        (_from_meta_tags, "meta-tags"),
        (_from_heuristics, "heuristic"),
        (_from_js_state, "js-state"),
    ):
        price = finder(soup)
        if price is not None:
            return price, method

    raise PriceNotFoundError(
        "No price found via JSON-LD, meta tags, DOM heuristics, or JS state. "
        "Add an explicit `selector:` to this watch item."
    )


# --- ladder rung 2: JSON-LD -------------------------------------------------

def _from_json_ld(soup: BeautifulSoup) -> float | None:
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        price = _price_from_ld(data)
        if price is not None:
            return price
    return None


def _price_from_ld(data) -> float | None:
    """Walk arbitrarily nested JSON-LD looking for offers/price fields."""
    if isinstance(data, list):
        for item in data:
            price = _price_from_ld(item)
            if price is not None:
                return price
        return None
    if not isinstance(data, dict):
        return None

    for key in ("price", "lowPrice"):
        if key in data:
            try:
                return parse_price(str(data[key]))
            except PriceNotFoundError:
                pass

    # hasVariant: Zalando/MediaMarkt-style ProductGroup wrapping the Products
    for key in ("offers", "@graph", "itemListElement", "item", "priceSpecification",
                "hasVariant", "variants", "products", "mainEntity"):
        if key in data:
            price = _price_from_ld(data[key])
            if price is not None:
                return price
    return None


# --- ladder rung 3: meta tags / microdata ------------------------------------

_META_PROPS = (
    ("property", "product:price:amount"),
    ("property", "og:price:amount"),
    ("itemprop", "price"),
    ("name", "twitter:data1"),
)


def _from_meta_tags(soup: BeautifulSoup) -> float | None:
    for attr, value in _META_PROPS:
        for tag in soup.find_all(attrs={attr: value}):
            candidate = tag.get("content") or tag.get_text(" ", strip=True)
            if not candidate:
                continue
            try:
                return parse_price(candidate)
            except PriceNotFoundError:
                continue
    return None


# --- ladder rung 4: heuristics ------------------------------------------------

# --- ladder rung 5: JS state objects ------------------------------------------

# Ordered by specificity: purchase-price keys first, generic price keys last.
_JS_PRICE_PATTERNS = (
    # "salePrice": {..., "value": 24.99} / {"amount": "24.99"}  (AliExpress & co.)
    re.compile(
        r'"(?:act|sale|current|deal|promotion|app|final|min|discount)'
        r'[A-Za-z]*[Pp]rice[A-Za-z]*"\s*:\s*\{[^{}]{0,300}?'
        r'"(?:value|amount|minPrice)"\s*:\s*"?(\d+(?:\.\d+)?)"?',
        re.DOTALL,
    ),
    # "actSkuCalPrice":"24,99" / "salePrice":"24.99"  (direct string values)
    re.compile(
        r'"(?:actSkuCalPrice|skuCalPrice|actSkuMultiCurrencyCalPrice|salePrice|'
        r'currentPrice|discountPrice|dealPrice|minActivityAmount|finalPrice)"'
        r'\s*:\s*"(\d+(?:[.,]\d+)?)"'
    ),
    # "formattedAmount":"24,99 €" / "formattedPrice":"€ 24.99"
    re.compile(r'"formatted(?:Amount|Price)"\s*:\s*"([^"]{0,10}\d[^"]{0,14})"'),
    # generic fallback: any "...price...": {"value": N}
    re.compile(
        r'"[A-Za-z]*[Pp]rice[A-Za-z]*"\s*:\s*\{[^{}]{0,300}?'
        r'"(?:value|amount)"\s*:\s*"?(\d+(?:\.\d+)?)"?',
        re.DOTALL,
    ),
)

_PLAUSIBLE = (0.01, 1_000_000.0)


def _from_js_state(soup: BeautifulSoup) -> float | None:
    blobs = [
        s.string or s.get_text()
        for s in soup.find_all("script")
        if s.get("type") in (None, "", "text/javascript", "application/javascript")
    ]
    joined = "\n".join(b for b in blobs if b and ("rice" in b or "mount" in b))
    if not joined:
        return None
    for pattern in _JS_PRICE_PATTERNS:
        for m in pattern.finditer(joined):
            try:
                price = parse_price(m.group(1))
            except PriceNotFoundError:
                continue
            if _PLAUSIBLE[0] <= price <= _PLAUSIBLE[1]:
                return price
    return None


_PRICEY_ATTR = re.compile(r"price|preis|cost|amount", re.IGNORECASE)
_EXCLUDED_ATTR = re.compile(
    r"strike|old|was|before|uvp|list[-_]?price|text-price|crossed|shipping|"
    r"per[-_]?unit|basis|unit[-_]?price|savings?|discount[-_]?(?:percent|label)|"
    r"sponsored|percent",
    re.IGNORECASE,
)
# Reference/crossed-out prices betrayed by the TEXT itself ("UVP 279,90 €",
# "RRP: €23.00", "251,37€ sparen"). Live-captured from otto.de, amazon.de,
# and aliexpress.
_EXCLUDED_TEXT = re.compile(
    r"\b(uvp|rrp|statt|vorher|sparen|saves?|instead of|list price)\b",
    re.IGNORECASE,
)


def _from_heuristics(soup: BeautifulSoup) -> float | None:
    candidates: list[tuple[int, float]] = []
    for node in soup.find_all(True):
        # Amazon marks crossed-out list prices with data-a-strike="true".
        if node.get("data-a-strike") == "true":
            continue
        attr_blob = " ".join(
            [" ".join(node.get("class", [])), node.get("id", ""),
             node.get("data-testid", ""), node.get("itemprop", "")]
        )
        if not _PRICEY_ATTR.search(attr_blob) or _EXCLUDED_ATTR.search(attr_blob):
            continue
        # ...and any element nested inside such a crossed-out block.
        if node.find_parent(attrs={"data-a-strike": "true"}) is not None:
            continue
        text = node.get_text(" ", strip=True)
        if not text or len(text) > 60:  # long text => not a lone price element
            continue
        if _EXCLUDED_TEXT.search(text):
            continue
        # Currency-adjacent numbers first: "Ende: 26. Aug. 173,99€" -> 173.99,
        # never the 26 from the countdown.
        price = parse_price_currency_first(text)
        if price is not None:
            candidates.append((1, price))
            continue
        try:
            price = parse_price(text)
        except PriceNotFoundError:
            continue
        if price > 0:  # sponsored placeholders render as "0,00 €"
            candidates.append((0, price))

    if not candidates:
        return None
    candidates.sort(key=lambda c: c[0], reverse=True)
    return candidates[0][1]
