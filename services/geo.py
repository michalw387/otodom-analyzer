"""Distance and rough travel time from a listing to the buyer's target address (work, school…).

Listings already carry coordinates from Otodom, so only the target address is geocoded
(Nominatim / OpenStreetMap, once per settings change). Travel times are heuristic estimates
derived from the straight-line distance – no routing service is involved.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

from geopy.distance import geodesic
from geopy.exc import GeopyError
from geopy.geocoders import Nominatim

import config
from formatting import decimal

if TYPE_CHECKING:
    from database import Listing

# Real routes are roughly 30% longer than the straight line between two points in a city.
ROUTE_FACTOR = 1.3


@dataclass(frozen=True)
class TravelMode:
    label: str  # used as "dojazd {label}"
    speed_kmh: float  # average door-to-door speed along the route
    overhead_minutes: int  # waiting, transfers, parking…


TRAVEL_MODES = {
    "transit": TravelMode("komunikacją miejską", speed_kmh=18, overhead_minutes=8),
    "car": TravelMode("samochodem", speed_kmh=28, overhead_minutes=5),
    "bike": TravelMode("rowerem", speed_kmh=14, overhead_minutes=2),
    "walk": TravelMode("pieszo", speed_kmh=4.5, overhead_minutes=0),
}


class GeocodingError(Exception):
    """The target address could not be geocoded."""


@dataclass(frozen=True)
class GeocodedAddress:
    latitude: float
    longitude: float
    display_name: str


class Geocoder(Protocol):
    def geocode(self, query: str, **kwargs: Any) -> Any: ...


@dataclass(frozen=True)
class Commute:
    km: float
    minutes: int
    target_label: str
    mode: TravelMode

    def describe(self) -> str:
        return (
            f"{decimal(round(self.km, 1))} km w linii prostej do: {self.target_label}, "
            f"szacowany dojazd {self.mode.label} ok. {self.minutes} min"
        )

    def short(self) -> str:
        return f"{decimal(round(self.km, 1))} km do: {self.target_label} · ok. {self.minutes} min {self.mode.label}"


def geocode(address: str, geocoder: Geocoder | None = None) -> GeocodedAddress:
    geocoder = geocoder or Nominatim(user_agent=config.NOMINATIM_USER_AGENT, timeout=10)
    try:
        location = geocoder.geocode(address, country_codes="pl", language="pl")
    except GeopyError as exc:
        raise GeocodingError(f"Geokodowanie adresu nie powiodło się: {exc}") from exc
    if location is None:
        raise GeocodingError(f"Nie znaleziono adresu: {address}")
    return GeocodedAddress(location.latitude, location.longitude, location.address)


def distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    return float(geodesic((lat1, lon1), (lat2, lon2)).km)


def travel_minutes(km: float, mode: str) -> int:
    travel_mode = TRAVEL_MODES[mode]
    return round(km * ROUTE_FACTOR / travel_mode.speed_kmh * 60 + travel_mode.overhead_minutes)


def commute(listing: Listing, target: dict[str, Any] | None) -> Commute | None:
    """Commute from the listing to the configured target, or None when either location is unknown."""
    if not target or target.get("latitude") is None or target.get("longitude") is None:
        return None
    if listing.latitude is None or listing.longitude is None:
        return None
    mode = str(target.get("mode"))
    if mode not in TRAVEL_MODES:
        mode = "transit"
    km = distance_km(listing.latitude, listing.longitude, target["latitude"], target["longitude"])
    return Commute(
        km=km,
        minutes=travel_minutes(km, mode),
        target_label=target.get("label") or "cel",
        mode=TRAVEL_MODES[mode],
    )
