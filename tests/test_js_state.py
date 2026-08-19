"""JS-state extraction rung: prices embedded in inline <script> data
(AliExpress runParams, Temu/Shein-style app state)."""

import pytest

from lookout.price_parser import extract_price

# Condensed from the real structure of an AliExpress product page: the static
# HTML contains no price in the DOM at all -- only window.runParams JSON.
ALIEXPRESS_STYLE = """
<html><head><title>29.74€ | Some gadget - AliExpress</title></head>
<body><div id="root"></div>
<script>
window.runParams = {"data":{"priceComponent":{"origPrice":{"minAmount":
{"currency":"EUR","value":59.48,"formattedAmount":"59,48 €"}},
"discountPrice":{"minActivityAmount":{"currency":"EUR","value":29.74,
"formattedAmount":"29,74 €"}},
"salePrice":{"currency":"EUR","value":29.74,"formattedAmount":"29,74 €",
"minPrice":29.74}},"sellingPoints":[],"shippingFee":"3.99"}};
</script>
</body></html>
"""


def test_aliexpress_style_js_state():
    price, method = extract_price(ALIEXPRESS_STYLE)
    assert method == "js-state"
    # must find the discounted sale price (29.74), not shipping (3.99)
    # and not the crossed-out original (59.48)
    assert price == pytest.approx(29.74)


SKU_STRING_STYLE = """
<html><body><script>
var skuData = {"skuId":"12345","actSkuCalPrice":"24,99","actSkuMultiCurrencyCalPrice":"24.99",
"skuActivityAmount":{"value":24.99}};
</script></body></html>
"""


def test_direct_string_price_keys():
    price, method = extract_price(SKU_STRING_STYLE)
    assert method == "js-state"
    assert price == pytest.approx(24.99)


FORMATTED_ONLY = """
<html><body><script>
window.__INIT_DATA__ = {"goods":{"formattedPrice":"€ 12,90","stock":42}};
</script></body></html>
"""


def test_formatted_price_string():
    price, method = extract_price(FORMATTED_ONLY)
    assert method == "js-state"
    assert price == pytest.approx(12.90)


def test_dom_price_beats_js_state():
    page = """
    <html><body>
      <span class="product-price">19,99 €</span>
      <script>var x = {"salePrice":{"value":99.99}};</script>
    </body></html>
    """
    price, method = extract_price(page)
    assert method == "heuristic"
    assert price == pytest.approx(19.99)


def test_implausible_js_values_skipped():
    page = """
    <html><body><script>
    var t = {"priceTimestamp":{"value":1755600000000}};
    var real = {"salePrice":{"value":49.99}};
    </script></body></html>
    """
    price, method = extract_price(page)
    assert price == pytest.approx(49.99)
