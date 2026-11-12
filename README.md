# 🏠 Otodom Analyzer

[![CI](https://github.com/michalw387/otodom-analyzer/actions/workflows/ci.yml/badge.svg)](https://github.com/michalw387/otodom-analyzer/actions/workflows/ci.yml)
![Python 3.14](https://img.shields.io/badge/python-3.14-blue)
![mypy](https://img.shields.io/badge/mypy-checked-2a6db2)
![ruff](https://img.shields.io/badge/lint-ruff-d7ff64)
![License: MIT](https://img.shields.io/badge/license-MIT-green)

**Stack:** Python 3.14 · Streamlit · SQLAlchemy 2.0 (SQLite) · APScheduler · Pydantic · curl_cffi + BeautifulSoup ·
Playwright · httpx · pandas · Claude Code CLI · pytest · mypy · ruff · pre-commit · GitHub Actions

A personal flat-hunting assistant. The app periodically checks new flat-for-sale listings on
[Otodom.pl](https://www.otodom.pl) (Poland's largest real-estate portal), downloads their full details and has
**Claude Code** (Anthropic) score each one against your criteria – on a 0–100 scale, with a list of pros,
cons/red flags, a summary and a check of whether the description is consistent with your filters. It notifies
you on your phone about the best offers and price drops right away.

> The user interface, AI prompts and notifications are in Polish, as the app targets the Polish market.
> Code, comments and logs are in English.

## Features

- **Otodom filters**: location (+ radius), price, area, number of rooms, max price per m², primary/secondary
  market – or any custom search URL copied from Otodom.
- **AI scoring criteria** with *Low / Medium / High / Critical* weights (editable table).
- **Automatic background scanning** (APScheduler) every 15 min – 12 h, plus a "scan and analyse now" button.
- **No duplicate work**: listings already stored in the database are never downloaded or sent to the AI again.
- **Notifications** (ntfy.sh, Discord, Telegram) about new offers scoring above a threshold (default 80) and
  about **price drops** of saved offers.
- **Duplicate detection** – the same flat listed by several agencies is shown as one card with a list of the
  related listings.
- **Distance and commute time** to a chosen address (work, school…) – on the cards, in exports and in the
  AI prompt.
- **No API key** – scoring is done by your installed, logged-in Claude Code within your subscription.
- **Daily AI token limit** – scanning will not burn through your whole subscription quota.
- **Dashboard**: sorting (score, price, price/m², date), minimum score filter, favourites, rejected,
  "contacted", archive of rejected offers, **export of selected offers to Excel**.
- **CLI scripts**: re-scoring offers and exporting reports to `.xlsx` / `.csv`.

## Requirements

- Python 3.14+
- [Claude Code](https://claude.com/claude-code) installed and logged in to an account with a Claude subscription
  (`claude auth login`). No API key is needed – without a working Claude Code, offers are downloaded but not
  scored.

## Installation

```bash
git clone https://github.com/michalw387/otodom-analyzer.git
cd otodom-analyzer
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# Optional: browser used as a fallback when Otodom blocks requests (HTTP 403)
playwright install chromium

claude auth login                  # if Claude Code is not logged in yet
cp .env.example .env               # optional: notifications, path to claude
```

Values in `.env`:

| Variable | Meaning |
|---|---|
| `CLAUDE_CLI_PATH` | optional: full path to `claude` when it is not on the app process's `PATH` |
| `NOTIFY_WEBHOOK_URL` | ntfy topic URL (`https://ntfy.sh/your-topic`) or a Discord webhook URL |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | Telegram bot (token from @BotFather) and chat ID |
| `OTODOM_DB_PATH` | optional: custom database path (default `data/otodom.db`) |

Notification settings can also be entered in the app (the **Ustawienia i Kryteria** / Settings & Criteria tab) –
a value saved in the app takes precedence over `.env`. `ANTHROPIC_API_KEY` from `.env` is ignored and never
passed to Claude Code, so scoring is never billed as paid API usage.

## Running

```bash
./start.sh
```

`start.sh` changes to the project directory, activates `.venv` and runs `streamlit run app.py`, so it works from
any directory (e.g. `~/projects/otodom-analyzer/start.sh`). Extra arguments are passed to Streamlit, e.g.
`./start.sh --server.port 8502`.

The app opens at <http://localhost:8501>. By default it listens on `localhost` only (`.streamlit/config.toml`) –
it runs Claude Code on your account and stores your notification credentials, so don't expose it to the network
without additional protection.

### First steps

1. Open **⚙️ Ustawienia i Kryteria** (Settings & Criteria).
2. Set the location – the part of an Otodom results URL after `/mieszkanie/`, e.g.
   `mazowieckie/warszawa/warszawa/warszawa/mokotow` or `malopolskie/krakow/krakow/krakow`.
   You can also paste a whole search URL into the **Własny URL** (custom URL) field – the other filters are then
   ignored.
3. Adjust the criteria and weights. Optionally enter a **destination address** and means of transport, and set up
   **notifications** (the **Przetestuj powiadomienie** button sends a test message without saving).
4. Choose the model, scan frequency and daily token limit, then click **Zapisz ustawienia** (Save settings).
   In the **🩺 Diagnostyka** (Diagnostics) section, **Sprawdź Claude Code** checks `claude --version` and the login.
5. Click **🔍 Skanuj i przeanalizuj teraz** (Scan and analyse now). Results appear in the
   **🏆 Najlepsze Oferty** (Best offers) tab.

## Notifications

| Channel | Setup |
|---|---|
| **ntfy.sh** | Install the ntfy app, subscribe to a hard-to-guess topic and enter `https://ntfy.sh/<topic>` (a self-hosted ntfy server works too). A topic on ntfy.sh is public to anyone who knows its name. |
| **Discord** | Channel settings → Integrations → Webhooks → copy the webhook URL. |
| **Telegram** | Create a bot with @BotFather (token), send it a message and read your chat ID (e.g. via @userinfobot). |

- **New offer**: sent when a freshly scored offer reaches the threshold. Contains the title, price, area, score,
  key pros and cons, and a link. Each flat is reported once – re-scoring it or the same offer from another agency
  does not trigger another notification.
- **Price drop**: "📉 Obniżka ceny! Z 700 000 zł na 650 000 zł" (price drop from 700,000 to 650,000 PLN). Prices of
  known offers are compared on every scan using the results list. In addition, **watched offers** (favourites,
  contacted, and those scoring at or above the notification threshold) that have dropped off the first result
  pages are checked directly – at most 10 per scan, each at most once a day.

## Duplicates

Two offers are considered the same flat when they are on the same floor, their area differs by at most 1 m², their
price differs by less than 5% and they are in the same place: on the same street (or, when one of them doesn't
give it, in the same district), in the same city and no more than 1.5 km apart (based on Otodom coordinates).
A group is shown as the card of the earliest found offer with an expandable list of the other listings (price,
score, link). Offers saved before this version have no street or district – their full location is compared
instead.

## Commute

Flat coordinates come from Otodom (some listings give an approximate location). The destination address is
geocoded with OpenStreetMap (Nominatim) only when the settings are saved. Distance is measured in a straight line
and the commute time is an **estimate** (route ≈ 1.3 × distance, average speed depending on the means of
transport) – it doesn't use timetables or navigation. The value is also included in the AI prompt, so after
changing the address it's worth using **Oceń ponownie wszystkie oferty** (re-score all offers).

## Token limit

Every Claude Code run is stored in the database with its token counts (input, output, cache). When today's usage
reaches the **daily limit** (default 100,000, `0` = no limit), scoring is paused until midnight and offers wait in
a queue. The limit is checked before each evaluation, so it can be exceeded by at most one request. Usage is
shown in the status bar.

## CLI scripts

```bash
# Re-score (e.g. after changing criteria or the prompt) – by default all non-rejected offers
python scripts/reevaluate.py
python scripts/reevaluate.py --unscored --limit 20 --yes
python scripts/reevaluate.py --ids 68562677 68559126 --model claude-haiku-5-5
python scripts/reevaluate.py --min-score 70 --notify --ignore-token-limit

# Export the best offers (default score ≥ 75 → reports/oferty-YYYY-MM-DD.xlsx)
python scripts/export_reports.py
python scripts/export_reports.py --min-score 60 -o oferty.csv
python scripts/export_reports.py --favorites --include-rejected
```

`reevaluate.py` respects the daily token limit (unless you pass `--ignore-token-limit`) and doesn't send
notifications by default (use `--notify` to enable them). Don't run it while the app is scanning – both processes
could score the same offers. CSV files use a semicolon separator and a decimal comma, so they open correctly in
Polish Excel. Listing texts in exports are protected against formula injection.

## How it works

1. **Search** – `scraper/otodom.py` builds a results URL sorted by newest and reads the data from the page's
   embedded `__NEXT_DATA__` JSON (Next.js). Requests are sent with `curl_cffi`, which reproduces the TLS
   fingerprint and headers of a real browser (Chrome / Edge / Safari / Firefox, picked at random per scan), with
   1.5–4 s delays, retries on 429/5xx and a Playwright fallback on 403. At most 2 result pages are checked; the
   scan stops early when a page contains nothing new.
2. **Price changes and new offers** – prices of known offers are compared with the results list (and re-checked
   for watched offers); new listings (at most 30 per scan) are downloaded in full.
3. **Duplicates** – after each scan, duplicate groups are recalculated for the whole database.
4. **AI scoring** – `ai/evaluator.py` runs `claude -p` (Claude Code in non-interactive mode) with a system prompt
   containing the criteria and the listing passed on stdin. The answer must be a plain JSON object – enforced by
   the `--json-schema` option and Pydantic validation. Claude Code runs **without any tools** (`--tools ""`),
   hooks, plugins or MCP servers, because listing descriptions are written by strangers: a description must not
   be able to make Claude do anything on your computer (which is why the app never uses
   `--dangerously-skip-permissions`). A failure for a single offer is retried on later scans (at most 3 attempts);
   a missing Claude Code, a missing login or an exhausted subscription quota stop scoring until the next scan.
5. **Notifications** – see above.

The scan logic lives in `services/scanner.py` and is shared by the scheduler and the scripts. The scheduler runs
inside the Streamlit process – scanning continues as long as `streamlit run` is running. Run one app instance per
database. A database from an earlier version is upgraded automatically on startup (new columns and tables)
without data loss.

## Models and subscription limits

| Model | ID (`claude --model`) |
|---|---|
| Claude Sonnet 5.5 (default) | `claude-sonnet-5-5` |
| Claude Haiku 5.5 (cheaper) | `claude-haiku-5-5` |

Scoring isn't billed separately – it uses your Claude subscription quota (the same one you use in Claude Code).
One evaluation takes about 10–15k tokens (Claude Code adds its own context) and about 10 s on Sonnet. On the Pro
plan, scoring many offers can quickly use up the usage window – the app then pauses scoring until the next scan.
Haiku uses the quota more slowly. The daily token limit (default 100,000, i.e. about 8–10 evaluations) further
caps usage by scanning.

## Tests, type checking and the pre-commit hook

```bash
python -m pytest     # tests never contact Otodom or Nominatim and never run the real claude
mypy                 # type check (configured in mypy.ini)

.venv/bin/pre-commit install            # once after cloning the repository
.venv/bin/pre-commit run --all-files    # run all hooks manually
```

Before every `git commit` the following run automatically ([pre-commit](https://pre-commit.com/), configured in
`.pre-commit-config.yaml`):

- file hygiene: trailing whitespace, final newline, LF line endings, valid YAML/TOML, merge conflict markers,
  large files, private keys;
- **ruff** – linting (rules in `ruff.toml`), with automatic fixes where safe;
- **secret blocking** – a commit containing a `.env` file, an SQLite database (`*.db`) or an Anthropic API key
  (`sk-ant-…`) is rejected;
- **mypy** and **pytest** – run from `.venv`, only when the commit contains Python files.

Hooks from external repositories have pinned versions; to update them: `.venv/bin/pre-commit autoupdate`.

The same hooks run on **GitHub Actions** (`.github/workflows/ci.yml`) on every push to `main` and every pull
request.

## Project structure

```text
otodom-analyzer/
├── CLAUDE.md             # Project rules for Claude Code
├── config.py             # Configuration, environment variables, defaults
├── database.py           # SQLite database, SQLAlchemy models, migrations, queries
├── formatting.py         # Polish formatting of amounts, numbers and plurals
├── scraper/
│   ├── otodom.py         # URL building, parsing of results and listings (__NEXT_DATA__)
│   └── utils.py          # curl_cffi, retries, Playwright fallback
├── ai/
│   ├── evaluator.py      # Claude Code CLI (claude -p), JSON answer, tokens, errors, diagnostics
│   └── prompts.py        # System prompt and listing prompt
├── services/
│   ├── scanner.py        # One scan: search, prices, duplicates, AI, notifications
│   ├── duplicates.py     # Duplicate offer detection
│   ├── geo.py            # Destination geocoding, distance and estimated commute
│   └── export.py         # Excel / CSV export
├── notifications/
│   └── webhook.py        # ntfy.sh, Discord, Telegram
├── scripts/
│   ├── reevaluate.py     # Re-scoring saved offers with AI
│   └── export_reports.py # Exporting the best offers to .xlsx / .csv
├── scheduler.py          # APScheduler: periodic and manual scans
├── app.py                # Streamlit UI
├── start.sh              # App launcher (venv + streamlit run)
├── tests/                # pytest tests
├── .github/workflows/ci.yml  # CI: pre-commit (ruff, mypy, pytest) on GitHub Actions
├── .streamlit/config.toml
├── .env.example
├── .pre-commit-config.yaml
├── mypy.ini
├── ruff.toml
├── requirements.txt
├── README.md
└── LICENSE
```

## Notes

- **Use it responsibly.** Otodom's terms of service may restrict automated downloading of content. The app is
  intended for personal use – don't shorten the interval beyond what you need and don't raise the page limits.
- **Changes on Otodom's side.** If Otodom changes its page structure or starts blocking requests, a scan error
  appears in the status bar (e.g. "brak danych `__NEXT_DATA__`" – missing `__NEXT_DATA__` data – or "HTTP 403").
  On 403, install Chromium for Playwright (`playwright install chromium`).
- **Nominatim** (OpenStreetMap) is a free service limited to 1 request per second – the app geocodes only the
  destination address and only when it changes.
- The AI score is an aid, not an oracle – always check the listing, the land and mortgage register
  (*księga wieczysta*) and the flat in person.

## License

[MIT](LICENSE)
