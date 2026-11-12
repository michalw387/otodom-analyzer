"""One scan: new Otodom listings, price changes, duplicates, AI scoring and notifications.

Used by the background scheduler and by scripts/reevaluate.py, so both share the same
token limit, error handling and notification rules.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import config
import database
from ai.evaluator import EvaluationError, Evaluator, FatalEvaluationError
from formatting import thousands
from notifications import webhook
from scraper import otodom
from scraper.utils import ScraperError, create_client
from services import duplicates

if TYPE_CHECKING:
    from curl_cffi import requests as curl_requests

    from ai.evaluator import Evaluation
    from database import Listing

log = logging.getLogger(__name__)

# Price changes below this are rounding noise, not a real change.
MIN_PRICE_CHANGE_PLN = 1.0


@dataclass
class ScanStats:
    found: int = 0
    new: int = 0
    evaluated: int = 0
    price_drops: int = 0
    problems: list[str] = field(default_factory=list)


def run_scan_once() -> ScanStats:
    settings = database.get_settings()
    scan_id = database.start_scan()
    stats = ScanStats()
    try:
        notifier = build_notifier(settings, stats)
        try:
            fetch_new_listings(settings, stats, notifier)
        except ScraperError as exc:
            log.warning("Otodom search failed: %s", exc)
            stats.problems.append(f"Otodom: {exc}")
        duplicates.refresh_duplicates()
        # Evaluate even if Otodom failed – listings queued earlier may still be waiting.
        pending = database.listings_to_evaluate(config.MAX_EVALUATIONS_PER_SCAN)
        evaluate_listings(pending, settings, stats, notifier)
    except Exception as exc:  # a background scan must always end up recorded
        log.exception("Unexpected scan failure")
        stats.problems.append(f"Nieoczekiwany błąd: {exc}")
    finally:
        database.finish_scan(
            scan_id,
            found=stats.found,
            new=stats.new,
            evaluated=stats.evaluated,
            price_drops=stats.price_drops,
            error="; ".join(stats.problems) or None,
        )
        log.info("Scan finished: %s", stats)
    return stats


def build_notifier(settings: dict[str, Any], stats: ScanStats) -> webhook.Notifier | None:
    notification_settings = settings["notifications"]
    if not notification_settings.get("enabled"):
        return None
    try:
        return webhook.Notifier.from_settings(notification_settings)
    except webhook.NotificationError as exc:
        stats.problems.append(f"Powiadomienia: {exc}")
        return None


def fetch_new_listings(settings: dict[str, Any], stats: ScanStats, notifier: webhook.Notifier | None = None) -> None:
    attempted = failed = 0
    with create_client() as client:
        for page in range(1, config.MAX_SEARCH_PAGES + 1):
            summaries, total_pages = otodom.search_page(client, settings["filters"], page)
            stats.found += len(summaries)
            known = database.known_prices([s["id"] for s in summaries])
            for summary in summaries:
                if summary["id"] in known:
                    _apply_price(summary["id"], known[summary["id"]], summary["price"], summary["price_per_m2"],
                                 settings, stats, notifier)
            database.mark_price_checked(list(known))
            # Only listings unknown to the database are fetched and later sent to the AI.
            fresh = [s for s in summaries if s["id"] not in known]
            for summary in fresh[: config.MAX_NEW_LISTINGS_PER_SCAN - attempted]:
                attempted += 1
                try:
                    listing = otodom.fetch_listing(client, summary["url"])
                except ScraperError as exc:
                    log.warning("Could not fetch %s: %s", summary["url"], exc)
                    failed += 1
                    continue
                if database.add_listing(listing):
                    stats.new += 1
            # Results are sorted newest first, so a page with nothing new means we are up to date.
            if not fresh or attempted >= config.MAX_NEW_LISTINGS_PER_SCAN or page >= total_pages:
                break
        _recheck_watched_prices(client, settings, stats, notifier)
    if failed:
        stats.problems.append(f"Nie pobrano ogłoszeń: {failed} – ponowię przy następnym skanowaniu")


def _recheck_watched_prices(
    client: curl_requests.Session, settings: dict[str, Any], stats: ScanStats, notifier: webhook.Notifier | None
) -> None:
    watched = database.listings_to_recheck(
        min_score=int(settings["notifications"].get("min_score", 80)), limit=config.MAX_PRICE_RECHECKS_PER_SCAN
    )
    for listing in watched:
        try:
            current = otodom.fetch_listing(client, listing.url)
        except ScraperError as exc:
            # Possibly removed from Otodom – try again after the next recheck interval.
            log.info("Price recheck of %s failed: %s", listing.url, exc)
        else:
            _apply_price(
                listing.id, listing.price, current["price"], current["price_per_m2"], settings, stats, notifier
            )
        database.mark_price_checked([listing.id])


def _apply_price(
    listing_id: int,
    old_price: float | None,
    new_price: float | None,
    new_price_per_m2: float | None,
    settings: dict[str, Any],
    stats: ScanStats,
    notifier: webhook.Notifier | None,
) -> None:
    if not old_price or not new_price or abs(new_price - old_price) < MIN_PRICE_CHANGE_PLN:
        return
    database.update_price(listing_id, price=new_price, price_per_m2=new_price_per_m2)
    if new_price > old_price:
        return
    stats.price_drops += 1
    listing = database.get_listing(listing_id)
    if notifier and settings["notifications"].get("price_drops") and listing and not listing.rejected:
        _send(notifier, webhook.price_drop_message(listing, old_price, new_price), stats)


def evaluate_listings(
    listings: Sequence[Listing],
    settings: dict[str, Any],
    stats: ScanStats,
    notifier: webhook.Notifier | None = None,
    *,
    enforce_token_limit: bool = True,
) -> None:
    if not listings:
        return
    evaluator = Evaluator(
        model=settings["ai_model"],
        criteria=settings["criteria"],
        filters=settings["filters"],
        target=settings["target"],
    )
    token_limit = int(settings.get("daily_token_limit") or 0) if enforce_token_limit else 0
    failed = 0
    for listing in listings:
        # Checked before every request, so a limit can be exceeded by at most one evaluation.
        if token_limit and database.tokens_used_today() >= token_limit:
            stats.problems.append(
                f"Osiągnięto dzienny limit tokenów AI ({thousands(token_limit)}) – pozostałe oferty ocenię jutro"
            )
            break
        try:
            result = evaluator.evaluate(listing)
        except FatalEvaluationError as exc:
            # Not the listing's fault – keep its attempts intact and retry on the next scan.
            log.warning("AI evaluation stopped: %s", exc)
            stats.problems.append(f"AI: {exc}")
            break
        except EvaluationError as exc:
            log.warning("Could not evaluate listing %s: %s", listing.id, exc)
            if exc.tokens:
                database.record_token_usage(evaluator.model, exc.tokens, listing.id)
            database.save_evaluation_error(listing.id, str(exc))
            failed += 1
            continue
        database.record_token_usage(evaluator.model, result.tokens, listing.id)
        database.save_evaluation(
            listing.id,
            score=result.evaluation.score,
            analysis=result.evaluation.model_dump(exclude={"score"}),
            model=evaluator.model,
        )
        stats.evaluated += 1
        _notify_if_great(listing, result.evaluation, settings, stats, notifier)
    if failed:
        stats.problems.append(f"Nieudane oceny AI: {failed}")


def _notify_if_great(
    listing: Listing,
    evaluation: Evaluation,
    settings: dict[str, Any],
    stats: ScanStats,
    notifier: webhook.Notifier | None,
) -> None:
    if notifier is None or evaluation.score < int(settings["notifications"].get("min_score", 80)):
        return
    # One alert per flat: re-evaluations and the same flat from another agency stay silent.
    if database.group_already_notified(listing.id):
        return
    message = webhook.new_listing_message(listing, evaluation.model_dump(exclude={"score"}), evaluation.score)
    if _send(notifier, message, stats):
        database.mark_notified(listing.id)


def _send(notifier: webhook.Notifier, notification: webhook.Notification, stats: ScanStats) -> bool:
    try:
        notifier.send(notification)
    except webhook.NotificationError as exc:
        log.warning("Notification failed: %s", exc)
        stats.problems.append(f"Powiadomienie nie zostało wysłane: {exc}")
        return False
    return True
