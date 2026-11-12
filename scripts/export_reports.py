#!/usr/bin/env python3
"""Export the best listings – with the AI analysis, duplicates and links – to Excel or CSV.

Examples:
    python scripts/export_reports.py                          # score ≥ 75 → reports/oferty-YYYY-MM-DD.xlsx
    python scripts/export_reports.py --min-score 60 -o oferty.csv
    python scripts/export_reports.py --favorites --include-rejected
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config
import database
from formatting import plural
from services import export


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Eksport najlepszych ofert do Excela (.xlsx) lub CSV.")
    parser.add_argument("--min-score", type=int, default=75, help="minimalna ocena AI (domyślnie 75)")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=config.REPORTS_DIR / f"oferty-{date.today():%Y-%m-%d}.xlsx",
        help="plik wynikowy .xlsx lub .csv (domyślnie reports/oferty-DATA.xlsx)",
    )
    parser.add_argument("--favorites", action="store_true", help="tylko ulubione")
    parser.add_argument("--include-rejected", action="store_true", help="uwzględnij odrzucone oferty")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    database.init_db()
    listings = database.query_listings(
        sort="score",
        min_score=args.min_score,
        only_favorites=args.favorites,
        rejected=None if args.include_rejected else False,
        include_duplicates=False,
    )
    rows = export.listing_rows(
        listings, duplicates=database.duplicate_members(), target=database.get_settings()["target"]
    )
    try:
        path = export.write_report(rows, args.output)
    except ValueError as exc:
        print(f"Błąd: {exc}", file=sys.stderr)
        return 1
    print(f"Zapisano {len(rows)} {plural(len(rows), 'ofertę', 'oferty', 'ofert')} (ocena ≥ {args.min_score}) do {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
