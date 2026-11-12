from __future__ import annotations

import contextlib
from datetime import datetime, timedelta
from typing import Any, ClassVar

import pytest

import database
import scheduler
from ai.evaluator import ConsistencyCheck, Evaluation, EvaluationError, EvaluationResult, FatalEvaluationError
from notifications import webhook
from scraper.utils import FetchError
from services import scanner

pytestmark = pytest.mark.usefixtures("db")

NTFY_SETTINGS = {"enabled": True, "channel": "ntfy", "webhook_url": "https://ntfy.sh/test-topic", "min_score": 80}


class FakeEvaluator:
    """Stands in for ai.evaluator.Evaluator; scores and failures are configured per listing id."""

    scores: ClassVar[dict[int, int]] = {}
    failures: ClassVar[dict[int, Exception]] = {}
    calls: ClassVar[list[int]] = []
    tokens_per_call = 1_000

    def __init__(self, *, model: str, criteria: Any, filters: Any, target: Any) -> None:
        self.model = model

    def evaluate(self, listing: database.Listing) -> EvaluationResult:
        FakeEvaluator.calls.append(listing.id)
        if listing.id in FakeEvaluator.failures:
            raise FakeEvaluator.failures[listing.id]
        evaluation = Evaluation(
            score=FakeEvaluator.scores.get(listing.id, 70),
            pros=["Balkon"],
            cons=["Brak windy"],
            summary="OK.",
            consistency=ConsistencyCheck(is_consistent=True, issues=[]),
        )
        return EvaluationResult(evaluation, FakeEvaluator.tokens_per_call)


@pytest.fixture
def sent(monkeypatch) -> list[webhook.Notification]:
    messages: list[webhook.Notification] = []
    monkeypatch.setattr(webhook.Notifier, "send", lambda _self, notification: messages.append(notification))
    return messages


@pytest.fixture
def otodom_stub(monkeypatch, make_listing):
    """Fake Otodom: listings 1–3 on page 1 (prices editable via .prices); records fetched URLs."""

    class Stub:
        fetched: ClassVar[list[str]] = []
        prices: ClassVar[dict[int, float]] = {1: 700_000.0, 2: 700_000.0, 3: 700_000.0}
        failed_once: ClassVar[set[str]] = set()
        overrides: ClassVar[dict[int, dict[str, Any]]] = {}
        search_ids: ClassVar[set[int] | None] = None  # None = every listing from .prices is on the search page

    def search_page(_client, _filters, page=1):
        summaries = [
            {"id": i, "url": f"https://www.otodom.pl/pl/oferta/m-ID{i}", "price": price, "price_per_m2": None}
            for i, price in Stub.prices.items()
            if Stub.search_ids is None or i in Stub.search_ids
        ]
        return (summaries if page == 1 else []), 1

    def fetch_listing(_client, url):
        Stub.fetched.append(url)
        if url.endswith("ID3") and url not in Stub.failed_once:
            Stub.failed_once.add(url)
            raise FetchError("HTTP 500")
        listing_id = int(url.rsplit("ID", 1)[1])
        return make_listing(listing_id, price=Stub.prices[listing_id], **Stub.overrides.get(listing_id, {}))

    monkeypatch.setattr(scanner.otodom, "search_page", search_page)
    monkeypatch.setattr(scanner.otodom, "fetch_listing", fetch_listing)
    monkeypatch.setattr(scanner, "create_client", contextlib.nullcontext)
    monkeypatch.setattr(scanner, "Evaluator", FakeEvaluator)
    FakeEvaluator.scores, FakeEvaluator.failures, FakeEvaluator.calls = {}, {}, []
    return Stub


def test_scan_fetches_only_new_listings_and_evaluates_them(otodom_stub):
    scheduler.run_scan()

    scan = database.last_finished_scan()
    assert (scan.found, scan.new, scan.evaluated) == (3, 2, 2)
    assert "Nie pobrano ogłoszeń: 1" in scan.error  # listing 3 failed to download
    assert {listing.score for listing in database.query_listings()} == {70}
    assert database.tokens_used_today() == 2 * FakeEvaluator.tokens_per_call

    otodom_stub.fetched.clear()
    scheduler.run_scan()

    # Known listings are neither re-downloaded nor re-sent to the AI; the failed one is retried.
    assert otodom_stub.fetched == ["https://www.otodom.pl/pl/oferta/m-ID3"]
    assert sorted(FakeEvaluator.calls) == [1, 2, 3]
    scan = database.last_finished_scan()
    assert (scan.new, scan.evaluated, scan.error) == (1, 1, None)


def test_missing_or_logged_out_cli_stops_batch_without_using_up_attempts(otodom_stub):
    missing = FatalEvaluationError("Nie znaleziono komendy `claude`")
    FakeEvaluator.failures = {1: missing, 2: missing}

    scheduler.run_scan()

    scan = database.last_finished_scan()
    assert scan.new == 2 and scan.evaluated == 0
    assert len(FakeEvaluator.calls) == 1  # stopped after the first fatal error
    assert all(listing.ai_attempts == 0 for listing in database.listings_to_evaluate(10))
    assert "AI: Nie znaleziono komendy" in scan.error


def test_per_listing_ai_error_is_recorded_and_others_continue(otodom_stub):
    FakeEvaluator.failures = {1: EvaluationError("Model odmówił", tokens=300)}

    scheduler.run_scan()

    scan = database.last_finished_scan()
    assert scan.evaluated == 1 and "Nieudane oceny AI: 1" in scan.error
    failed = database.get_listing(1)
    assert failed.ai_attempts == 1 and failed.score is None
    assert database.tokens_used_today() == 300 + FakeEvaluator.tokens_per_call  # failed calls count too


def test_daily_token_limit_pauses_evaluation(otodom_stub):
    database.save_settings({"daily_token_limit": FakeEvaluator.tokens_per_call})

    scheduler.run_scan()

    scan = database.last_finished_scan()
    assert scan.new == 2 and scan.evaluated == 1
    assert "limit tokenów" in scan.error
    assert len(database.listings_to_evaluate(10)) == 1  # waits for tomorrow, attempts untouched


def test_token_limit_can_be_bypassed_for_explicit_runs(otodom_stub, make_listing):
    database.save_settings({"daily_token_limit": 1})
    database.record_token_usage("m", 5)
    for listing_id in (10, 11):
        database.add_listing(make_listing(listing_id))
    stats = scanner.ScanStats()

    scanner.evaluate_listings(
        database.listings_by_ids([10, 11]), database.get_settings(), stats, enforce_token_limit=False
    )

    assert stats.evaluated == 2


def test_great_new_listing_is_announced_once_per_flat(otodom_stub, sent):
    database.save_settings({"notifications": NTFY_SETTINGS})
    # Listings 1 and 2 are the same flat (same area, floor and district, similar price).
    otodom_stub.overrides = {1: {"area": 52.0}, 2: {"area": 52.4}}
    otodom_stub.prices[2] = 710_000.0
    FakeEvaluator.scores = {1: 91, 2: 88}

    scheduler.run_scan()

    assert [listing.id for listing in database.duplicate_members()[1]] == [2]
    assert len(sent) == 1 and sent[0].title.startswith("🏠 Nowa oferta")
    assert "✅ Balkon" in sent[0].body and sent[0].url.endswith(("ID1", "ID2"))

    database.reset_evaluations()
    scheduler.run_scan()  # re-evaluation of an announced flat stays silent
    assert len(sent) == 1


def test_low_score_is_not_announced(otodom_stub, sent):
    database.save_settings({"notifications": NTFY_SETTINGS})
    FakeEvaluator.scores = {1: 79, 2: 40}

    scheduler.run_scan()

    assert sent == []


def test_price_drop_is_tracked_and_announced(otodom_stub, sent):
    scheduler.run_scan()
    database.save_settings({"notifications": NTFY_SETTINGS})
    otodom_stub.prices[1] = 650_000.0  # drop
    otodom_stub.prices[2] = 720_000.0  # increase – tracked silently

    scheduler.run_scan()

    dropped, raised = database.get_listing(1), database.get_listing(2)
    assert (dropped.price, dropped.previous_price) == (650_000.0, 700_000.0)
    assert (raised.price, raised.previous_price) == (720_000.0, 700_000.0)
    assert database.last_finished_scan().price_drops == 1
    assert [n.title for n in sent] == ["📉 Obniżka ceny!"]
    assert "Z 700\u00a0000\u00a0zł na 650\u00a0000\u00a0zł" in sent[0].body


def test_watched_listings_off_the_search_pages_are_rechecked_daily(otodom_stub, sent, make_listing):
    database.save_settings({"notifications": NTFY_SETTINGS})
    week_ago = datetime.now() - timedelta(days=7)
    for listing_id in (50, 51):
        url = f"https://www.otodom.pl/pl/oferta/old-ID{listing_id}"
        database.add_listing(make_listing(listing_id, url=url, first_seen_at=week_ago))
    database.set_flag(50, "favorite", True)  # 51 is not watched
    otodom_stub.prices = {1: 700_000.0, 50: 640_000.0}  # listing 50's detail page shows a lower price
    otodom_stub.search_ids = {1}  # … but it is no longer on the newest-first search pages

    scheduler.run_scan()

    old_url = "https://www.otodom.pl/pl/oferta/old-ID50"
    assert old_url in otodom_stub.fetched
    assert (database.get_listing(50).price, database.get_listing(50).previous_price) == (640_000.0, 700_000.0)
    assert [n.title for n in sent] == ["📉 Obniżka ceny!"]
    assert database.get_listing(51).price_checked_at is None

    otodom_stub.fetched.clear()
    scheduler.run_scan()  # checked less than 24 h ago – not fetched again
    assert old_url not in otodom_stub.fetched


def test_misconfigured_notifications_are_reported_without_breaking_the_scan(otodom_stub, sent):
    database.save_settings({"notifications": {**NTFY_SETTINGS, "webhook_url": ""}})

    scheduler.run_scan()

    scan = database.last_finished_scan()
    assert scan.evaluated == 2 and "Powiadomienia:" in scan.error and sent == []


def test_search_failure_is_recorded(monkeypatch):
    def search_page(*_args, **_kwargs):
        raise FetchError("HTTP 403")

    monkeypatch.setattr(scanner.otodom, "search_page", search_page)
    monkeypatch.setattr(scanner, "create_client", contextlib.nullcontext)

    scheduler.run_scan()

    scan = database.last_finished_scan()
    assert scan.found == 0 and "Otodom: HTTP 403" in scan.error
