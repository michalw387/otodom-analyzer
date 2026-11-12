from __future__ import annotations

from types import SimpleNamespace

import pytest
from geopy.exc import GeocoderTimedOut

from database import Listing
from services import geo

PALACE_OF_CULTURE = (52.2318, 21.0060)
OLD_TOWN_SQUARE = (52.2497, 21.0122)
TARGET = {"label": "Praca", "mode": "transit", "latitude": OLD_TOWN_SQUARE[0], "longitude": OLD_TOWN_SQUARE[1]}


class FakeGeocoder:
    def __init__(self, result=None, error: Exception | None = None) -> None:
        self.result, self.error, self.calls = result, error, []

    def geocode(self, query, **kwargs):
        self.calls.append((query, kwargs))
        if self.error:
            raise self.error
        return self.result


def test_distance_between_known_points():
    assert geo.distance_km(*PALACE_OF_CULTURE, *OLD_TOWN_SQUARE) == pytest.approx(2.03, abs=0.05)


@pytest.mark.parametrize(
    ("km", "mode", "minutes"),
    [(10, "transit", 51), (10, "car", 33), (2, "walk", 35), (0, "bike", 2)],
)
def test_travel_minutes(km, mode, minutes):
    assert geo.travel_minutes(km, mode) == minutes


def test_commute_from_listing_to_target(make_listing):
    listing = Listing(**make_listing(1, latitude=PALACE_OF_CULTURE[0], longitude=PALACE_OF_CULTURE[1]))

    trip = geo.commute(listing, TARGET)

    assert trip is not None and trip.km == pytest.approx(2.03, abs=0.05) and trip.minutes == 17
    assert trip.describe() == (
        "2 km w linii prostej do: Praca, szacowany dojazd komunikacją miejską ok. 17 min"
    )
    assert trip.short() == "2 km do: Praca · ok. 17 min komunikacją miejską"


@pytest.mark.parametrize(
    ("listing_coordinates", "target"),
    [
        ({"latitude": None, "longitude": None}, TARGET),
        ({"latitude": 52.2, "longitude": 21.0}, {**TARGET, "latitude": None}),
        ({"latitude": 52.2, "longitude": 21.0}, None),
    ],
)
def test_commute_needs_both_locations(make_listing, listing_coordinates, target):
    assert geo.commute(Listing(**make_listing(1, **listing_coordinates)), target) is None


def test_geocode_biases_to_poland():
    geocoder = FakeGeocoder(SimpleNamespace(latitude=52.25, longitude=21.01, address="Rynek Starego Miasta, Warszawa"))

    result = geo.geocode("Rynek Starego Miasta 1", geocoder)

    assert result == geo.GeocodedAddress(52.25, 21.01, "Rynek Starego Miasta, Warszawa")
    assert geocoder.calls == [("Rynek Starego Miasta 1", {"country_codes": "pl", "language": "pl"})]


def test_geocode_errors():
    with pytest.raises(geo.GeocodingError, match="Nie znaleziono"):
        geo.geocode("xyz", FakeGeocoder(None))
    with pytest.raises(geo.GeocodingError, match="nie powiodło się"):
        geo.geocode("xyz", FakeGeocoder(error=GeocoderTimedOut("timeout")))
