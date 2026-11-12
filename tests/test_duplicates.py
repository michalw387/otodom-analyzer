from __future__ import annotations

from datetime import datetime, timedelta

import pytest

import database
from database import Listing
from services import duplicates

T0 = datetime(2026, 10, 1, 12, 0)


@pytest.fixture
def flat(make_listing):
    """Same flat by default: 50 m², 3rd floor, Iberyjska street in Mokotów, 700 000 zł."""

    def _flat(listing_id: int, **overrides) -> Listing:
        defaults = {
            "area": 50.0,
            "street": "Iberyjska",
            "location": "Warszawa, Mokotów, Stegny, Iberyjska",
            "first_seen_at": T0 + timedelta(minutes=listing_id),
        }
        return Listing(**make_listing(listing_id, **{**defaults, **overrides}))

    return _flat


@pytest.mark.parametrize(
    ("raw", "normalized"),
    [
        ("ul. Żeglugi Wiślanej", "zeglugi wislanej"),
        ("Aleje Jerozolimskie", "jerozolimskie"),
        ("ŁÓDŹ, Bałuty", "lodz baluty"),
        ("  ", None),
        (None, None),
    ],
)
def test_normalize_place(raw, normalized):
    assert duplicates.normalize_place(raw) == normalized


def test_same_flat_from_another_agency_is_a_duplicate(flat):
    assert duplicates.is_duplicate(flat(1), flat(2, area=50.9, price=730_000.0, street="ul. Iberyjska"))


@pytest.mark.parametrize(
    "difference",
    [
        {"area": 51.5},  # more than 1 m² apart
        {"price": 735_000.0},  # 5% more expensive
        {"floor": "4"},
        {"floor": None},
        {"street": "Sobieskiego"},
        {"price": None},
        {"location": "Kraków, Podgórze, Iberyjska"},  # same street name, other city
    ],
)
def test_different_flats_are_not_duplicates(flat, difference):
    assert not duplicates.is_duplicate(flat(1), flat(2, **difference))


def test_district_is_compared_when_a_street_is_missing(flat):
    assert duplicates.is_duplicate(flat(1), flat(2, street=None))
    assert not duplicates.is_duplicate(flat(1, street=None), flat(2, street=None, district="Wola"))


def test_coordinates_far_apart_rule_out_a_match(flat):
    near, far = flat(1, latitude=52.18, longitude=21.05), flat(2, latitude=52.25, longitude=21.05)
    assert not duplicates.is_duplicate(near, far)
    assert duplicates.is_duplicate(near, flat(3, latitude=52.181, longitude=21.051))


def test_listings_saved_before_v1_1_compare_the_full_location(flat):
    old = {"street": None, "district": None}
    assert duplicates.is_duplicate(flat(1, **old), flat(2, **old))
    assert not duplicates.is_duplicate(flat(1, **old), flat(2, **old, location="Warszawa, Mokotów, Sielce"))


def test_groups_are_transitive_and_the_first_seen_listing_is_primary(flat):
    listings = [
        flat(3, area=50.8),
        flat(1, area=50.0),
        flat(2, area=50.4),
        flat(4, area=51.6),  # 0.8 m² from listing 3 → same group through 3
        flat(5, area=70.0),  # different flat
    ]

    assert duplicates.find_duplicates(listings) == {2: 1, 3: 1, 4: 1}


def test_refresh_duplicates_updates_the_database(db, make_listing):
    for listing_id, area in ((1, 50.0), (2, 50.5), (3, 80.0)):
        database.add_listing(make_listing(listing_id, area=area, street="Iberyjska"))

    assert duplicates.refresh_duplicates() == 1
    assert [listing.id for listing in database.duplicate_members()[1]] == [2]

    database.update_price(2, price=900_000, price_per_m2=None)  # no longer similar
    assert duplicates.refresh_duplicates() == 1
    assert database.duplicate_members() == {}
