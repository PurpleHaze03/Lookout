"""Configuration loading: environment (.env) + watchlist (YAML)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from dotenv import load_dotenv


class ConfigError(Exception):
    """Raised when required configuration is missing or malformed."""


@dataclass(frozen=True)
class SmtpConfig:
    host: str
    port: int
    username: str
    password: str
    use_tls: bool
    email_from: str
    email_to: str


@dataclass
class WatchItem:
    """A product page watched for a price drop."""

    name: str
    url: str
    target_price: float
    currency: str = "EUR"
    selector: str | None = None
    render_js: bool = False
    re_alert_drop_percent: float = 5.0
    re_alert_cooldown_hours: float = 24.0

    @property
    def key(self) -> str:
        """Stable identifier used for storage (url uniquely identifies an item)."""
        return self.url


@dataclass
class SummaryItem:
    """A page whose summarized content is mailed as a digest."""

    name: str
    url: str
    max_sentences: int = 7


@dataclass
class StockItem:
    """A product page watched for coming back in stock."""

    name: str
    url: str
    render_js: bool = False

    @property
    def key(self) -> str:
        return f"stock:{self.url}"


@dataclass
class KeywordItem:
    """A page watched for a keyword appearing or disappearing."""

    name: str
    url: str
    keyword: str
    trigger: str = "appears"  # or "disappears"
    render_js: bool = False

    @property
    def key(self) -> str:
        return f"keyword:{self.trigger}:{self.keyword.lower()}:{self.url}"


@dataclass
class ChangeItem:
    """A page watched for meaningful content changes."""

    name: str
    url: str
    min_change_percent: float = 5.0
    render_js: bool = False

    @property
    def key(self) -> str:
        return f"change:{self.url}"


@dataclass
class Watchlist:
    watches: list[WatchItem] = field(default_factory=list)
    summaries: list[SummaryItem] = field(default_factory=list)
    stock_watches: list[StockItem] = field(default_factory=list)
    keyword_watches: list[KeywordItem] = field(default_factory=list)
    change_watches: list[ChangeItem] = field(default_factory=list)


def load_smtp_config(dotenv_path: str | os.PathLike | None = None) -> SmtpConfig:
    """Load SMTP settings from the environment (reading .env first if present)."""
    load_dotenv(dotenv_path=dotenv_path)

    missing = [k for k in ("SMTP_HOST", "SMTP_USERNAME", "SMTP_PASSWORD", "EMAIL_TO")
               if not os.getenv(k)]
    if missing:
        raise ConfigError(
            "Missing required environment variables: "
            + ", ".join(missing)
            + ". Copy .env.example to .env and fill them in."
        )

    return SmtpConfig(
        host=os.environ["SMTP_HOST"],
        port=int(os.getenv("SMTP_PORT", "587")),
        username=os.environ["SMTP_USERNAME"],
        password=os.environ["SMTP_PASSWORD"],
        use_tls=os.getenv("SMTP_USE_TLS", "true").strip().lower() in ("1", "true", "yes"),
        email_from=os.getenv("EMAIL_FROM") or os.environ["SMTP_USERNAME"],
        email_to=os.environ["EMAIL_TO"],
    )


def _require(mapping: dict, key: str, context: str):
    if key not in mapping or mapping[key] in (None, ""):
        raise ConfigError(f"Watchlist entry {context!r} is missing required field {key!r}.")
    return mapping[key]


def load_watchlist(path: str | os.PathLike | None = None) -> Watchlist:
    """Parse the YAML watchlist into typed items, validating required fields."""
    path = Path(path or os.getenv("LOOKOUT_WATCHLIST", "watchlist.yaml"))
    if not path.exists():
        raise ConfigError(
            f"Watchlist file not found: {path}. "
            "Copy watchlist.example.yaml to watchlist.yaml and edit it."
        )

    with open(path, encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}

    watches: list[WatchItem] = []
    for entry in raw.get("watches") or []:
        name = _require(entry, "name", str(entry))
        watches.append(
            WatchItem(
                name=name,
                url=_require(entry, "url", name),
                target_price=float(_require(entry, "target_price", name)),
                currency=str(entry.get("currency", "EUR")),
                selector=entry.get("selector") or None,
                render_js=bool(entry.get("render_js", False)),
                re_alert_drop_percent=float(entry.get("re_alert_drop_percent", 5.0)),
                re_alert_cooldown_hours=float(entry.get("re_alert_cooldown_hours", 24.0)),
            )
        )

    summaries: list[SummaryItem] = []
    for entry in raw.get("summaries") or []:
        name = _require(entry, "name", str(entry))
        summaries.append(
            SummaryItem(
                name=name,
                url=_require(entry, "url", name),
                max_sentences=int(entry.get("max_sentences", 7)),
            )
        )

    stock_watches: list[StockItem] = []
    for entry in raw.get("stock_watches") or []:
        name = _require(entry, "name", str(entry))
        stock_watches.append(
            StockItem(
                name=name,
                url=_require(entry, "url", name),
                render_js=bool(entry.get("render_js", False)),
            )
        )

    keyword_watches: list[KeywordItem] = []
    for entry in raw.get("keyword_watches") or []:
        name = _require(entry, "name", str(entry))
        trigger = str(entry.get("trigger", "appears")).strip().lower()
        if trigger not in ("appears", "disappears"):
            raise ConfigError(
                f"Keyword watch {name!r}: trigger must be 'appears' or 'disappears', "
                f"got {trigger!r}."
            )
        keyword_watches.append(
            KeywordItem(
                name=name,
                url=_require(entry, "url", name),
                keyword=str(_require(entry, "keyword", name)),
                trigger=trigger,
                render_js=bool(entry.get("render_js", False)),
            )
        )

    change_watches: list[ChangeItem] = []
    for entry in raw.get("change_watches") or []:
        name = _require(entry, "name", str(entry))
        change_watches.append(
            ChangeItem(
                name=name,
                url=_require(entry, "url", name),
                min_change_percent=float(entry.get("min_change_percent", 5.0)),
                render_js=bool(entry.get("render_js", False)),
            )
        )

    return Watchlist(watches=watches, summaries=summaries,
                     stock_watches=stock_watches,
                     keyword_watches=keyword_watches,
                     change_watches=change_watches)


def db_path() -> str:
    return os.getenv("LOOKOUT_DB", "lookout.db")


def poll_interval_minutes() -> int:
    return int(os.getenv("LOOKOUT_INTERVAL_MINUTES", "60"))
