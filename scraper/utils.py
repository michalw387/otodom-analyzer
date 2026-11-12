"""Scraper HTTP layer: browser TLS impersonation (curl_cffi), retries and a Playwright fallback."""

from __future__ import annotations

import logging
import random
import time

from curl_cffi import requests as curl_requests
from curl_cffi.requests.exceptions import RequestException

import config

log = logging.getLogger(__name__)

# curl_cffi reproduces the TLS/HTTP2 fingerprint *and* the matching headers (incl. User-Agent) of a
# real browser, so we rotate whole browser profiles per session instead of bare User-Agent strings.
IMPERSONATE_TARGETS = ("chrome", "edge", "safari", "firefox")
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/141.0.0.0 Safari/537.36"
)
EXTRA_HEADERS = {"Accept-Language": "pl-PL,pl;q=0.9,en-US;q=0.6,en;q=0.5"}
RETRY_STATUSES = {429, 500, 502, 503, 504}
MAX_ATTEMPTS = 3
MAX_BACKOFF_SECONDS = 60


class ScraperError(Exception):
    """Failure fetching or parsing Otodom data."""


class FetchError(ScraperError):
    """A page could not be fetched."""


def create_client() -> curl_requests.Session:
    return curl_requests.Session(
        impersonate=random.choice(IMPERSONATE_TARGETS),
        timeout=30,
        allow_redirects=True,
        headers=EXTRA_HEADERS,
    )


def fetch_html(client: curl_requests.Session, url: str) -> str:
    """Fetch a page with a random delay, retries on 429/5xx and Playwright when blocked (403)."""
    time.sleep(random.uniform(*config.REQUEST_DELAY_SECONDS))
    last_error = ""
    for attempt in range(1, MAX_ATTEMPTS + 1):
        retry_after = ""
        try:
            response = client.get(url)
        except RequestException as exc:
            last_error = f"błąd sieci: {exc}"
        else:
            if response.status_code == 200:
                return response.text
            if response.status_code == 403:
                log.warning("Otodom returned 403 for %s – retrying with Playwright", url)
                return fetch_with_browser(url)
            if response.status_code not in RETRY_STATUSES:
                raise FetchError(f"HTTP {response.status_code}: {url}")
            last_error = f"HTTP {response.status_code}"
            retry_after = response.headers.get("Retry-After") or ""
        if attempt < MAX_ATTEMPTS:
            delay = float(retry_after) if retry_after.isdigit() else 2**attempt + random.uniform(0, 1)
            log.info("%s for %s – retrying in %.0f s", last_error, url, delay)
            time.sleep(min(delay, MAX_BACKOFF_SECONDS))
    raise FetchError(f"{last_error} po {MAX_ATTEMPTS} próbach: {url}")


def fetch_with_browser(url: str) -> str:
    """Fetch a page with a real headless Chromium – used when plain requests get blocked."""
    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise FetchError("Strona zablokowała zapytanie, a Playwright nie jest zainstalowany") from exc

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                page = browser.new_page(user_agent=BROWSER_USER_AGENT, locale="pl-PL")
                page.goto(url, wait_until="domcontentloaded", timeout=45_000)
                page.wait_for_selector("script#__NEXT_DATA__", state="attached", timeout=15_000)
                return page.content()
            finally:
                browser.close()
    except PlaywrightError as exc:
        raise FetchError(f"Playwright nie pobrał strony {url}: {exc.message}") from exc
