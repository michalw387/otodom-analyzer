from __future__ import annotations

from typing import Any

import pytest

import database


@pytest.fixture
def db(tmp_path):
    database.init_db(tmp_path / "test.db")


@pytest.fixture
def make_listing():
    def _make(listing_id: int, **overrides: Any) -> dict[str, Any]:
        data = {
            "id": listing_id,
            "url": f"https://www.otodom.pl/pl/oferta/mieszkanie-ID{listing_id}",
            "title": f"Mieszkanie {listing_id}",
            "price": 700_000.0,
            "price_per_m2": 14_000.0,
            "area": 40.0 + listing_id * 5,  # distinct areas, so default listings are never duplicates
            "rooms": 2,
            "floor": "3",
            "building_floors": 4,
            "build_year": 1985,
            "heating": "miejskie",
            "rent": 800.0,
            "market": "wtórny",
            "location": "Warszawa, Mokotów",
            "description": "Słoneczne mieszkanie z balkonem.",
            "image_url": None,
            "attributes": {"extras_types": ["balcony"]},
            "created_at": None,
            "district": "Mokotów",
            "street": None,
            "latitude": None,
            "longitude": None,
        }
        data.update(overrides)
        return data

    return _make


@pytest.fixture(autouse=True)
def no_notification_env(monkeypatch):
    """Keep values from a developer's .env out of the tests."""
    for name in ("NOTIFY_WEBHOOK_URL", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"):
        monkeypatch.delenv(name, raising=False)
