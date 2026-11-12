"""Detect the same flat listed several times, e.g. by different real-estate agencies.

Two listings are treated as the same flat when they share the floor, their areas differ by at most
1 m², their prices by less than 5% and they lie in the same place (street, otherwise district,
otherwise the whole location label). Groups are transitive; the listing seen first is the primary.
"""

from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from collections.abc import Sequence
from typing import TYPE_CHECKING

import database
from services.geo import distance_km

if TYPE_CHECKING:
    from database import Listing

AREA_TOLERANCE_M2 = 1.0
MAX_PRICE_DIFFERENCE = 0.05
# Otodom coordinates may be approximate, but the same flat is never this far apart.
MAX_DISTANCE_KM = 1.5

_PLACE_PREFIXES = re.compile(r"^(ul|ulica|al|aleja|aleje|os|osiedle|pl|plac|rondo)\b\.?\s*")


def normalize_place(text: str | None) -> str | None:
    """'ul. Żeglugi Wiślanej' -> 'zeglugi wislanej'."""
    if not text:
        return None
    text = text.lower().replace("ł", "l")
    text = "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))
    text = _PLACE_PREFIXES.sub("", text.strip())
    text = re.sub(r"[^a-z0-9]+", " ", text).strip()
    return text or None


def is_duplicate(a: Listing, b: Listing) -> bool:
    if a.area is None or b.area is None or abs(a.area - b.area) > AREA_TOLERANCE_M2:
        return False
    if a.floor is None or a.floor != b.floor:
        return False
    if not a.price or not b.price or abs(a.price - b.price) / min(a.price, b.price) >= MAX_PRICE_DIFFERENCE:
        return False
    return _same_place(a, b)


def _same_place(a: Listing, b: Listing) -> bool:
    if _city(a) != _city(b):
        return False
    if (
        a.latitude is not None
        and a.longitude is not None
        and b.latitude is not None
        and b.longitude is not None
        and distance_km(a.latitude, a.longitude, b.latitude, b.longitude) > MAX_DISTANCE_KM
    ):
        return False
    if a.street and b.street:
        return normalize_place(a.street) == normalize_place(b.street)
    if a.district and b.district:
        return normalize_place(a.district) == normalize_place(b.district)
    # Listings saved before v1.1 have neither street nor district – require the same full location.
    return a.location is not None and normalize_place(a.location) == normalize_place(b.location)


def _city(listing: Listing) -> str | None:
    """Location labels start with the city, e.g. 'Warszawa, Mokotów, Stegny'."""
    return normalize_place((listing.location or "").split(",")[0])


def find_duplicates(listings: Sequence[Listing]) -> dict[int, int]:
    """Return {duplicate_id: primary_id} for every listing that belongs to a group of 2+ listings."""
    parent = {listing.id: listing.id for listing in listings}

    def root(listing_id: int) -> int:
        while parent[listing_id] != listing_id:
            parent[listing_id] = parent[parent[listing_id]]
            listing_id = parent[listing_id]
        return listing_id

    # Only listings on the same floor can match, and within a floor only neighbours by area.
    by_floor: dict[str, list[Listing]] = defaultdict(list)
    for listing in listings:
        if listing.floor and listing.area is not None and listing.price:
            by_floor[listing.floor].append(listing)
    for same_floor in by_floor.values():
        same_floor.sort(key=lambda listing: listing.area or 0.0)
        for i, a in enumerate(same_floor):
            for b in same_floor[i + 1 :]:
                if (b.area or 0.0) - (a.area or 0.0) > AREA_TOLERANCE_M2:
                    break
                if is_duplicate(a, b):
                    parent[root(b.id)] = root(a.id)

    groups: dict[int, list[Listing]] = defaultdict(list)
    for listing in listings:
        groups[root(listing.id)].append(listing)

    primary_of: dict[int, int] = {}
    for members in groups.values():
        if len(members) < 2:
            continue
        primary = min(members, key=lambda listing: (listing.first_seen_at, listing.id))
        primary_of.update({member.id: primary.id for member in members if member.id != primary.id})
    return primary_of


def refresh_duplicates() -> int:
    """Recompute duplicate groups for the whole database; returns the number of changed listings."""
    return database.save_duplicate_groups(find_duplicates(database.all_listings()))
