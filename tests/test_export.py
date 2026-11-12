from __future__ import annotations

import io

import openpyxl
import pytest

from database import Listing
from services import export

ANALYSIS = {
    "pros": ["Balkon", "Metro"],
    "cons": ["Brak windy"],
    "summary": "Dobra oferta.",
    "consistency": {"is_consistent": False, "issues": ["Inny metraż w opisie"]},
}


@pytest.fixture
def listings(make_listing) -> list[Listing]:
    best = Listing(**make_listing(1, title="=HYPERLINK(\"http://evil\")", score=91, analysis=ANALYSIS, favorite=True))
    other = Listing(**make_listing(2, title="Mieszkanie 2", score=None))
    return [best, other]


def test_listing_rows_include_analysis_duplicates_and_commute(listings, make_listing):
    duplicate = Listing(**make_listing(3, url="https://www.otodom.pl/pl/oferta/dup-ID3", price=690_000.0))
    listings[0].latitude, listings[0].longitude = 52.2318, 21.0060
    target = {"label": "Praca", "mode": "car", "latitude": 52.2497, "longitude": 21.0122}

    rows = export.listing_rows(listings, duplicates={1: [duplicate]}, target=target)

    first = rows[0]
    assert first["Ocena AI"] == 91 and first["Ulubione"] == "Tak" and first["Link"] == listings[0].url
    assert first["Zalety"] == "Balkon\nMetro" and first["Niespójności"] == "Inny metraż w opisie"
    assert first["Odległość do celu (km)"] == 2.0 and first["Szac. dojazd (min)"] == 11
    assert first["Inne ogłoszenia tego mieszkania"] == "https://www.otodom.pl/pl/oferta/dup-ID3 (690\u00a0000\u00a0zł)"
    assert rows[1]["Ocena AI"] is None and rows[1]["Odległość do celu (km)"] is None


def test_xlsx_keeps_third_party_text_as_plain_strings(listings):
    workbook = openpyxl.load_workbook(io.BytesIO(export.to_xlsx(export.listing_rows(listings))))
    sheet = workbook.active
    headers = [cell.value for cell in sheet[1]]

    title = sheet.cell(row=2, column=headers.index("Tytuł") + 1)
    assert title.data_type == "s" and title.value.startswith("=HYPERLINK")
    link = sheet.cell(row=2, column=headers.index("Link") + 1)
    assert link.hyperlink.target == listings[0].url
    assert sheet.max_row == 3


def test_csv_neutralizes_formulas_and_opens_in_polish_excel(listings):
    data = export.to_csv(export.listing_rows(listings))

    assert data.startswith(b"\xef\xbb\xbf")  # UTF-8 BOM
    text = data.decode("utf-8-sig")
    header, first_row = text.splitlines()[:2]
    assert header.startswith("ID;Tytuł;Link;Ocena AI")
    assert first_row.split(";")[1].startswith("\"'=HYPERLINK")


def test_write_report_picks_format_from_suffix(tmp_path, listings):
    rows = export.listing_rows(listings)

    assert export.write_report(rows, tmp_path / "out" / "r.xlsx").read_bytes()[:2] == b"PK"
    assert export.write_report(rows, tmp_path / "r.csv").read_bytes().startswith(b"\xef\xbb\xbf")
    with pytest.raises(ValueError):
        export.write_report(rows, tmp_path / "r.pdf")


def test_empty_export_is_a_valid_workbook():
    workbook = openpyxl.load_workbook(io.BytesIO(export.to_xlsx([])))
    assert workbook.active.max_row == 1
