from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

import pytest
from sqlalchemy import inspect

import config
import database

pytestmark = pytest.mark.usefixtures("db")


def test_settings_default_and_merge():
    assert database.get_settings() == config.DEFAULT_SETTINGS

    database.save_settings({"filters": {"price_max": 650_000}, "ai_model": "claude-haiku-5-5"})
    settings = database.get_settings()

    assert settings["filters"]["price_max"] == 650_000
    assert settings["filters"]["location"] == config.DEFAULT_SETTINGS["filters"]["location"]
    assert settings["ai_model"] == "claude-haiku-5-5"


def test_add_listing_is_idempotent_and_keeps_user_flags(make_listing):
    assert database.add_listing(make_listing(1)) is True
    database.set_flag(1, "favorite", True)

    assert database.add_listing(make_listing(1, title="Zmieniony")) is False
    [listing] = database.query_listings(rejected=None)
    assert listing.favorite is True and listing.title == "Mieszkanie 1"
    assert database.known_prices([1, 2]) == {1: 700_000.0}


def test_set_flag_rejects_unknown_flag(make_listing):
    database.add_listing(make_listing(1))
    with pytest.raises(ValueError):
        database.set_flag(1, "score", True)


def test_query_listings_filters_and_sorting(make_listing):
    now = datetime.now()
    database.add_listing(make_listing(1, price=900_000, created_at=now - timedelta(days=2)))
    database.add_listing(make_listing(2, price=500_000, created_at=now))
    database.add_listing(make_listing(3, price=None, created_at=now - timedelta(days=1)))
    database.save_evaluation(1, score=90, analysis={}, model="m")
    database.save_evaluation(2, score=60, analysis={}, model="m")
    database.set_flag(3, "rejected", True)

    ids = lambda listings: [listing.id for listing in listings]  # noqa: E731
    assert ids(database.query_listings(sort="score")) == [1, 2]
    assert ids(database.query_listings(sort="score", rejected=None)) == [1, 2, 3]  # unscored last
    assert ids(database.query_listings(sort="price", rejected=None)) == [2, 1, 3]  # no price last
    assert ids(database.query_listings(sort="newest", rejected=None)) == [2, 3, 1]
    assert ids(database.query_listings(min_score=70)) == [1]
    assert ids(database.query_listings(rejected=True)) == [3]

    database.set_flag(2, "favorite", True)
    assert ids(database.query_listings(only_favorites=True)) == [2]


def test_listings_to_evaluate_skips_done_rejected_and_exhausted(make_listing):
    for listing_id in range(1, 5):
        database.add_listing(make_listing(listing_id))
    database.save_evaluation(1, score=80, analysis={}, model="m")
    database.set_flag(2, "rejected", True)
    for _ in range(config.MAX_AI_ATTEMPTS):
        database.save_evaluation_error(3, "błąd")

    assert [listing.id for listing in database.listings_to_evaluate(10)] == [4]

    assert database.reset_evaluations() == 3  # everything except the rejected one
    assert {listing.id for listing in database.listings_to_evaluate(10)} == {1, 3, 4}


def test_scan_history():
    assert database.last_finished_scan() is None

    first = database.start_scan()
    database.finish_scan(first, found=36, new=3, evaluated=2, error=None)
    interrupted = database.start_scan()

    last = database.last_finished_scan()
    assert last.id == first and last.new == 3

    database.close_interrupted_scans()
    last = database.last_finished_scan()
    assert last.id == interrupted and "przerwane" in last.error


def test_update_price_remembers_previous_price(make_listing):
    database.add_listing(make_listing(1, price=700_000, area=50.0))

    database.update_price(1, price=650_000, price_per_m2=None)

    listing = database.get_listing(1)
    assert (listing.price, listing.previous_price, listing.price_per_m2) == (650_000, 700_000, 13_000)
    assert listing.price_changed_at is not None


def test_duplicate_groups_and_notification_per_flat(make_listing):
    for listing_id in (1, 2, 3):
        database.add_listing(make_listing(listing_id))

    assert database.save_duplicate_groups({2: 1}) == 1
    assert database.save_duplicate_groups({2: 1}) == 0  # unchanged
    assert [listing.id for listing in database.duplicate_members()[1]] == [2]
    assert sorted(listing.id for listing in database.query_listings(include_duplicates=False)) == [1, 3]

    assert not database.group_already_notified(2)
    database.mark_notified(1)
    assert database.group_already_notified(2) and database.group_already_notified(1)
    assert not database.group_already_notified(3)

    database.save_duplicate_groups({})
    assert database.duplicate_members() == {}


def test_listings_to_recheck_returns_stale_watched_listings(make_listing):
    week_ago = datetime.now() - timedelta(days=7)
    database.add_listing(make_listing(1, first_seen_at=week_ago))  # favourite
    database.add_listing(make_listing(2, first_seen_at=week_ago))  # high score
    database.add_listing(make_listing(3, first_seen_at=week_ago))  # low score – not watched
    database.add_listing(make_listing(4))  # favourite, but just found
    database.add_listing(make_listing(5, first_seen_at=week_ago))  # favourite, but rejected
    for listing_id in (1, 4, 5):
        database.set_flag(listing_id, "favorite", True)
    database.set_flag(5, "rejected", True)
    database.save_evaluation(2, score=85, analysis={}, model="m")
    database.save_evaluation(3, score=50, analysis={}, model="m")

    assert {listing.id for listing in database.listings_to_recheck(min_score=80, limit=10)} == {1, 2}

    database.mark_price_checked([1])
    assert [listing.id for listing in database.listings_to_recheck(min_score=80, limit=10)] == [2]


def test_token_usage_counts_today_only():
    database.record_token_usage("claude-haiku-5-5", 1_500, listing_id=1)
    database.record_token_usage("claude-haiku-5-5", 500)

    assert database.tokens_used_today() == 2_000
    assert database.tokens_used_since(datetime.now() + timedelta(minutes=1)) == 0


V1_SCHEMA = """
CREATE TABLE settings (key VARCHAR NOT NULL PRIMARY KEY, value JSON);
CREATE TABLE listings (
    id INTEGER NOT NULL PRIMARY KEY, url VARCHAR NOT NULL, title VARCHAR NOT NULL, price FLOAT,
    price_per_m2 FLOAT, area FLOAT, rooms INTEGER, floor VARCHAR, building_floors INTEGER,
    build_year INTEGER, heating VARCHAR, rent FLOAT, market VARCHAR, location VARCHAR,
    description TEXT NOT NULL, image_url VARCHAR, attributes JSON NOT NULL, created_at DATETIME,
    first_seen_at DATETIME NOT NULL, favorite BOOLEAN NOT NULL, rejected BOOLEAN NOT NULL,
    contacted BOOLEAN NOT NULL, score INTEGER, analysis JSON, ai_model VARCHAR, evaluated_at DATETIME,
    ai_error TEXT, ai_attempts INTEGER NOT NULL
);
CREATE TABLE scan_runs (
    id INTEGER NOT NULL PRIMARY KEY, started_at DATETIME NOT NULL, finished_at DATETIME,
    found INTEGER NOT NULL, new INTEGER NOT NULL, evaluated INTEGER NOT NULL, error TEXT
);
INSERT INTO listings VALUES (7, 'https://www.otodom.pl/pl/oferta/x-ID7', 'Stare', 500000, 10000, 50, 2, '3',
    4, 1990, 'miejskie', 600, 'wtórny', 'Warszawa, Wola', '', NULL, '{}', NULL, '2026-10-01 10:00:00', 1, 0, 0,
    77, '{"pros": [], "cons": [], "summary": "ok", "consistency": {"is_consistent": true, "issues": []}}',
    'claude-sonnet-5-5', '2026-10-01 10:05:00', NULL, 1);
INSERT INTO settings VALUES ('notifications', '{"min_score": 90}');
INSERT INTO settings VALUES ('anthropic_api_key', '"sk-ant-old-key"');
"""


def test_v1_database_is_migrated_in_place(tmp_path, make_listing):
    path = tmp_path / "v1.db"
    with sqlite3.connect(path) as connection:
        connection.executescript(V1_SCHEMA)

    database.init_db(path)

    columns = {c["name"] for c in inspect(database._engine).get_columns("listings")}
    assert {"district", "street", "latitude", "longitude", "previous_price", "duplicate_of", "notified_at"} <= columns
    [listing] = database.query_listings()
    assert (listing.id, listing.score, listing.favorite, listing.duplicate_of) == (7, 77, True, None)
    settings = database.get_settings()
    assert settings["notifications"]["min_score"] == 90  # stored value kept …
    assert settings["notifications"]["channel"] == "ntfy"  # … new keys filled from defaults
    assert "anthropic_api_key" not in settings  # obsolete secret removed from the database
    # New-style rows and the new token_usage table work on the migrated database.
    assert database.add_listing(make_listing(8, street="Wolska", latitude=52.23, longitude=20.95))
    assert database.get_listing(8).street == "Wolska"
    database.record_token_usage("claude-sonnet-5-5", 10)
    assert database.tokens_used_today() == 10
