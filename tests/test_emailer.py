"""Email template builders (pure functions, no SMTP needed)."""

from lookout.config import WatchItem
from lookout.emailer import (build_change_alert, build_digest,
                                build_keyword_alert, build_price_alert,
                                build_stock_alert)
from lookout.summarizer import PageSummary

ITEM = WatchItem(name="Tommy Hilfiger Trench", url="https://www.otto.de/p/x",
                 target_price=220.0, currency="EUR")


def test_price_alert_contents():
    subject, plain, html = build_price_alert(ITEM, 209.99, lowest_seen=199.99,
                                             prev_price=229.99, reason="below target")
    assert "209.99" in subject and "Tommy" in subject
    for body in (plain, html):
        assert "209.99" in body           # current price
        assert "220.00" in body           # target
        assert "199.99" in body           # lowest seen
        assert "229.99" in body           # previous price
    assert "PRICE ALERT" in html
    assert "otto.de" in html              # host shown
    assert html.count("https://www.otto.de/p/x") >= 1


def test_price_alert_without_history_rows():
    subject, plain, html = build_price_alert(ITEM, 209.99, lowest_seen=None,
                                             prev_price=None, reason="below target")
    assert "Lowest" not in html
    assert "209.99" in html


def test_stock_alert_contents():
    subject, plain, html = build_stock_alert("Sneaker", "https://shop.example/p/1")
    assert "Back in stock" in subject
    assert "BACK IN STOCK" in html
    assert "shop.example" in html


def test_keyword_alert_contents():
    subject, plain, html = build_keyword_alert("Tickets page", "https://t.example/x",
                                               "tickets available", "appears")
    assert "tickets available" in subject
    assert "appeared" in html
    assert "KEYWORD" in html


def test_change_alert_diff_rendering():
    diff = "+ New paragraph added here.\n- Old paragraph removed here."
    subject, plain, html = build_change_alert("Pricing page", "https://c.example/p",
                                              12.3, diff)
    assert "12%" in subject or "12" in subject
    assert "New paragraph added" in html and "Old paragraph removed" in html
    assert "PAGE CHANGED" in html
    assert diff.splitlines()[0] in plain


def test_digest_combines_multiple_pages():
    s1 = PageSummary(title="Article A", sentences=["First key point.", "Second point."],
                     word_count=500)
    s2 = PageSummary(title="Portal B", sentences=["Headline one here today."],
                     word_count=300)
    subject, plain, html = build_digest([("News A", "https://a.example", s1),
                                         ("News B", "https://b.example", s2)])
    assert "+ 1 more" in subject
    for body in (plain, html):
        assert "First key point." in body and "Headline one here today." in body
    assert "DIGEST" in html
    assert "a.example" in html and "b.example" in html


def test_html_is_escaped():
    evil = WatchItem(name='<script>alert("x")</script>', url="https://s.example/p",
                     target_price=10.0, currency="EUR")
    _, _, html = build_price_alert(evil, 9.99, None, None, "below target")
    assert "<script>alert" not in html
    assert "&lt;script&gt;" in html
