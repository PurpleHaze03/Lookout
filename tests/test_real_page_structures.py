"""Extraction tests against fixtures modeled on real shop/news page markup
(otto.de product page, amazon.de price block, news-portal front page)."""

import pytest

from lookout.price_parser import extract_price
from lookout.summarizer import summarize_html

# --- otto.de-style page: JSON-LD + crossed-out UVP in the DOM ---------------
# Mirrors the real Tommy Hilfiger trench page: 199.99 current, 279.90 UVP.

OTTO_STYLE = """
<html><head><title>Tommy Hilfiger Langjacke | OTTO</title>
<script type="application/ld+json">
{"@context":"http://schema.org","@type":"Product",
 "name":"Tommy Hilfiger Langjacke HERITAGE SINGLE BREASTED TRENCH",
 "offers":{"@type":"Offer","price":199.99,"priceCurrency":"EUR",
           "availability":"http://schema.org/InStock"}}
</script></head>
<body>
  <span class="pdp_price__retail-price">199,99 €</span>
  <span class="pdp_price__uvp">UVP 279,90 €</span>
</body></html>
"""


def test_otto_style_json_ld():
    price, method = extract_price(OTTO_STYLE)
    assert price == pytest.approx(199.99)
    assert method == "json-ld"


def test_otto_style_heuristic_avoids_uvp():
    # Same page WITHOUT JSON-LD: the heuristic must skip the UVP price.
    no_ld = OTTO_STYLE.replace('type="application/ld+json"', 'type="text/x-disabled"')
    price, method = extract_price(no_ld)
    assert price == pytest.approx(199.99)
    assert method == "heuristic"


# --- amazon.de-style price block: a-offscreen + struck-through list price -----

AMAZON_STYLE = """
<html><head><title>Amazon.de</title></head><body>
<div id="corePriceDisplay_desktop_feature_div">
  <span class="a-price aok-align-center">
    <span class="a-offscreen">24,99 €</span>
    <span aria-hidden="true">
      <span class="a-price-whole">24<span class="a-price-decimal">,</span></span>
      <span class="a-price-fraction">99</span>
      <span class="a-price-symbol">€</span>
    </span>
  </span>
  <span class="a-price a-text-price" data-a-strike="true">
    <span class="a-offscreen">39,99 €</span>
  </span>
</div>
<div id="unitPrice"><span class="a-price"><span class="a-offscreen">12,50 €</span></span></div>
</body></html>
"""


def test_amazon_style_current_price_not_list_price():
    price, method = extract_price(AMAZON_STYLE)
    assert price == pytest.approx(24.99)
    assert method == "heuristic"


def test_amazon_strike_block_children_excluded():
    only_strike = """
    <html><body>
      <span class="a-price a-text-price" data-a-strike="true">
        <span class="a-offscreen">39,99 €</span>
      </span>
    </body></html>
    """
    with pytest.raises(Exception):
        extract_price(only_strike)


# --- news-portal front page: headlines, no prose sentences ---------------------

NEWS_PORTAL = """
<html><head><title>Yahoo News - Latest News & Headlines</title></head><body>
<nav><a href="/">News</a><a href="/us">US</a><a href="/world">World</a></nav>
<div class="stream">
  <h3><a href="/1">Wildfire smoke blankets the western United States again</a></h3>
  <h3><a href="/2">Markets rally after central bank signals rate pause ahead</a></h3>
  <h3><a href="/3">New study links sleep patterns to long-term heart health</a></h3>
  <h3><a href="/4">Storm system expected to bring heavy rain to the coast</a></h3>
  <h3><a href="/5">Lawmakers debate new rules for online marketplaces</a></h3>
</div>
<footer>Terms Privacy About</footer>
</body></html>
"""


def test_news_portal_falls_back_to_headlines():
    summary = summarize_html(NEWS_PORTAL, max_sentences=4)
    assert len(summary.sentences) == 4
    assert any("Wildfire" in s for s in summary.sentences)
    # headline fallback must not swallow nav/footer junk
    assert all("Privacy" not in s for s in summary.sentences)


# --- structures captured LIVE from real shops (via browser, 2026-08-19) --------

# zalando.de: Product nested inside ProductGroup.hasVariant
ZALANDO_LIVE = """
<html><head><script type="application/ld+json">
{"@context":"https://schema.org","@type":"ProductGroup",
 "name":"Puma COURT CLASSIC","brand":{"@type":"Brand","name":"Puma"},
 "hasVariant":[{"@type":"Product","sku":"PU112O0P9-A11",
   "offers":{"@type":"Offer","priceCurrency":"EUR","price":"64.95",
             "availability":"https://schema.org/InStock"}}]}
</script></head><body><span class="hD5J5m">€64.95VAT included</span></body></html>
"""


def test_zalando_product_group_has_variant():
    price, method = extract_price(ZALANDO_LIVE)
    assert price == pytest.approx(64.95)
    assert method == "json-ld"


# aliexpress rendered DOM: countdown text glued to prices in one element
ALIEXPRESS_DOM_LIVE = """
<html><body>
  <div class="price-default--wrap--uwQneeq">Ende : 26. Aug. 23:59 CET173,99€ 251,37€ sparen425,36€</div>
  <div class="price-default--current--F8OlYIo">173,99€</div>
  <div class="price-default--origin--WWvVpp1">425,36€</div>
</body></html>
"""


def test_aliexpress_countdown_text_does_not_poison_price():
    price, method = extract_price(ALIEXPRESS_DOM_LIVE)
    assert method == "heuristic"
    # must NOT be 26 (from "26. Aug") and not the crossed-out 425.36
    assert price == pytest.approx(173.99)


# otto.de: sponsored-content placeholders rendering as 0,00 EUR + UVP label
OTTO_SPONSORED_LIVE = """
<html><body>
  <span class="pdp_sponsored-content-alternative__price">0,00 € UVP 0,00 €</span>
  <span class="pdp_price__discount-label">UVP 279,90 €</span>
  <span class="pdp_price__retail-price">209,99 €</span>
</body></html>
"""


def test_otto_zero_and_uvp_placeholders_skipped():
    price, method = extract_price(OTTO_SPONSORED_LIVE)
    assert method == "heuristic"
    assert price == pytest.approx(209.99)


# mediamarkt.de: percent badge and UVP glued into price-adjacent elements
MEDIAMARKT_LIVE = """
<html><head><script type="application/ld+json">
{"@type":"ProductGroup","hasVariant":[{"@type":"Product",
 "offers":{"@type":"Offer","priceCurrency":"EUR","price":999,
           "itemCondition":"https://schema.org/NewCondition"}}]}
</script></head>
<body>
  <div data-test="mms-branded-price">-23%UVP 1299,– €1299,00 €</div>
  <span class="branded-price-whole">999,</span>
</body></html>
"""


def test_mediamarkt_numeric_ld_price():
    price, method = extract_price(MEDIAMARKT_LIVE)
    assert price == pytest.approx(999.0)
    assert method == "json-ld"


def test_prose_article_still_uses_sentence_ranking():
    article = """
    <html><head><title>A</title></head><body><article><p>
    Solar power generation reached a new record in Europe this summer season.
    Analysts attribute the record to rapidly falling panel prices everywhere.
    Grid operators warn that storage capacity has not kept pace with growth.
    Policy makers are debating incentives for home batteries and tariffs.
    Industry groups expect the record to be broken again next year too.
    </p></article></body></html>
    """
    summary = summarize_html(article, max_sentences=3)
    assert len(summary.sentences) == 3
    assert all(s.endswith(".") for s in summary.sentences)
