"""Command-line interface.

    lookout check                 run one polling cycle (all watches + digests)
    lookout check --dry-run       same, but log instead of sending mail
    lookout run                   keep polling on a schedule (daemon mode)
    lookout summarize URL         summarize any page ad hoc (--email to send it)
    lookout price URL             extract the price of any product page ad hoc
    lookout list                  show the configured watchlist
    lookout history URL           show recorded price history + stats for an item
    lookout export                export all price history to CSV
"""

from __future__ import annotations

import argparse
import logging
import smtplib
import sys

from . import __version__, config, runner
from .config import ConfigError
from .fetcher import FetchError, fetch_html, playwright_available
from .price_parser import PriceNotFoundError, extract_price
from .storage import Storage
from .summarizer import summarize_html

log = logging.getLogger("lookout")


def _smtp_or_none(require: bool):
    try:
        return config.load_smtp_config()
    except ConfigError as exc:
        if require:
            raise
        log.warning("SMTP not configured (%s) -- running without email.", exc)
        return None


def cmd_check(args) -> int:
    watchlist = config.load_watchlist(args.watchlist)
    smtp = _smtp_or_none(require=not args.dry_run)
    with Storage(config.db_path()) as storage:
        report = runner.run_cycle(watchlist, storage, smtp, dry_run=args.dry_run,
                                  include_summaries=not args.no_summaries)
    parts = []
    for label, n in (("price", report.price_alerts), ("stock", report.stock_alerts),
                     ("keyword", report.keyword_alerts), ("change", report.change_alerts)):
        if n:
            parts.append(f"{n} {label}")
    alerts_str = ", ".join(parts) if parts else "0"
    print(f"Checked {report.checked} item(s); alerts: {alerts_str}; "
          f"{report.digests_sent} digest mail(s).")
    for err in report.errors:
        print(f"  ERROR: {err}", file=sys.stderr)
    return 1 if report.errors else 0


def cmd_run(args) -> int:
    from apscheduler.schedulers.blocking import BlockingScheduler

    interval = args.interval or config.poll_interval_minutes()
    smtp = _smtp_or_none(require=True)

    def job():
        try:
            watchlist = config.load_watchlist(args.watchlist)  # re-read: edits picked up live
            with Storage(config.db_path()) as storage:
                report = runner.run_cycle(watchlist, storage, smtp)
            log.info("Cycle done: %d checked, %d alerts, %d digests, %d errors",
                     report.checked, report.alerts_sent, report.digests_sent,
                     len(report.errors))
        except Exception:
            log.exception("Polling cycle failed")

    print(f"lookout {__version__}: polling every {interval} min. Ctrl+C to stop.")
    scheduler = BlockingScheduler()
    scheduler.add_job(job, "interval", minutes=interval, next_run_time=None)
    job()  # run immediately on startup, then on the interval
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        print("\nStopped.")
    return 0


def cmd_summarize(args) -> int:
    html = fetch_html(args.url, render_js=args.render_js)
    summary = summarize_html(html, max_sentences=args.sentences)
    if not summary.sentences and not args.render_js and playwright_available():
        print("No readable text in static HTML; retrying with browser rendering...",
              file=sys.stderr)
        html = fetch_html(args.url, render_js=True)
        summary = summarize_html(html, max_sentences=args.sentences)
    print(f"# {summary.title}\n")
    for s in summary.sentences:
        print(f"* {s}")
    print(f"\n({len(summary.sentences)} sentences from ~{summary.word_count} words)")
    if args.email:
        from . import emailer
        smtp = config.load_smtp_config()
        emailer.send_summary_digest(smtp, summary.title, args.url, summary)
        print(f"Digest mailed to {smtp.email_to}.")
    return 0


def cmd_price(args) -> int:
    html = fetch_html(args.url, render_js=args.render_js)
    try:
        price, method = extract_price(html, selector=args.selector)
    except PriceNotFoundError:
        # App-shell shops (AliExpress & co.) ship static HTML with no price
        # at all -- same automatic rendered retry the watchlist runner does.
        if args.render_js or not playwright_available():
            if not playwright_available():
                print("No price in the static HTML -- this looks like a "
                      "JavaScript-only shop.\nInstall browser rendering to "
                      "handle it automatically:\n"
                      "  pip install playwright && playwright install chromium",
                      file=sys.stderr)
                return 1
            raise
        print("No price in static HTML; retrying with browser rendering...",
              file=sys.stderr)
        html = fetch_html(args.url, render_js=True)
        try:
            price, method = extract_price(html, selector=args.selector)
        except PriceNotFoundError:
            _print_page_diagnostic(html)
            raise
    print(f"{price:.2f}  (extracted via {method})")
    return 0


def _print_page_diagnostic(html: str) -> None:
    """When even the rendered page has no price, show what we actually got --
    a tiny page or a suspicious title usually means a bot-challenge page."""
    import re as _re

    m = _re.search(r"<title[^>]*>(.*?)</title>", html, _re.IGNORECASE | _re.DOTALL)
    title = _re.sub(r"\s+", " ", m.group(1)).strip()[:90] if m else "(no title)"
    print(f"Diagnostic: rendered page was {len(html) // 1024} KB, "
          f"title: {title!r}", file=sys.stderr)
    if len(html) < 30_000:
        print("  That is suspiciously small -- the shop probably served a "
              "bot-challenge page\n  instead of the product. Try again in a few "
              "minutes; repeated fast retries make it worse.", file=sys.stderr)


def cmd_list(args) -> int:
    wl = config.load_watchlist(args.watchlist)
    sections = [
        ("Price watches", [(w.name, f"target {w.target_price:.2f} {w.currency}", w.url)
                           for w in wl.watches]),
        ("Stock watches", [(s.name, "back-in-stock alert", s.url) for s in wl.stock_watches]),
        ("Keyword watches", [(k.name, f"'{k.keyword}' {k.trigger}", k.url)
                             for k in wl.keyword_watches]),
        ("Change monitors", [(c.name, f">= {c.min_change_percent:.0f}% change", c.url)
                             for c in wl.change_watches]),
        ("Summary digests", [(s.name, f"{s.max_sentences} sentences", s.url)
                             for s in wl.summaries]),
    ]
    if not any(items for _, items in sections):
        print("Watchlist is empty.")
        return 0
    for title, items in sections:
        if items:
            print(f"{title}:")
            for name, detail, url in items:
                print(f"  * {name}: {detail}  <{url}>")
    return 0


def cmd_history(args) -> int:
    with Storage(config.db_path()) as storage:
        rows = storage.history(args.url, limit=args.limit)
        stats = storage.stats(args.url)
    if not rows:
        print("No recorded history for that URL yet. Run `lookout check` first.")
        return 0
    if stats:
        print(f"{stats['count']} checks | min {stats['min']:.2f} | "
              f"max {stats['max']:.2f} | avg {stats['avg']:.2f}\n")
    for row in rows:
        print(f"{row.checked_at:%Y-%m-%d %H:%M} UTC  {row.price:>10.2f}  ({row.method})")
    return 0


def cmd_export(args) -> int:
    import csv

    with Storage(config.db_path()) as storage:
        rows = storage.all_history(args.url)
    if not rows:
        print("No price history recorded yet. Run `lookout check` first.")
        return 0
    with open(args.out, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["url", "price", "method", "checked_at_utc"])
        writer.writerows(rows)
    print(f"Exported {len(rows)} row(s) to {args.out}")
    return 0


_EXAMPLES = """\
command structure:
  lookout <command> [arguments] [--options]

everyday examples:
  lookout check --dry-run
        test everything in watchlist.yaml WITHOUT sending any mail
  lookout check
        one full cycle: scrape all watches, send due alerts + digest
  lookout run --interval 30
        keep running, checking every 30 minutes (Ctrl+C to stop)

ad-hoc examples (no watchlist needed):
  lookout price "https://www.otto.de/p/some-product/"
        show the price lookout sees on any product page
  lookout price "https://shop.example/p/1" --selector ".price-tag"
        debug a custom CSS selector for a stubborn shop
  lookout summarize "https://www.tagesschau.de/some-article.html"
        print a summary of any page
  lookout summarize "https://www.yahoo.com/news/" --sentences 5 --email
        summarize and mail it to yourself

history examples:
  lookout history "https://www.otto.de/p/some-product/"
        recorded prices + min/max/avg for a watched item
  lookout export --out prices.csv
        dump every recorded price as CSV (Excel-ready)

first-time setup:  run setup.bat (Windows) or ./setup.sh (Linux/macOS),
then edit .env (mail credentials) and watchlist.yaml (what to watch).
Quote URLs ("...") -- the & characters in them break the shell otherwise."""


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="lookout",
        description="Website monitor with email alerts: price drops, restocks, "
                    "keywords, page changes, and content digests.",
        epilog=_EXAMPLES,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--version", action="version", version=f"lookout {__version__}")
    p.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    sub = p.add_subparsers(dest="command", required=True, metavar="<command>")

    def cmd(name, help_, example):
        return sub.add_parser(
            name, help=help_, description=help_[0].upper() + help_[1:] + ".",
            epilog="example:\n  " + example,
            formatter_class=argparse.RawDescriptionHelpFormatter,
        )

    c = cmd("check", "run one polling cycle now (all watches + digests)",
            "lookout check --dry-run")
    c.add_argument("--watchlist", default=None, help="path to watchlist.yaml "
                   "(default: ./watchlist.yaml)")
    c.add_argument("--dry-run", action="store_true", help="log what WOULD be "
                   "mailed instead of sending")
    c.add_argument("--no-summaries", action="store_true", help="skip summary digests")
    c.set_defaults(func=cmd_check)

    r = cmd("run", "poll continuously on a schedule (daemon mode)",
            "lookout run --interval 30")
    r.add_argument("--watchlist", default=None, help="path to watchlist.yaml")
    r.add_argument("--interval", type=int, default=None,
                   help="minutes between cycles (default: 60, or "
                        "LOOKOUT_INTERVAL_MINUTES from .env)")
    r.set_defaults(func=cmd_run)

    s = cmd("summarize", "summarize any web page ad hoc",
            'lookout summarize "https://www.yahoo.com/news/" --sentences 5 --email')
    s.add_argument("url", help="page URL (put it in quotes)")
    s.add_argument("--sentences", type=int, default=7, help="summary length (default: 7)")
    s.add_argument("--email", action="store_true", help="also mail the digest to EMAIL_TO")
    s.add_argument("--render-js", action="store_true",
                   help="force headless-browser rendering (usually automatic)")
    s.set_defaults(func=cmd_summarize)

    pr = cmd("price", "extract the current price of any product page ad hoc",
             'lookout price "https://www.otto.de/p/some-product/"')
    pr.add_argument("url", help="product page URL (put it in quotes)")
    pr.add_argument("--selector", default=None,
                    help='CSS selector of the price element, e.g. ".price-tag"')
    pr.add_argument("--render-js", action="store_true",
                    help="force headless-browser rendering (usually automatic)")
    pr.set_defaults(func=cmd_price)

    ls = cmd("list", "show everything configured in the watchlist",
             "lookout list")
    ls.add_argument("--watchlist", default=None, help="path to watchlist.yaml")
    ls.set_defaults(func=cmd_list)

    h = cmd("history", "show recorded price history + stats for a watched URL",
            'lookout history "https://www.otto.de/p/some-product/"')
    h.add_argument("url", help="the product URL exactly as it appears in watchlist.yaml")
    h.add_argument("--limit", type=int, default=20, help="rows to show (default: 20)")
    h.set_defaults(func=cmd_history)

    ex = cmd("export", "export recorded price history to a CSV file",
             "lookout export --out prices.csv")
    ex.add_argument("--out", default="price_history.csv", help="output CSV path")
    ex.add_argument("--url", default=None, help="only this item (default: all items)")
    ex.set_defaults(func=cmd_export)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    try:
        return args.func(args)
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2
    except (FetchError, PriceNotFoundError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except smtplib.SMTPAuthenticationError:
        print(
            "Email error: the mail server rejected the login.\n"
            "  * Check SMTP_USERNAME in .env (your full email address).\n"
            "  * For Gmail, SMTP_PASSWORD must be a 16-letter App Password\n"
            "    generated at https://myaccount.google.com/apppasswords\n"
            "    (NOT your normal Gmail password), pasted without spaces.",
            file=sys.stderr,
        )
        return 3
    except (smtplib.SMTPException, ConnectionError, TimeoutError, OSError) as exc:
        print(
            f"Email error: could not send via the configured SMTP server: {exc}\n"
            "  Check SMTP_HOST/SMTP_PORT in .env (Gmail: smtp.gmail.com / 587, "
            "SMTP_USE_TLS=true).",
            file=sys.stderr,
        )
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
