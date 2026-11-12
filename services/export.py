"""Excel/CSV export of listings, shared by the UI button and scripts/export_reports.py.

Listing texts come from third parties, so cells are protected against formula injection:
xlsx cells are typed as plain strings and CSV values starting with = + - @ get a leading quote.
"""

from __future__ import annotations

import io
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font
from openpyxl.utils import get_column_letter

from formatting import pln
from services.geo import commute

if TYPE_CHECKING:
    from database import Listing

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
LINK_COLUMN = "Link"
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")
_WIDE_COLUMNS = {"Tytuł": 45, "Lokalizacja": 35, "Zalety": 60, "Wady i ryzyka": 60, "Podsumowanie AI": 70,
                 "Niespójności": 40, "Inne ogłoszenia tego mieszkania": 60, LINK_COLUMN: 30}


def listing_rows(
    listings: Sequence[Listing],
    *,
    duplicates: dict[int, list[Listing]] | None = None,
    target: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    rows = []
    for listing in listings:
        analysis = listing.analysis or {}
        trip = commute(listing, target)
        others = (duplicates or {}).get(listing.id, [])
        rows.append(
            {
                "ID": listing.id,
                "Tytuł": listing.title,
                LINK_COLUMN: listing.url,
                "Ocena AI": listing.score,
                "Cena (zł)": listing.price,
                "Cena za m² (zł)": listing.price_per_m2,
                "Poprzednia cena (zł)": listing.previous_price,
                "Powierzchnia (m²)": listing.area,
                "Pokoje": listing.rooms,
                "Piętro": listing.floor,
                "Pięter w budynku": listing.building_floors,
                "Rok budowy": listing.build_year,
                "Ogrzewanie": listing.heating,
                "Czynsz (zł)": listing.rent,
                "Rynek": listing.market,
                "Lokalizacja": listing.location,
                "Odległość do celu (km)": round(trip.km, 1) if trip else None,
                "Szac. dojazd (min)": trip.minutes if trip else None,
                "Zalety": "\n".join(analysis.get("pros", [])),
                "Wady i ryzyka": "\n".join(analysis.get("cons", [])),
                "Podsumowanie AI": analysis.get("summary"),
                "Niespójności": "\n".join((analysis.get("consistency") or {}).get("issues", [])),
                "Ulubione": _yes_no(listing.favorite),
                "Skontaktowano": _yes_no(listing.contacted),
                "Odrzucone": _yes_no(listing.rejected),
                "Inne ogłoszenia tego mieszkania": "\n".join(f"{o.url} ({pln(o.price) or 'cena ?'})" for o in others),
                "Dodano na Otodom": listing.created_at,
                "Znaleziono": listing.first_seen_at,
            }
        )
    return rows


def to_xlsx(rows: Sequence[dict[str, Any]]) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    assert sheet is not None
    sheet.title = "Oferty"
    headers = list(rows[0]) if rows else []
    sheet.append(headers)
    for cell in sheet[1]:
        cell.font = Font(bold=True)
    for row in rows:
        sheet.append([row[header] for header in headers])
    for cells in sheet.iter_rows(min_row=2):
        for cell in cells:
            if headers[cell.column - 1] == LINK_COLUMN and cell.value:
                cell.hyperlink = str(cell.value)
                cell.style = "Hyperlink"
            if isinstance(cell.value, str):
                cell.data_type = "s"  # never let openpyxl treat "=…" as a formula
            cell.alignment = Alignment(wrap_text=isinstance(cell.value, str), vertical="top")
    for index, header in enumerate(headers, start=1):
        sheet.column_dimensions[get_column_letter(index)].width = _WIDE_COLUMNS.get(header, max(12, len(header) + 2))
    sheet.freeze_panes = "C2"
    if headers:
        sheet.auto_filter.ref = sheet.dimensions
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def to_csv(rows: Sequence[dict[str, Any]]) -> bytes:
    """Semicolon-separated UTF-8 with BOM and decimal commas – opens correctly in Polish Excel."""
    safe_rows = [{key: _csv_safe(value) for key, value in row.items()} for row in rows]
    frame = pd.DataFrame(safe_rows)
    return frame.to_csv(sep=";", decimal=",", index=False).encode("utf-8-sig")


def write_report(rows: Sequence[dict[str, Any]], path: Path) -> Path:
    if path.suffix.lower() not in (".xlsx", ".csv"):
        raise ValueError("Obsługiwane formaty: .xlsx i .csv")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(to_xlsx(rows) if path.suffix.lower() == ".xlsx" else to_csv(rows))
    return path


def _csv_safe(value: Any) -> Any:
    if isinstance(value, str) and value.startswith(_FORMULA_PREFIXES):
        return "'" + value
    return value


def _yes_no(value: bool) -> str:
    return "Tak" if value else "Nie"
