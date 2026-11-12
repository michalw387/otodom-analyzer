#!/usr/bin/env python3
"""Re-score saved listings with the current criteria, target address, model and system prompt.

Examples:
    python scripts/reevaluate.py                      # all non-rejected listings
    python scripts/reevaluate.py --ids 68562677 68559126
    python scripts/reevaluate.py --unscored --limit 20 --yes
    python scripts/reevaluate.py --min-score 70 --model claude-haiku-5-5
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config
import database
from database import Listing
from formatting import plural, thousands
from services import scanner


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Ponowna ocena AI zapisanych ofert.")
    parser.add_argument("--ids", type=int, nargs="+", help="oceń tylko oferty o tych ID")
    parser.add_argument("--unscored", action="store_true", help="tylko oferty bez oceny")
    parser.add_argument("--min-score", type=int, help="tylko oferty z obecną oceną ≥ tej wartości")
    parser.add_argument("--max-score", type=int, help="tylko oferty z obecną oceną ≤ tej wartości")
    parser.add_argument("--include-rejected", action="store_true", help="uwzględnij odrzucone oferty")
    parser.add_argument("--limit", type=int, help="maksymalna liczba ofert")
    parser.add_argument("--model", choices=list(config.AI_MODELS), help="model zamiast ustawionego w aplikacji")
    parser.add_argument("--ignore-token-limit", action="store_true", help="nie sprawdzaj dziennego limitu tokenów")
    parser.add_argument("--notify", action="store_true", help="wyślij powiadomienia o ofertach powyżej progu")
    parser.add_argument("-y", "--yes", action="store_true", help="nie pytaj o potwierdzenie")
    return parser.parse_args(argv)


def select_listings(args: argparse.Namespace) -> list[Listing]:
    if args.ids:
        listings = database.listings_by_ids(args.ids)
    else:
        listings = database.query_listings(sort="newest", rejected=None if args.include_rejected else False)
    if args.unscored:
        listings = [listing for listing in listings if listing.score is None]
    if args.min_score is not None:
        listings = [listing for listing in listings if listing.score is not None and listing.score >= args.min_score]
    if args.max_score is not None:
        listings = [listing for listing in listings if listing.score is not None and listing.score <= args.max_score]
    return listings[: args.limit] if args.limit else listings


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    config.configure_logging()
    database.init_db()
    settings = database.get_settings()
    if args.model:
        settings["ai_model"] = args.model

    listings = select_listings(args)
    if not listings:
        print("Brak ofert spełniających warunki.")
        return 0

    limit = int(settings["daily_token_limit"] or 0)
    print(
        f"Do oceny: {len(listings)} {plural(len(listings), 'oferta', 'oferty', 'ofert')} · "
        f"model {settings['ai_model']} · "
        f"tokeny dziś: {thousands(database.tokens_used_today())}"
        + (f" / limit {thousands(limit)}" if limit and not args.ignore_token_limit else " (bez limitu)")
    )
    if not args.yes and input("Kontynuować? [t/N] ").strip().lower() not in ("t", "tak", "y", "yes"):
        print("Przerwano.")
        return 1

    stats = scanner.ScanStats()
    notifier = scanner.build_notifier(settings, stats) if args.notify else None
    scanner.evaluate_listings(
        listings, settings, stats, notifier, enforce_token_limit=not args.ignore_token_limit
    )
    print(f"Oceniono {stats.evaluated} z {len(listings)} {'oferty' if len(listings) == 1 else 'ofert'}.")
    for problem in stats.problems:
        print(f"⚠️  {problem}")
    return 0 if stats.evaluated == len(listings) else 2


if __name__ == "__main__":
    sys.exit(main())
