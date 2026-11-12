"""Searching and parsing apartment listings from Otodom.pl.

Otodom is a Next.js app: the full search-result and listing data is embedded in
<script id="__NEXT_DATA__"> as JSON. Reading that JSON instead of the HTML layout makes
the scraper far more resilient to visual redesigns.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from bs4 import BeautifulSoup, SoupStrainer
from curl_cffi import requests as curl_requests

from scraper.utils import ScraperError, fetch_html

BASE_URL = "https://www.otodom.pl"
SEARCH_PATH = "/pl/wyniki/sprzedaz/mieszkanie/"
RADIUS_OPTIONS_KM = [0, 5, 10, 15, 25, 50, 75]
MARKETS = {"ALL": "dowolny", "PRIMARY": "pierwotny", "SECONDARY": "wtórny"}
LOCATION_PATTERN = re.compile(r"^[a-z0-9-]+(/[a-z0-9-]+)*$")
MAX_ROOMS = 10  # Otodom: ONE … TEN, plus MORE for larger flats

_ROOM_VALUES = ["ONE", "TWO", "THREE", "FOUR", "FIVE", "SIX", "SEVEN", "EIGHT", "NINE", "TEN"]
_FLOORS = {
    "cellar": "suterena",
    "ground_floor": "parter",
    "floor_higher_10": "powyżej 10",
    "garret": "poddasze",
}
_HEATING = {
    "urban": "miejskie",
    "gas": "gazowe",
    "electrical": "elektryczne",
    "boiler_room": "kotłownia",
    "tiled_stove": "piece kaflowe",
    "other": "inne",
}
_MARKET_LABELS = {"primary": "pierwotny", "secondary": "wtórny"}
_SKIPPED_LOCATION_LEVELS = {"voivodeship", "county", "municipality"}


class ParseError(ScraperError):
    """The page lacks the expected data."""


# --- Search --------------------------------------------------------------------


def build_search_url(filters: dict[str, Any], page: int = 1) -> str:
    """Search URL sorted newest first. The user's custom URL takes precedence over the filters."""
    custom_url = (filters.get("custom_url") or "").strip()
    if custom_url:
        parts = urlsplit(custom_url)
        query = dict(parse_qsl(parts.query))
        query.update(by="LATEST", direction="DESC", page=str(page))
        return urlunsplit(parts._replace(query=urlencode(query)))

    params: dict[str, Any] = {}
    for key, param in (
        ("price_min", "priceMin"),
        ("price_max", "priceMax"),
        ("area_min", "areaMin"),
        ("area_max", "areaMax"),
        ("price_per_m2_max", "pricePerMeterMax"),
    ):
        if filters.get(key):
            params[param] = _plain_number(filters[key])
    rooms = room_values(filters.get("rooms_min") or 1, filters.get("rooms_max") or MAX_ROOMS)
    if rooms:
        params["roomsNumber"] = "[" + ",".join(rooms) + "]"
    if filters.get("market", "ALL") != "ALL":
        params["market"] = filters["market"]
    if filters.get("radius_km"):
        params["distanceRadius"] = filters["radius_km"]
    params.update(by="LATEST", direction="DESC", limit=36, page=page)
    location = filters["location"].strip().strip("/")
    return f"{BASE_URL}{SEARCH_PATH}{location}?{urlencode(params)}"


def room_values(rooms_min: int, rooms_max: int) -> list[str]:
    """roomsNumber values; empty when the range covers every room count."""
    if rooms_min <= 1 and rooms_max >= MAX_ROOMS:
        return []
    values = _ROOM_VALUES[rooms_min - 1 : rooms_max]
    return [*values, "MORE"] if rooms_max >= MAX_ROOMS else values


def search_page(
    client: curl_requests.Session, filters: dict[str, Any], page: int = 1
) -> tuple[list[dict[str, Any]], int]:
    """Return ([{id, url, price, price_per_m2}, …], total pages) for one results page."""
    html = fetch_html(client, build_search_url(filters, page))
    return parse_search_results(extract_next_data(html))


def parse_search_results(next_data: dict[str, Any]) -> tuple[list[dict[str, Any]], int]:
    try:
        search_ads = next_data["props"]["pageProps"]["data"]["searchAds"]
    except (KeyError, TypeError) as exc:
        raise ParseError("Nieoczekiwana struktura strony wyników Otodom") from exc

    results = []
    for item in search_ads.get("items") or []:
        # Otodom interleaves recommendations and developer projects – they have another type or link.
        if item.get("estate") != "FLAT" or not str(item.get("href", "")).startswith("[lang]/ad/"):
            continue
        results.append(
            {
                "id": int(item["id"]),
                "url": f"{BASE_URL}/pl/oferta/{item['slug']}",
                # Search results carry the current price – enough to detect price changes of known listings.
                "price": None if item.get("hidePrice") else _money_value(item.get("totalPrice")),
                "price_per_m2": _money_value(item.get("pricePerSquareMeter")),
            }
        )
    total_pages = (search_ads.get("pagination") or {}).get("totalPages") or 1
    return results, total_pages


# --- Listing -------------------------------------------------------------------


def fetch_listing(client: curl_requests.Session, url: str) -> dict[str, Any]:
    return parse_listing(extract_next_data(fetch_html(client, url)), url)


def parse_listing(next_data: dict[str, Any], url: str) -> dict[str, Any]:
    """Map listing data to a dict matching the database.Listing columns."""
    try:
        ad = next_data["props"]["pageProps"]["ad"]
        listing_id = int(ad["id"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ParseError(f"Brak danych ogłoszenia: {url}") from exc

    attrs = dict(ad.get("attributes") or {})
    # "characteristics" fills in parameters missing from "attributes" (e.g. price and rent).
    for item in ad.get("characteristics") or []:
        if item.get("key"):
            attrs.setdefault(item["key"], item.get("value"))

    price = _to_float(attrs.get("price"))
    area = _to_float(attrs.get("m"))
    price_per_m2 = _to_float(attrs.get("price_per_m"))
    if price_per_m2 is None and price and area:
        price_per_m2 = round(price / area, 2)
    images = ad.get("images") or []
    location = ad.get("location") or {}
    coordinates = location.get("coordinates") or {}

    return {
        "id": listing_id,
        "url": ad.get("url") or url,
        "title": (ad.get("title") or "").strip() or f"Ogłoszenie {listing_id}",
        "price": price,
        "price_per_m2": price_per_m2,
        "area": area,
        "rooms": _to_int(attrs.get("rooms_num")),
        "floor": _floor_label(attrs.get("floor_no")),
        "building_floors": _to_int(attrs.get("building_floors_num")),
        "build_year": _to_int(attrs.get("build_year")),
        "heating": _translate(_HEATING, attrs.get("heating")),
        "rent": _to_float(attrs.get("rent")),
        "market": _translate(_MARKET_LABELS, attrs.get("market")),
        "location": _location_label(location),
        "district": _district(location),
        "street": (((location.get("address") or {}).get("street")) or {}).get("name") or None,
        "latitude": _to_float(coordinates.get("latitude")),
        "longitude": _to_float(coordinates.get("longitude")),
        "description": html_to_text(ad.get("description") or ""),
        "image_url": (images[0].get("medium") or images[0].get("large")) if images else None,
        "attributes": attrs,
        "created_at": _parse_datetime(ad.get("createdAt")),
    }


# --- Helpers -------------------------------------------------------------------


def extract_next_data(html: str) -> dict[str, Any]:
    only_next_data = SoupStrainer("script", id="__NEXT_DATA__")
    tag = BeautifulSoup(html, "html.parser", parse_only=only_next_data).find("script")
    if tag is None or not tag.string:
        raise ParseError(
            "Strona nie zawiera danych __NEXT_DATA__ (zmiana struktury Otodom lub blokada antybotowa)"
        )
    try:
        return json.loads(tag.string)
    except json.JSONDecodeError as exc:
        raise ParseError("Niepoprawny JSON w __NEXT_DATA__") from exc


def html_to_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for br in soup.find_all("br"):
        br.replace_with("\n")
    for block in soup.find_all(["p", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6"]):
        block.append("\n")
    lines = (line.strip() for line in soup.get_text().splitlines())
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def _location_label(location: dict[str, Any]) -> str | None:
    levels = (location.get("reverseGeocoding") or {}).get("locations") or []
    parts = [lvl.get("name") for lvl in levels if lvl.get("locationLevel") not in _SKIPPED_LOCATION_LEVELS]
    street = ((location.get("address") or {}).get("street")) or {}
    if street.get("name"):
        parts.append(" ".join(str(p) for p in (street["name"], street.get("number")) if p))
    return ", ".join(p for p in parts if p) or None


def _district(location: dict[str, Any]) -> str | None:
    levels = (location.get("reverseGeocoding") or {}).get("locations") or []
    return next((lvl.get("name") for lvl in levels if lvl.get("locationLevel") == "district"), None)


def _money_value(money: Any) -> float | None:
    return _to_float(money.get("value")) if isinstance(money, dict) else None


def _floor_label(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    if value.startswith("floor_") and value[6:].isdigit():
        return value[6:]
    return _FLOORS.get(value, value)


def _translate(mapping: dict[str, str], value: Any) -> str | None:
    if value is None or value == "":
        return None
    if isinstance(value, list):
        return ", ".join(mapping.get(v, str(v)) for v in value) or None
    return mapping.get(value, str(value))


def _to_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_int(value: Any) -> int | None:
    number = _to_float(value)
    return int(number) if number is not None else None


def _plain_number(value: float) -> str:
    number = float(value)
    return str(int(number)) if number.is_integer() else str(number)


def _parse_datetime(value: Any) -> datetime | None:
    """Otodom timestamp (UTC) as naive local time – like every other date in the database."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value).astimezone().replace(tzinfo=None)
    except (TypeError, ValueError):
        return None
