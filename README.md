# Lookout

**Watch any web page and get an email when something changes** — price drops,
restocks, keywords appearing, content edits, or a summarized digest.

Point it at product pages on shopping sites and set a target price — the moment an
item drops to or below your target, you get an email. Beyond prices, it watches
anything: sold-out items coming back in stock, keywords appearing on (or vanishing
from) any page, meaningful content changes with a diff in your inbox, and summarized
digests of pages you follow.

Built in pure Python: `requests` + `BeautifulSoup` for scraping, Playwright as an
automatic fallback for bot-protected shops, SQLite for history, `smtplib` for mail,
`APScheduler` for polling.

---

## Features

**Price alerts**

- Watches any number of product pages defined in a simple YAML watchlist.
- Five-rung price extraction ladder — tries the most reliable source first:
  1. an explicit CSS selector you provide (optional),
  2. structured data (**JSON-LD**, schema.org `Product`/`Offer`/`ProductGroup` —
     verified live against otto.de, Zalando, MediaMarkt, IKEA, H&M),
  3. price **meta tags** / microdata (`og:price:amount`, `itemprop="price"`, ...),
  4. **DOM heuristics** that prefer currency-adjacent numbers and skip
     struck-through "UVP/RRP" prices, "-23%" badges, countdown timers, and
     sponsored 0,00-€ placeholders (traps captured from live Amazon/otto/AliExpress pages),
  5. **JS state objects** — prices embedded in inline script data for app-style shops.
- Handles localized formats: `$1,299.99`, `1.299,99 €`, `1299,-`, `2 499,00 kr`.
- Full **price history** in SQLite; alert mails show previous price, % drop, and
  the lowest price ever seen. Export everything to CSV.
- **Smart alert dedup**: re-alerts only on a further N% drop or after a cooldown;
  re-arms automatically when the price recovers above target.

**More monitors**

- **Back in stock**: alerts on the sold-out → available transition
  (schema.org availability + DE/EN text markers). Never mails on first sight.
- **Keyword watch**: a word/phrase *appears on* or *disappears from* any page —
  concert tickets, job postings, "available now".
- **Page-change monitor**: mails a colored diff excerpt when a page's content
  changes by more than a configurable percentage. Small edits accumulate until
  they cross the threshold together.
- **Summary digests**: local extractive summarization (no API keys) of pages you
  follow — one combined digest mail per polling cycle. News-portal front pages
  are digested via their headlines automatically.

**Operations**

- **Bot-protection aware**: realistic browser headers, detection of blocking
  status codes *and* captcha pages served as 200s, automatic headless-Chromium
  fallback — this is what makes otto.de, Amazon & AliExpress work in practice.
- `lookout run` polls on a schedule; `lookout check` is a single cycle for
  cron / Windows Task Scheduler; `--dry-run` logs instead of mailing.
- Failures on one item never block the rest; fetches retry with backoff.
- Branded, mobile-friendly HTML mails (table-based layout, inline styles) with
  plain-text alternatives; credentials live in `.env` (gitignored).

---

## Architecture

```
watchlist.yaml               .env (SMTP credentials)
     │                            │
     ▼                            ▼
┌──────────┐  HTML  ┌───────────────────┐
│ fetcher   │ ─────► │ extractors        │      ┌──────────────┐     ┌──────────┐
│ realistic │        │ price_parser (5-  │ ───► │ decision     │ ──► │ emailer   │──► 📧
│ headers,  │        │ rung ladder)      │      │ engines      │     │ branded   │
│ bot-wall  │        │ watchers (stock/  │      │ alerts.py +  │     │ HTML +    │
│ detection,│        │ keyword/change)   │      │ transition   │     │ plaintext │
│ Chromium  │        │ summarizer        │      │ rules        │     └──────────┘
│ fallback  │        └───────────────────┘      └──────┬───────┘
└──────────┘                                           │
     ▲                                          ┌──────▼───────┐
     └── runner.py orchestrates one cycle ────  │ storage      │
         cli.py is the entry point              │ SQLite:      │
                                                │ history +    │
                                                │ alert/watch  │
                                                │ state        │
                                                └──────────────┘
```

Every module is a single responsibility with pure, unit-testable decision
functions; `runner.py` wires one polling cycle and isolates per-item failures.

---

## Quick start

**One-shot setup** (creates a virtual environment, installs everything including
the headless browser, and generates your starter config files):

```
setup.bat        (Windows -- double-click it, or run it in PowerShell)
./setup.sh       (Linux / macOS)
```

Then edit the two files it created — `.env` (your mail credentials) and
`watchlist.yaml` (what to watch) — activate the environment, and go:

```bash
.venv\Scripts\activate      # Windows   (Linux/macOS: source .venv/bin/activate)
lookout check --dry-run  # test without sending anything
lookout check            # one real cycle now
lookout run              # keep polling (default: every 60 min)
```

<details><summary>Manual setup instead</summary>

```bash
pip install -e .                           # Python 3.10+
playwright install chromium                # one-time: browser for protected shops
cp .env.example .env                       # fill in SMTP credentials
cp watchlist.example.yaml watchlist.yaml   # add items to watch
```
</details>

### Ad-hoc commands

```bash
lookout price https://shop.example/product/123        # what price does it see?
lookout price URL --selector ".price-tag"             # debug a custom selector
lookout summarize https://news.example/article        # print a summary
lookout summarize URL --sentences 5 --email           # ...and mail it
lookout list                                          # show the watchlist
lookout history URL                                   # price history + min/max/avg
lookout export --out prices.csv                       # full history as CSV
```

### Watchlist reference

```yaml
watches:              # price alerts
  - name: "Sony WH-1000XM6"
    url: "https://www.example-shop.de/sony-wh-1000xm6"
    target_price: 279.00

stock_watches:        # back-in-stock alerts
  - name: "Limited sneaker"
    url: "https://www.example-shop.de/sneaker-drop"

keyword_watches:      # keyword appears/disappears
  - name: "Tour tickets"
    url: "https://www.example-tickets.de/tour"
    keyword: "tickets available"
    trigger: "appears"

change_watches:       # page-change monitor with diff mail
  - name: "Competitor pricing"
    url: "https://competitor.example/pricing"
    min_change_percent: 5

summaries:            # combined digest mail per cycle
  - name: "Tech news"
    url: "https://news.example/"
    max_sentences: 7
```

### Scheduling without the daemon

```
# cron (Linux/macOS): every hour
0 * * * * cd /path/to/price-watch && /usr/bin/python -m lookout.cli check

# Windows Task Scheduler: run `python -m lookout.cli check`
# with "Start in" set to the project folder.
```

---

## Design decisions

- **Extraction ladder over per-shop scrapers.** Site-specific scrapers break
  weekly. Structured data is stable because search engines consume it; DOM
  heuristics and JS-state parsing are ordered fallbacks, and the ladder rung
  that matched is logged with every price.
- **Transition-based alerting.** All monitors are pure decision functions
  (`alerts.evaluate`, `watchers.evaluate_*`) that map (new observation,
  stored state) → (alert?, reason). First observations never alert; the reason
  string is shown in the mail and the logs.
- **Verified against real shops.** Page structures for the parser tests were
  captured live from otto.de, Amazon, Zalando, MediaMarkt, IKEA, H&M, and
  AliExpress — including the traps: ProductGroup-nested prices, countdown text
  glued to prices, UVP/RRP labels, sponsored 0,00-€ placeholders, and
  app-shell pages whose static HTML contains no price at all.
- **Extractive summarization on purpose.** Frequency-based sentence ranking is
  transparent, free, offline; swapping in an LLM later is a one-file change.
- **SQLite over flat files.** History enables "lowest seen"/"was X, −9%"
  context in alerts, stats, and CSV export at zero operational cost.

## Troubleshooting

- **"HTTP 400/403 (bot protection)" or a captcha error** — make sure the
  browser fallback is available: `pip install playwright` then
  `playwright install chromium` (once). It's used automatically from then on.
- **"No price found"** — some shops (e.g. AliExpress) ship *empty* static HTML;
  lookout retries with the headless browser automatically if installed.
  Still failing? Right-click the price → Inspect, and pass the CSS class via
  `selector:` — test with `lookout price <URL> --selector ".my-class"`.
- **News front pages** summarize via headlines instead of prose. That's intended.

## Testing

```bash
pip install -e .[dev]
pytest
```

81 unit tests cover the extraction ladder (all five rungs, localized formats,
live-captured shop structures), the fetcher (bot walls, captcha pages, rendering
fallback), all four alert decision engines (dedup, cooldowns, transitions,
diff thresholds), storage, CSV export, the summarizer, and every email template.

## Responsible scraping

lookout is designed for low-frequency personal polling (default: hourly).
Check a site's Terms of Service and `robots.txt` before watching it, and keep
polling intervals reasonable.

## License

MIT
