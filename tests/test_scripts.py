from __future__ import annotations

import openpyxl
import pytest

import database
from scripts import export_reports, reevaluate
from services import scanner
from tests.test_scanner import FakeEvaluator

pytestmark = pytest.mark.usefixtures("db")


@pytest.fixture(autouse=True)
def keep_test_database(db, monkeypatch):
    """The scripts call init_db() with the real path – stay on the test database instead."""
    monkeypatch.setattr(database, "init_db", lambda *_args, **_kwargs: None)


@pytest.fixture
def saved_listings(make_listing):
    for listing_id, score in ((1, 90), (2, 60), (3, None)):
        database.add_listing(make_listing(listing_id))
        if score is not None:
            analysis = {"pros": [], "cons": [], "summary": "x"}
            database.save_evaluation(listing_id, score=score, analysis=analysis, model="m")
    database.set_flag(2, "rejected", True)


@pytest.fixture
def fake_ai(monkeypatch):
    monkeypatch.setattr(scanner, "Evaluator", FakeEvaluator)
    FakeEvaluator.scores, FakeEvaluator.failures, FakeEvaluator.calls = {}, {}, []


def test_reevaluate_selected_listings(saved_listings, fake_ai, capsys):
    assert reevaluate.main(["--yes"]) == 0  # all non-rejected: 1 and 3

    assert sorted(FakeEvaluator.calls) == [1, 3]
    assert database.get_listing(1).score == 70 and database.get_listing(2).score == 60
    assert "Oceniono 2 z 2 ofert." in capsys.readouterr().out


def test_reevaluate_filters(saved_listings, fake_ai):
    reevaluate.main(["--yes", "--unscored"])
    assert FakeEvaluator.calls == [3]

    FakeEvaluator.calls.clear()
    reevaluate.main(["--yes", "--ids", "2", "--model", "claude-haiku-5-5"])
    assert FakeEvaluator.calls == [2] and database.get_listing(2).ai_model == "claude-haiku-5-5"


def test_reevaluate_respects_the_token_limit_unless_overridden(saved_listings, fake_ai):
    database.save_settings({"daily_token_limit": 100})
    database.record_token_usage("m", 100)

    assert reevaluate.main(["--yes"]) == 2
    assert FakeEvaluator.calls == []

    assert reevaluate.main(["--yes", "--ignore-token-limit"]) == 0
    assert sorted(FakeEvaluator.calls) == [1, 3]


def test_reevaluate_asks_for_confirmation(saved_listings, fake_ai, monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _prompt: "n")
    assert reevaluate.main([]) == 1
    assert FakeEvaluator.calls == []


def test_export_best_listings_to_xlsx(saved_listings, tmp_path, capsys):
    output = tmp_path / "raport.xlsx"

    assert export_reports.main(["--min-score", "75", "-o", str(output)]) == 0

    sheet = openpyxl.load_workbook(output).active
    assert [cell.value for cell in sheet["A"]][1:] == [1]  # only listing 1 scores ≥ 75
    assert "Zapisano 1 ofertę" in capsys.readouterr().out


def test_export_to_csv_with_rejected(saved_listings, tmp_path):
    output = tmp_path / "raport.csv"

    export_reports.main(["--min-score", "50", "--include-rejected", "-o", str(output)])

    lines = output.read_text(encoding="utf-8-sig").splitlines()
    assert [line.split(";")[0] for line in lines[1:]] == ["1", "2"]


def test_export_rejects_unknown_format(saved_listings, tmp_path, capsys):
    assert export_reports.main(["-o", str(tmp_path / "raport.pdf")]) == 1
    assert "Obsługiwane formaty" in capsys.readouterr().err
