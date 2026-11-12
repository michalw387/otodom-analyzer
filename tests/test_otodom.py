from __future__ import annotations

import json
from urllib.parse import parse_qs, urlsplit

import pytest
from curl_cffi.requests.exceptions import RequestException

from scraper import otodom, utils


def _next_data_html(page_props: dict) -> str:
    payload = json.dumps({"props": {"pageProps": page_props}})
    return f'<html><body><script id="__NEXT_DATA__" type="application/json">{payload}</script></body></html>'


def test_build_search_url_from_filters():
    url = otodom.build_search_url(
        {
            "location": "/mazowieckie/warszawa/warszawa/warszawa/mokotow/",
            "radius_km": 5,
            "price_min": None,
            "price_max": 900_000,
            "area_min": 40,
            "area_max": None,
            "rooms_min": 2,
            "rooms_max": 3,
            "price_per_m2_max": 16_000.0,
            "market": "SECONDARY",
        },
        page=2,
    )
    parts = urlsplit(url)
    query = parse_qs(parts.query)
    assert parts.path == "/pl/wyniki/sprzedaz/mieszkanie/mazowieckie/warszawa/warszawa/warszawa/mokotow"
    assert query["priceMax"] == ["900000"]
    assert query["areaMin"] == ["40"]
    assert query["pricePerMeterMax"] == ["16000"]
    assert query["roomsNumber"] == ["[TWO,THREE]"]
    assert query["market"] == ["SECONDARY"]
    assert query["distanceRadius"] == ["5"]
    assert query["by"] == ["LATEST"] and query["direction"] == ["DESC"] and query["page"] == ["2"]
    assert "priceMin" not in query and "areaMax" not in query


def test_build_search_url_skips_default_market_and_full_room_range():
    url = otodom.build_search_url({"location": "malopolskie/krakow/krakow/krakow", "rooms_min": 1, "rooms_max": 10})
    query = parse_qs(urlsplit(url).query)
    assert "roomsNumber" not in query and "market" not in query and "distanceRadius" not in query


def test_custom_url_takes_precedence_and_is_sorted_by_newest():
    custom = "https://www.otodom.pl/pl/wyniki/sprzedaz/mieszkanie/pomorskie/gdansk?priceMax=500000&by=PRICE&page=7"
    query = parse_qs(urlsplit(otodom.build_search_url({"custom_url": custom, "location": "x"}, page=1)).query)
    assert query["priceMax"] == ["500000"]
    assert query["by"] == ["LATEST"] and query["direction"] == ["DESC"] and query["page"] == ["1"]


@pytest.mark.parametrize(
    ("rooms_min", "rooms_max", "expected"),
    [
        (1, 10, []),
        (2, 3, ["TWO", "THREE"]),
        (9, 10, ["NINE", "TEN", "MORE"]),
        (1, 1, ["ONE"]),
    ],
)
def test_room_values(rooms_min, rooms_max, expected):
    assert otodom.room_values(rooms_min, rooms_max) == expected


def test_parse_search_results_skips_injected_items():
    html = _next_data_html(
        {
            "data": {
                "searchAds": {
                    "items": [
                        {
                            "id": 1,
                            "slug": "a-ID1",
                            "estate": "FLAT",
                            "href": "[lang]/ad/a-ID1",
                            "totalPrice": {"value": 799000, "currency": "PLN"},
                            "pricePerSquareMeter": {"value": 11979.01, "currency": "PLN"},
                        },
                        {"id": 2, "slug": "b-ID2", "estate": "FLAT", "href": "[lang]/ad/b-ID2", "hidePrice": True,
                         "totalPrice": {"value": 1, "currency": "PLN"}},
                        {"id": 9000000001, "slug": "a-ID1", "estate": "FLAT", "href": "hpr/[lang]/ad/b-ID2"},
                        {"id": 3, "slug": "inv-ID3", "estate": "INVESTMENT", "href": "[lang]/ad/inv-ID3"},
                    ],
                    "pagination": {"totalPages": 4},
                }
            }
        }
    )
    results, total_pages = otodom.parse_search_results(otodom.extract_next_data(html))
    assert results == [
        {"id": 1, "url": "https://www.otodom.pl/pl/oferta/a-ID1", "price": 799000.0, "price_per_m2": 11979.01},
        {"id": 2, "url": "https://www.otodom.pl/pl/oferta/b-ID2", "price": None, "price_per_m2": None},
    ]
    assert total_pages == 4


def test_parse_listing_maps_fields():
    ad = {
        "id": 68555171,
        "title": " Metro | 2 pokoje ",
        "url": "https://www.otodom.pl/pl/oferta/metro-ID4DEkz",
        "description": "<p>Mieszkanie <b>do wprowadzenia</b>.</p><p>Blisko metra.<br>Czynsz 1150 zł.</p>",
        "createdAt": "2026-10-22T12:10:47Z",
        "attributes": {
            "build_year": "1982",
            "building_floors_num": "4",
            "floor_no": "floor_4",
            "heating": "urban",
            "m": "47",
            "market": "secondary",
            "rooms_num": "2",
            "extras_types": ["balcony", "basement"],
        },
        "characteristics": [
            {"key": "price", "value": "699000"},
            {"key": "rent", "value": "1150"},
            {"key": "m", "value": "999"},  # "attributes" wins over "characteristics"
        ],
        "images": [{"medium": "https://img/medium.jpg", "large": "https://img/large.jpg"}],
        "location": {
            "coordinates": {"latitude": 52.1505, "longitude": 21.0451},
            "address": {"street": {"name": "Stefana Szolc-Rogozińskiego", "number": None}},
            "reverseGeocoding": {
                "locations": [
                    {"locationLevel": "voivodeship", "name": "mazowieckie"},
                    {"locationLevel": "city_or_village", "name": "Warszawa"},
                    {"locationLevel": "district", "name": "Ursynów"},
                ]
            },
        },
    }
    listing = otodom.parse_listing(otodom.extract_next_data(_next_data_html({"ad": ad})), "fallback-url")

    assert listing["id"] == 68555171
    assert listing["title"] == "Metro | 2 pokoje"
    assert listing["price"] == 699_000.0
    assert listing["area"] == 47.0
    assert listing["price_per_m2"] == round(699_000 / 47, 2)  # computed when missing
    assert listing["rooms"] == 2
    assert listing["floor"] == "4" and listing["building_floors"] == 4
    assert listing["heating"] == "miejskie"
    assert listing["market"] == "wtórny"
    assert listing["rent"] == 1150.0
    assert listing["location"] == "Warszawa, Ursynów, Stefana Szolc-Rogozińskiego"
    assert listing["district"] == "Ursynów"
    assert listing["street"] == "Stefana Szolc-Rogozińskiego"
    assert (listing["latitude"], listing["longitude"]) == (52.1505, 21.0451)
    assert listing["image_url"] == "https://img/medium.jpg"
    assert listing["description"] == "Mieszkanie do wprowadzenia.\nBlisko metra.\nCzynsz 1150 zł."
    assert listing["attributes"]["extras_types"] == ["balcony", "basement"]
    assert listing["created_at"] is not None


def test_parse_listing_without_ad_raises():
    with pytest.raises(otodom.ParseError):
        otodom.parse_listing({"props": {"pageProps": {}}}, "https://www.otodom.pl/pl/oferta/x")


def test_extract_next_data_missing_raises():
    with pytest.raises(otodom.ParseError):
        otodom.extract_next_data("<html><body>Access denied</body></html>")


class FakeResponse:
    def __init__(self, status_code: int, text: str = "", headers: dict[str, str] | None = None) -> None:
        self.status_code = status_code
        self.text = text
        self.headers = headers or {}


class FakeSession:
    """Stands in for curl_cffi.requests.Session; replays responses (or raises exceptions) in order."""

    def __init__(self, *responses: FakeResponse | Exception) -> None:
        self._responses = iter(responses)
        self.calls = 0

    def get(self, url: str) -> FakeResponse:
        self.calls += 1
        response = next(self._responses)
        if isinstance(response, Exception):
            raise response
        return response


@pytest.fixture
def no_sleep(monkeypatch):
    monkeypatch.setattr(utils.time, "sleep", lambda _seconds: None)


def test_fetch_html_retries_on_rate_limit_and_network_errors(no_sleep):
    session = FakeSession(
        FakeResponse(429, headers={"Retry-After": "1"}), RequestException("connection reset"), FakeResponse(200, "ok")
    )
    assert utils.fetch_html(session, "https://www.otodom.pl/x") == "ok"
    assert session.calls == 3


def test_fetch_html_gives_up_after_max_attempts(no_sleep):
    session = FakeSession(*[FakeResponse(503)] * utils.MAX_ATTEMPTS)
    with pytest.raises(utils.FetchError, match="503"):
        utils.fetch_html(session, "https://www.otodom.pl/x")


def test_fetch_html_does_not_retry_not_found(no_sleep):
    session = FakeSession(FakeResponse(404))
    with pytest.raises(utils.FetchError, match="404"):
        utils.fetch_html(session, "https://www.otodom.pl/x")
    assert session.calls == 1


def test_fetch_html_falls_back_to_browser_when_blocked(no_sleep, monkeypatch):
    monkeypatch.setattr(utils, "fetch_with_browser", lambda url: f"browser:{url}")
    assert utils.fetch_html(FakeSession(FakeResponse(403)), "https://www.otodom.pl/x") == "browser:https://www.otodom.pl/x"


def test_create_client_impersonates_a_browser():
    with utils.create_client() as session:
        assert session.impersonate in utils.IMPERSONATE_TARGETS
        assert session.headers["Accept-Language"].startswith("pl-PL")
