"""Email delivery: branded HTML mails over SMTP (Gmail app password, etc.).

Every mail type is a pure `build_*` function returning (subject, plain, html)
-- fully unit-testable without a mail server -- plus a thin `send_*` wrapper.
Layout uses table-based HTML with inline styles only, which is what renders
consistently across Gmail, Outlook, and mobile clients.
"""

from __future__ import annotations

import html as _html
import logging
import smtplib
from datetime import datetime
from email.message import EmailMessage
from urllib.parse import urlparse

from .config import SmtpConfig, WatchItem
from .summarizer import PageSummary

log = logging.getLogger(__name__)

# --- palette -------------------------------------------------------------------

_GREEN = "#0f7b4f"
_BLUE = "#2456c9"
_AMBER = "#b45309"
_INK = "#1f2937"
_MUTED = "#6b7280"
_BG = "#f3f4f6"
_CARD = "#ffffff"
_HEADER_BG = "#111827"

_FONT = "-apple-system,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"


def _e(text) -> str:
    return _html.escape(str(text))


def _host(url: str) -> str:
    return urlparse(url).netloc.removeprefix("www.")


def _shell(badge: str, badge_color: str, preheader: str, inner: str) -> str:
    """Shared outer frame: hidden preheader, dark header bar, white card, footer."""
    stamp = datetime.now().strftime("%d.%m.%Y %H:%M")
    return f"""\
<!DOCTYPE html>
<html><body style="margin:0;padding:0;background:{_BG};">
<div style="display:none;max-height:0;overflow:hidden;">{_e(preheader)}</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:{_BG};padding:24px 8px;">
<tr><td align="center">
  <table role="presentation" width="600" cellpadding="0" cellspacing="0"
         style="max-width:600px;width:100%;background:{_CARD};border-radius:10px;overflow:hidden;
                font-family:{_FONT};color:{_INK};">
    <tr><td style="background:{_HEADER_BG};padding:14px 24px;">
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>
        <td style="font-family:{_FONT};color:#ffffff;font-size:17px;font-weight:700;letter-spacing:.3px;">
          &#128200; lookout</td>
        <td align="right"><span style="font-family:{_FONT};background:{badge_color};color:#ffffff;font-size:11px;
          font-weight:700;letter-spacing:.8px;padding:4px 10px;border-radius:999px;">{_e(badge)}</span></td>
      </tr></table>
    </td></tr>
    <tr><td style="padding:26px 28px 8px 28px;">{inner}</td></tr>
    <tr><td style="padding:18px 28px 22px 28px;">
      <p style="margin:0;font-size:12px;color:{_MUTED};border-top:1px solid #e5e7eb;padding-top:14px;">
        Sent automatically by lookout &middot; {stamp} &middot;
        edit <code style="font-size:11px;">watchlist.yaml</code> to change what is monitored.</p>
    </td></tr>
  </table>
</td></tr>
</table>
</body></html>"""


def _button(url: str, label: str, color: str = _GREEN) -> str:
    return (f'<p style="margin:20px 0 8px 0;"><a href="{_e(url)}" '
            f'style="display:inline-block;background:{color};color:#ffffff;font-family:{_FONT};'
            f'font-size:14px;font-weight:600;padding:11px 22px;border-radius:8px;'
            f'text-decoration:none;">{_e(label)}</a></p>')


def _kv_row(label: str, value_html: str) -> str:
    return (f'<tr><td style="padding:5px 16px 5px 0;font-size:14px;color:{_MUTED};'
            f'white-space:nowrap;">{_e(label)}</td>'
            f'<td style="padding:5px 0;font-size:14px;color:{_INK};">{value_html}</td></tr>')


# --- price alert ----------------------------------------------------------------


def build_price_alert(item: WatchItem, price: float, lowest_seen: float | None,
                      prev_price: float | None, reason: str) -> tuple[str, str, str]:
    cur = item.currency
    below = item.target_price - price
    below_pct = (below / item.target_price * 100) if item.target_price else 0.0

    subject = f"↓ {item.name} — {price:.2f} {cur} (target {item.target_price:.2f})"
    preheader = f"{item.name} dropped to {price:.2f} {cur} - {below:.2f} under your target."

    drop_html = ""
    drop_plain = ""
    if prev_price is not None and prev_price > price:
        pct = (prev_price - price) / prev_price * 100
        drop_html = (f'<span style="color:{_MUTED};font-size:15px;"> was '
                     f'<s>{prev_price:.2f} {_e(cur)}</s> '
                     f'<b style="color:{_GREEN};">&minus;{pct:.0f}%</b></span>')
        drop_plain = f" (was {prev_price:.2f} {cur}, -{pct:.0f}%)"

    rows = [
        _kv_row("Your target", f"<b>{item.target_price:.2f} {_e(cur)}</b>"
                               f' <span style="color:{_GREEN};">('
                               f"{below:.2f} {_e(cur)} / {below_pct:.0f}% under)</span>"),
    ]
    if lowest_seen is not None:
        marker = " &mdash; new low!" if price <= lowest_seen else ""
        rows.append(_kv_row("Lowest seen", f"<b>{lowest_seen:.2f} {_e(cur)}</b>{marker}"))
    rows.append(_kv_row("Trigger", _e(reason)))

    inner = f"""
      <h1 style="margin:0 0 2px 0;font-size:20px;line-height:1.35;">{_e(item.name)}</h1>
      <p style="margin:0 0 18px 0;font-size:13px;color:{_MUTED};">{_e(_host(item.url))}</p>
      <p style="margin:0 0 14px 0;">
        <span style="font-size:34px;font-weight:800;color:{_GREEN};">{price:.2f}&nbsp;{_e(cur)}</span>
        {drop_html}</p>
      <table role="presentation" cellpadding="0" cellspacing="0">{''.join(rows)}</table>
      {_button(item.url, "Open product page")}
    """

    plain = (
        f"Price alert: {item.name}\n"
        f"Current price: {price:.2f} {cur}{drop_plain}\n"
        f"Your target:   {item.target_price:.2f} {cur} "
        f"({below:.2f} {cur} / {below_pct:.0f}% under)\n"
        + (f"Lowest seen:   {lowest_seen:.2f} {cur}\n" if lowest_seen is not None else "")
        + f"Trigger:       {reason}\n"
        f"Link:          {item.url}\n"
    )
    return subject, plain, _shell("PRICE ALERT", _GREEN, preheader, inner)


# --- back in stock -----------------------------------------------------------------


def build_stock_alert(name: str, url: str) -> tuple[str, str, str]:
    subject = f"✓ Back in stock: {name}"
    preheader = f"{name} is available again on {_host(url)}."
    inner = f"""
      <h1 style="margin:0 0 2px 0;font-size:20px;line-height:1.35;">{_e(name)}</h1>
      <p style="margin:0 0 18px 0;font-size:13px;color:{_MUTED};">{_e(_host(url))}</p>
      <p style="margin:0;font-size:17px;">This item is <b style="color:{_GREEN};">available again</b>.
         Popular items sell out fast &mdash; grab it while it lasts.</p>
      {_button(url, "Open product page")}
    """
    plain = f"Back in stock: {name}\n{url}\n"
    return subject, plain, _shell("BACK IN STOCK", _GREEN, preheader, inner)


# --- keyword alert -----------------------------------------------------------------


def build_keyword_alert(name: str, url: str, keyword: str,
                        trigger: str) -> tuple[str, str, str]:
    verb = "appeared on" if trigger == "appears" else "disappeared from"
    subject = f"⚡ “{keyword}” {verb} {name}"
    preheader = f"Your watched keyword '{keyword}' {verb} {_host(url)}."
    inner = f"""
      <h1 style="margin:0 0 2px 0;font-size:20px;line-height:1.35;">{_e(name)}</h1>
      <p style="margin:0 0 18px 0;font-size:13px;color:{_MUTED};">{_e(_host(url))}</p>
      <p style="margin:0;font-size:16px;">The keyword
        <span style="background:#eef2ff;color:{_BLUE};font-weight:700;padding:2px 8px;
        border-radius:6px;">{_e(keyword)}</span> has <b>{_e(verb)}</b> this page.</p>
      {_button(url, "Open page", _BLUE)}
    """
    plain = f"Keyword watch: '{keyword}' {verb} {name}\n{url}\n"
    return subject, plain, _shell("KEYWORD", _BLUE, preheader, inner)


# --- page changed --------------------------------------------------------------------


def build_change_alert(name: str, url: str, change_percent: float,
                       diff_excerpt: str) -> tuple[str, str, str]:
    subject = f"✎ Page changed (~{change_percent:.0f}%): {name}"
    preheader = f"{name} changed by about {change_percent:.0f}% since the last snapshot."

    diff_rows = []
    for line in diff_excerpt.splitlines():
        added = line.startswith("+")
        color = _GREEN if added else "#b91c1c"
        bg = "#ecfdf5" if added else "#fef2f2"
        diff_rows.append(
            f'<div style="background:{bg};color:{color};font-size:13px;'
            f'font-family:Consolas,Menlo,monospace;padding:4px 10px;border-radius:4px;'
            f'margin:2px 0;">{_e(line)}</div>'
        )
    diff_html = "".join(diff_rows) or f'<p style="color:{_MUTED};">(diff unavailable)</p>'

    inner = f"""
      <h1 style="margin:0 0 2px 0;font-size:20px;line-height:1.35;">{_e(name)}</h1>
      <p style="margin:0 0 18px 0;font-size:13px;color:{_MUTED};">{_e(_host(url))}</p>
      <p style="margin:0 0 12px 0;font-size:16px;">Content changed by about
        <b style="color:{_AMBER};">{change_percent:.0f}%</b> since the last snapshot:</p>
      {diff_html}
      {_button(url, "Open page", _AMBER)}
    """
    plain = (f"Page changed (~{change_percent:.0f}%): {name}\n{url}\n\n"
             f"{diff_excerpt}\n")
    return subject, plain, _shell("PAGE CHANGED", _AMBER, preheader, inner)


# --- summary digest (one mail per cycle, all pages combined) --------------------------


def build_digest(entries: list[tuple[str, str, PageSummary]]) -> tuple[str, str, str]:
    """entries: (name, url, summary) per subscribed page."""
    n = len(entries)
    first = entries[0][0] if entries else ""
    subject = (f"✉ Your digest: {first}" if n == 1
               else f"✉ Your digest: {first} + {n - 1} more")
    preheader = "Summaries of " + ", ".join(name for name, _, _ in entries[:3])

    sections = []
    for i, (name, url, summary) in enumerate(entries):
        bullets = "".join(
            f'<li style="margin:0 0 9px 0;font-size:14px;line-height:1.55;">{_e(s)}</li>'
            for s in summary.sentences
        ) or '<li style="color:#6b7280;">(No readable text found on this page.)</li>'
        divider = ('<tr><td style="border-top:1px solid #e5e7eb;padding-top:20px;"></td></tr>'
                   if i else "")
        sections.append(f"""
          <table role="presentation" width="100%" cellpadding="0" cellspacing="0">{divider}
          <tr><td>
            <h2 style="margin:0 0 2px 0;font-size:17px;">
              <a href="{_e(url)}" style="color:{_INK};text-decoration:none;">{_e(name)}</a></h2>
            <p style="margin:0 0 10px 0;font-size:12px;color:{_MUTED};">
              {_e(_host(url))} &middot; {_e(summary.title)} &middot;
              ~{summary.word_count} words read</p>
            <ul style="margin:0 0 16px 0;padding-left:20px;">{bullets}</ul>
          </td></tr></table>""")

    inner = "".join(sections)
    plain_parts = []
    for name, url, summary in entries:
        plain_parts.append(f"## {name}\n{url}\n"
                           + "\n".join(f"- {s}" for s in summary.sentences))
    plain = "\n\n".join(plain_parts) + "\n"
    return subject, plain, _shell("DIGEST", _BLUE, preheader, inner)


# --- send wrappers ---------------------------------------------------------------------


def send_price_alert(cfg: SmtpConfig, item: WatchItem, price: float,
                     lowest_seen: float | None, reason: str,
                     prev_price: float | None = None) -> None:
    _send(cfg, *build_price_alert(item, price, lowest_seen, prev_price, reason))


def send_stock_alert(cfg: SmtpConfig, name: str, url: str) -> None:
    _send(cfg, *build_stock_alert(name, url))


def send_keyword_alert(cfg: SmtpConfig, name: str, url: str,
                       keyword: str, trigger: str) -> None:
    _send(cfg, *build_keyword_alert(name, url, keyword, trigger))


def send_change_alert(cfg: SmtpConfig, name: str, url: str,
                      change_percent: float, diff_excerpt: str) -> None:
    _send(cfg, *build_change_alert(name, url, change_percent, diff_excerpt))


def send_digest(cfg: SmtpConfig, entries: list[tuple[str, str, PageSummary]]) -> None:
    _send(cfg, *build_digest(entries))


def send_summary_digest(cfg: SmtpConfig, name: str, url: str, summary: PageSummary) -> None:
    """Single-page digest (used by `lookout summarize URL --email`)."""
    send_digest(cfg, [(name, url, summary)])


def _send(cfg: SmtpConfig, subject: str, plain: str, html_body: str) -> None:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = cfg.email_from
    msg["To"] = cfg.email_to
    msg.set_content(plain)
    msg.add_alternative(html_body, subtype="html")

    log.info("Sending mail via %s:%d -> %s (%s)", cfg.host, cfg.port, cfg.email_to, subject)
    if cfg.use_tls:
        with smtplib.SMTP(cfg.host, cfg.port, timeout=30) as server:
            server.starttls()
            server.login(cfg.username, cfg.password)
            server.send_message(msg)
    else:
        with smtplib.SMTP_SSL(cfg.host, cfg.port, timeout=30) as server:
            server.login(cfg.username, cfg.password)
            server.send_message(msg)
