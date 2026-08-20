"""Page fetching: browser-realistic HTTP with retries, bot-wall detection,
and automatic headless-Chromium fallback (Playwright) for protected shops.

Large shops (otto.de, amazon.*, zalando...) sit behind bot-detection layers
(Akamai, CloudFront, PerimeterX). A plain `requests` call with a generic or
tool-suffixed User-Agent is answered with 400/403/503 or a captcha page.
Strategy here:

1. Try plain HTTP with a full, realistic Chrome header set (fast + cheap).
2. If the response is a bot-wall (blocking status code OR a 200 that is
   actually a captcha/"access denied" interstitial), transparently retry in
   a real headless Chromium via Playwright, if it is installed.
3. If Playwright is missing, raise a clear error telling the user how to
   enable it.
"""

from __future__ import annotations

import importlib.util
import logging
import re
import time

import requests

log = logging.getLogger(__name__)

# A real, current Chrome-on-Windows identity. Shops reject obviously
# synthetic UAs (this is also why we don't append a tool suffix here).
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36"
)

BROWSER_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,image/apng,*/*;q=0.8"
    ),
    "Accept-Language": "de-DE,de;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding": "gzip, deflate",
    "Sec-Ch-Ua": '"Chromium";v="139", "Google Chrome";v="139", "Not?A_Brand";v="99"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
}

DEFAULT_TIMEOUT = 25  # seconds
RETRIES = 2
BACKOFF_BASE = 2.0

# Status codes that in practice mean "a bot-detection layer refused you",
# not "the page does not exist". 400 is included on purpose: Akamai-fronted
# shops (e.g. otto.de) answer non-browser clients with plain 400s.
BOT_BLOCK_STATUS = {400, 401, 403, 405, 406, 409, 412, 418, 429, 503}

# Markers of a 200 response that is really a captcha / block interstitial.
# "verify you are a human/robot" (not a bare "verify you are", which also
# matches age gates like "verify you are over 18").
_BLOCK_MARKERS = re.compile(
    r"captcha|robot check|are you a (?:human|robot)|access denied"
    r"|zugriff verweigert|pardon our interruption|unusual traffic"
    r"|verify you are (?:a )?(?:human|not a robot)|automated access"
    r"|/errors/validatecaptcha",
    re.IGNORECASE,
)


class FetchError(Exception):
    """Raised when a page could not be fetched after all retries."""


class BotBlockError(FetchError):
    """The site's bot protection refused the plain-HTTP request."""


def playwright_available() -> bool:
    return importlib.util.find_spec("playwright") is not None


def fetch_html(url: str, *, render_js: bool = False,
               allow_render_fallback: bool = True,
               timeout: int = DEFAULT_TIMEOUT) -> str:
    """Return the HTML of *url*.

    Plain HTTP first (unless *render_js* forces browser rendering). When the
    site's bot protection blocks plain HTTP and Playwright is installed, the
    fetch transparently falls back to a real headless browser.
    """
    if render_js:
        return _fetch_rendered(url, timeout=timeout)

    try:
        return _fetch_http(url, timeout=timeout)
    except BotBlockError as exc:
        if allow_render_fallback and playwright_available():
            log.info("%s -- falling back to headless-browser rendering", exc)
            return _fetch_rendered(url, timeout=timeout)
        raise BotBlockError(
            f"{exc} This shop blocks plain HTTP clients. Install browser "
            "rendering support with:  pip install playwright  &&  "
            "playwright install chromium  -- lookout will then fall back "
            "to it automatically (or set `render_js: true` on the item)."
        ) from exc


def _looks_blocked(text: str) -> bool:
    # Only scan the head of the page: block pages are tiny, and scanning a
    # full product page risks false positives from unrelated content.
    return bool(_BLOCK_MARKERS.search(text[:6000])) and len(text) < 60_000


def _fetch_http(url: str, *, timeout: int) -> str:
    last_error: Exception | None = None
    with requests.Session() as session:  # keeps cookies between retries
        session.headers.update(BROWSER_HEADERS)
        for attempt in range(1, RETRIES + 1):
            try:
                resp = session.get(url, timeout=timeout)
                if resp.status_code in BOT_BLOCK_STATUS:
                    raise BotBlockError(
                        f"{url} answered HTTP {resp.status_code} (bot protection)."
                    )
                resp.raise_for_status()
                if not resp.encoding or resp.encoding.lower() == "iso-8859-1":
                    resp.encoding = resp.apparent_encoding
                if _looks_blocked(resp.text):
                    raise BotBlockError(
                        f"{url} returned a captcha/block page instead of content."
                    )
                return resp.text
            except BotBlockError:
                raise  # no point retrying with the same fingerprint
            except requests.RequestException as exc:
                last_error = exc
                if attempt < RETRIES:
                    delay = BACKOFF_BASE ** attempt
                    log.warning("Fetch %s failed (attempt %d/%d): %s -- retrying in %.0fs",
                                url, attempt, RETRIES, exc, delay)
                    time.sleep(delay)
    raise FetchError(f"Could not fetch {url} after {RETRIES} attempts: {last_error}")


def _fetch_rendered(url: str, *, timeout: int) -> str:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # pragma: no cover - depends on optional extra
        raise FetchError(
            "Browser rendering requested, but Playwright is not installed. "
            "Run: pip install playwright && playwright install chromium"
        ) from exc

    try:
        with sync_playwright() as pw:
            # Anti-bot layers (Akamai & co.) fingerprint headless browsers.
            # Two countermeasures: hide the automation flag, and prefer the
            # user's REAL installed Chrome (closer fingerprint than the
            # bundled Chromium) when available.
            launch_args = ["--disable-blink-features=AutomationControlled"]
            browser = None
            for channel in ("chrome", "msedge", None):
                try:
                    browser = pw.chromium.launch(headless=True, channel=channel,
                                                 args=launch_args)
                    break
                except Exception:
                    continue
            if browser is None:  # bundled Chromium missing too
                browser = pw.chromium.launch(headless=True, args=launch_args)
            try:
                context = browser.new_context(
                    user_agent=USER_AGENT,
                    locale="de-DE",
                    timezone_id="Europe/Berlin",
                    viewport={"width": 1366, "height": 900},
                    extra_http_headers={"Accept-Language": "de-DE,de;q=0.9,en;q=0.7"},
                )
                # Patch the tells that betray headless automation.
                context.add_init_script(
                    "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
                    "window.chrome = window.chrome || {runtime:{}};"
                    "Object.defineProperty(navigator,'languages',{get:()=>['de-DE','de','en']});"
                    "Object.defineProperty(navigator,'plugins',{get:()=>[1,2,3,4,5]});"
                )
                page = context.new_page()
                # Skip heavy assets -- we only need the DOM.
                page.route(
                    re.compile(r"\.(png|jpe?g|gif|webp|avif|svg|woff2?|ttf|mp4)(\?|$)"),
                    lambda route: route.abort(),
                )
                # "networkidle" hangs forever on shops full of trackers;
                # DOM-ready plus a short settle wait is far more reliable.
                page.goto(url, timeout=timeout * 1000, wait_until="domcontentloaded")
                # JS-heavy shops (AliExpress...) inject their JSON-LD and price
                # DOM well after DOMContentLoaded -- wait for either to appear.
                try:
                    page.wait_for_selector(
                        'script[type="application/ld+json"], [class*="price" i]',
                        timeout=12000,
                    )
                except Exception:
                    pass  # not every page has either; take what's there
                page.wait_for_timeout(1500)
                content = page.content()
                if _looks_blocked(content):
                    raise BotBlockError(
                        f"{url} showed a captcha even to the headless browser. "
                        "Open the page once in your normal browser, or watch the "
                        "product on another shop."
                    )
                return content
            finally:
                browser.close()
    except (FetchError, BotBlockError):
        raise
    except Exception as exc:
        msg = str(exc)
        if "Executable doesn't exist" in msg or "browser has not been installed" in msg.lower():
            raise FetchError(
                "Playwright is installed but its browser is missing. "
                "Run once: playwright install chromium"
            ) from exc
        raise FetchError(f"Headless-browser fetch of {url} failed: {msg[:300]}") from exc
