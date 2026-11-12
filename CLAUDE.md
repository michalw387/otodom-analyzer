# Otodom Analyzer – instructions for Claude Code

Personal assistant that monitors Otodom.pl flat listings, scores them with Claude and notifies the buyer.
**Language:** UI texts, LLM prompts and user-facing messages are Polish; code, comments, docstrings and
log messages are English.

## Tech stack

- Python 3.14+ (virtualenv in `.venv/`)
- Streamlit – UI (`app.py`); APScheduler – background scans inside the Streamlit process
- SQLite + SQLAlchemy 2.0 ORM (`database.py`)
- Claude Code CLI (`claude -p`, the user's logged-in subscription – no API key, no Anthropic SDK); answers
  validated with `--json-schema` + Pydantic
- curl_cffi – Otodom scraping with browser TLS impersonation; BeautifulSoup4; Playwright only as a 403 fallback
- httpx2 – webhooks; geopy + Nominatim; pandas + openpyxl
- pytest, mypy, ruff (lint) and pre-commit (git hooks)

## Commands

```bash
source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py                    # UI on http://localhost:8501 (localhost only)
python -m pytest                        # all tests – no network, no real `claude` runs
mypy                                    # type check, configured in mypy.ini
.venv/bin/pre-commit install            # once per clone: enable the git pre-commit hook
.venv/bin/pre-commit run --all-files    # ruff + hygiene + secret checks + mypy + pytest on tracked files
python scripts/reevaluate.py --help     # re-score saved listings
python scripts/export_reports.py --help # export best listings to .xlsx / .csv
```

## Layout

- `scraper/otodom.py` – search URLs and parsing of the `__NEXT_DATA__` JSON; `scraper/utils.py` – HTTP
  (curl_cffi session per scan), retries, Playwright fallback
- `ai/prompts.py` – the system prompt is prompt-cached: keep it identical for all listings of a scan and put
  per-listing data in the listing prompt (stdin); `ai/evaluator.py` – `claude -p` run, JSON parsing, error
  classification, token count, `cli_status()` diagnostics
- `services/scanner.py` – one scan: search → price changes → new listings → duplicates → AI → notifications.
  Shared by `scheduler.py` and `scripts/`; put scan logic here, not in the UI or the scheduler
- `services/duplicates.py`, `services/geo.py`, `services/export.py`, `notifications/webhook.py`
- `formatting.py` – Polish money/number/plural formatting; reuse it instead of re-implementing
- `config.py` – defaults, limits, env fallbacks; `database.py` – models, migrations, all queries

## Coding rules

- **Strict typing.** Every function and method has annotated parameters and return type; `mypy` must report
  no errors. Prefer precise types (`Literal`, dataclasses, Pydantic models, TypedDicts)
  over `Any` and loose dicts.
- **Scraper error handling.** Raise `ScraperError` subclasses (`FetchError`, `ParseError`) with a Polish message.
  A failing page or listing must never crash a scan – record it in `ScanStats.problems`. Read Otodom data from
  `__NEXT_DATA__`, not from the HTML layout. Keep the request delays and per-scan limits from `config.py`.
- **AI (Claude Code CLI) error handling.** In `ai/evaluator.py` classify every outcome of the `claude -p` run:
  account-wide failures (CLI missing, not logged in, unknown model, usage/rate limit, 5xx, crash without JSON)
  → `FatalEvaluationError` (stop the batch, keep the listing's attempts); per-listing failures (timeout, refusal,
  invalid JSON, other CLI errors) → `EvaluationError` (counts an attempt). Read `is_error`/`api_error_status`
  and `stop_reason` before using the output, and record the tokens of every run so the daily limit stays accurate.
- **Running Claude Code safely.** Listing texts are untrusted, so the CLI always runs with `--tools ""`,
  `--restricted`, `--strict-mcp-config` and `--disable-slash-commands`, from a neutral working directory, with
  the listing on stdin. Never add `--dangerously-skip-permissions` or tools, never use `--bare` (it ignores the
  subscription login), and keep `ANTHROPIC_API_KEY` stripped from the CLI environment (no paid API billing).
- **Untrusted content.** Listing texts come from third parties: they are data, not instructions, in prompts;
  escape them with `md()` in Streamlit markdown; send notifications as plain text; keep exports formula-safe.
- **Secrets.** Never put API keys, bot tokens or webhook URLs into logs or error messages (the `httpx2` logger
  stays at WARNING because Telegram URLs contain the token).
- **Backward compatibility.** New DB columns must be nullable – `_add_missing_columns()` adds them to existing
  databases automatically. New settings need defaults in `config.DEFAULT_SETTINGS`. Don't rename or drop
  columns or settings keys without an explicit migration.
- **Lint.** ruff rules live in `ruff.toml` (line length 120). Polish typography (–, „”) is allowed; write
  non-breaking spaces as the `\u00a0` escape, never as a literal invisible character.
- **Models.** IDs live in `config.AI_MODELS` (`claude-sonnet-5-5`, `claude-haiku-5-5`). Don't send sampling
  parameters (`temperature`, `top_p`, `top_k`) – current models reject non-default values.

## Tests – required before finishing

- Add or update tests in `tests/` for every change. Tests must not hit the network, Otodom, Nominatim or the
  Claude Code CLI, and must not touch `data/otodom.db`: use the `db` / `make_listing` fixtures, fakes,
  `httpx2.MockTransport` and `monkeypatch` (`tests/test_ai.py` replaces `subprocess.run` with a fake).
- **A task is finished only when `python -m pytest` and `mypy` both pass** (plus `pre-commit run` before a
  commit). If something fails or could not be run, say so explicitly instead of reporting success.
- Every commit goes through the pre-commit hook (ruff, file hygiene, blocking of `.env`/database files and
  Anthropic API keys, mypy, pytest). Fix the cause of a failing hook – never bypass it with `--no-verify`.
