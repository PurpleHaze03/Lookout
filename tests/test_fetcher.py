"""Fetcher tests: bot-wall detection and the automatic rendering fallback."""

import pytest

from lookout import fetcher
from lookout.fetcher import BotBlockError, FetchError, _looks_blocked, fetch_html


class FakeResponse:
    def __init__(self, status_code=200, text="<html>ok</html>"):
        self.status_code = status_code
        self.text = text
        self.encoding = "utf-8"
        self.apparent_encoding = "utf-8"

    def raise_for_status(self):
        if self.status_code >= 400:
            raise fetcher.requests.HTTPError(f"{self.status_code}")


class FakeSession:
    def __init__(self, response):
        self._response = response
        self.headers = {}

    def get(self, url, timeout=None):
        return self._response

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _patch_session(monkeypatch, response):
    monkeypatch.setattr(fetcher.requests, "Session", lambda: FakeSession(response))


# --- bot-wall detection -----------------------------------------------------

def test_http_400_raises_bot_block_without_playwright(monkeypatch):
    _patch_session(monkeypatch, FakeResponse(status_code=400))
    monkeypatch.setattr(fetcher, "playwright_available", lambda: False)
    with pytest.raises(BotBlockError, match="playwright install chromium"):
        fetch_html("https://www.otto.de/p/whatever")


def test_captcha_page_detected_as_block(monkeypatch):
    captcha = "<html><title>Robot Check</title><body>Type the characters you see</body></html>"
    _patch_session(monkeypatch, FakeResponse(status_code=200, text=captcha))
    monkeypatch.setattr(fetcher, "playwright_available", lambda: False)
    with pytest.raises(BotBlockError):
        fetch_html("https://www.amazon.de/dp/B000000")


def test_looks_blocked_ignores_real_product_pages():
    big_real_page = "<html><body>" + "Great product with many words. " * 3000 + "</body></html>"
    assert not _looks_blocked(big_real_page)


def test_clean_200_passes_through(monkeypatch):
    _patch_session(monkeypatch, FakeResponse(text="<html>real content</html>"))
    assert fetch_html("https://example.com") == "<html>real content</html>"


# --- automatic rendering fallback ---------------------------------------------

def test_bot_block_falls_back_to_rendering(monkeypatch):
    _patch_session(monkeypatch, FakeResponse(status_code=403))
    monkeypatch.setattr(fetcher, "playwright_available", lambda: True)
    monkeypatch.setattr(fetcher, "_fetch_rendered",
                        lambda url, timeout: "<html>rendered!</html>")
    assert fetch_html("https://www.otto.de/p/x") == "<html>rendered!</html>"


def test_fallback_can_be_disabled(monkeypatch):
    _patch_session(monkeypatch, FakeResponse(status_code=403))
    monkeypatch.setattr(fetcher, "playwright_available", lambda: True)
    with pytest.raises(BotBlockError):
        fetch_html("https://www.otto.de/p/x", allow_render_fallback=False)


def test_render_js_true_skips_http(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("plain HTTP must not be used when render_js=True")
    monkeypatch.setattr(fetcher, "_fetch_http", boom)
    monkeypatch.setattr(fetcher, "_fetch_rendered", lambda url, timeout: "<html>r</html>")
    assert fetch_html("https://example.com", render_js=True) == "<html>r</html>"


def test_missing_playwright_message_is_actionable(monkeypatch):
    _patch_session(monkeypatch, FakeResponse(status_code=400))
    monkeypatch.setattr(fetcher, "playwright_available", lambda: False)
    with pytest.raises(FetchError) as excinfo:
        fetch_html("https://www.otto.de/p/whatever")
    assert "pip install playwright" in str(excinfo.value)
