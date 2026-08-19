import pytest

from lookout.price_parser import PriceNotFoundError, extract_price, parse_price


# --- parse_price: localized number formats -----------------------------------

@pytest.mark.parametrize("text,expected", [
    ("$1,299.99", 1299.99),
    ("1.299,99 €", 1299.99),
    ("1299", 1299.0),
    ("EUR 49,90", 49.90),
    ("Price: 12.99 USD", 12.99),
    ("1299,-", 1299.0),
    ("2 499,00 kr", 2499.0),
    ("Now only 999.00!", 999.0),
    ("0,99 €", 0.99),
])
def test_parse_price_formats(text, expected):
    assert parse_price(text) == pytest.approx(expected)


def test_parse_price_no_number_raises():
    with pytest.raises(PriceNotFoundError):
        parse_price("Out of stock")


# --- extract_price ladder -------------------------------------------------------

JSON_LD_PAGE = """
<html><head>
<script type="application/ld+json">
{"@context":"https://schema.org","@type":"Product","name":"Headphones",
 "offers":{"@type":"Offer","price":"179.95","priceCurrency":"EUR"}}
</script>
</head><body><div class="price">999.99</div></body></html>
"""


def test_json_ld_wins_over_heuristics():
    price, method = extract_price(JSON_LD_PAGE)
    assert price == pytest.approx(179.95)
    assert method == "json-ld"


META_PAGE = """
<html><head>
<meta property="product:price:amount" content="59.99">
</head><body></body></html>
"""


def test_meta_tag_extraction():
    price, method = extract_price(META_PAGE)
    assert price == pytest.approx(59.99)
    assert method == "meta-tags"


HEURISTIC_PAGE = """
<html><body>
  <div class="old-price">249,99 €</div>
  <span class="product-price">199,99 €</span>
  <div class="shipping-price">4,99 €</div>
</body></html>
"""


def test_heuristic_skips_old_and_shipping_price():
    price, method = extract_price(HEURISTIC_PAGE)
    assert price == pytest.approx(199.99)
    assert method == "heuristic"


def test_css_selector_takes_precedence():
    price, method = extract_price(JSON_LD_PAGE, selector=".price")
    assert price == pytest.approx(999.99)
    assert method == "css-selector"


def test_selector_matching_nothing_raises():
    with pytest.raises(PriceNotFoundError):
        extract_price(JSON_LD_PAGE, selector="#does-not-exist")


def test_no_price_anywhere_raises():
    with pytest.raises(PriceNotFoundError):
        extract_price("<html><body><p>Hello world, no numbers here.</p></body></html>")


NESTED_LD_PAGE = """
<html><head><script type="application/ld+json">
[{"@graph":[{"@type":"Product","offers":[{"@type":"Offer","lowPrice":"88.00"}]}]}]
</script></head><body></body></html>
"""


def test_nested_json_ld_low_price():
    price, method = extract_price(NESTED_LD_PAGE)
    assert price == pytest.approx(88.0)
    assert method == "json-ld"
