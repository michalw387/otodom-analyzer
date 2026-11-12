"""SQLite database (SQLAlchemy): listings with AI scores, user settings, scan history and token usage."""

from __future__ import annotations

import copy
import threading
from collections import defaultdict
from collections.abc import Iterable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, cast

from sqlalchemy import (
    JSON,
    ColumnElement,
    CursorResult,
    Engine,
    Text,
    create_engine,
    delete,
    event,
    func,
    inspect,
    nulls_last,
    or_,
    select,
    text,
    update,
)
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

import config

FLAGS = ("favorite", "rejected", "contacted")
# Settings no longer used – removed on start-up (anthropic_api_key: AI now runs through Claude Code).
OBSOLETE_SETTINGS = ("anthropic_api_key",)
SORT_OPTIONS = {
    "score": "Ocena AI (najlepsze)",
    "price": "Cena (rosnąco)",
    "price_per_m2": "Cena za m² (rosnąco)",
    "newest": "Data dodania (najnowsze)",
}


class Base(DeclarativeBase):
    pass


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(primary_key=True)
    value: Mapped[Any] = mapped_column(JSON)


class Listing(Base):
    __tablename__ = "listings"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=False)  # Otodom listing ID
    url: Mapped[str]
    title: Mapped[str]
    price: Mapped[float | None]
    price_per_m2: Mapped[float | None]
    area: Mapped[float | None]
    rooms: Mapped[int | None]
    floor: Mapped[str | None]
    building_floors: Mapped[int | None]
    build_year: Mapped[int | None]
    heating: Mapped[str | None]
    rent: Mapped[float | None]
    market: Mapped[str | None]
    location: Mapped[str | None]
    description: Mapped[str] = mapped_column(Text, default="")
    image_url: Mapped[str | None]
    attributes: Mapped[dict] = mapped_column(JSON, default=dict)  # all raw parameters from Otodom
    created_at: Mapped[datetime | None]  # when the listing was posted on Otodom
    first_seen_at: Mapped[datetime] = mapped_column(default=datetime.now)

    favorite: Mapped[bool] = mapped_column(default=False)
    rejected: Mapped[bool] = mapped_column(default=False)
    contacted: Mapped[bool] = mapped_column(default=False)

    score: Mapped[int | None] = mapped_column(index=True)
    analysis: Mapped[dict | None] = mapped_column(JSON)  # pros, cons, summary, consistency
    ai_model: Mapped[str | None]
    evaluated_at: Mapped[datetime | None]
    ai_error: Mapped[str | None] = mapped_column(Text)
    ai_attempts: Mapped[int] = mapped_column(default=0)

    # Added in v1.1 – must stay nullable so _add_missing_columns() can migrate older databases.
    district: Mapped[str | None]
    street: Mapped[str | None]
    latitude: Mapped[float | None]
    longitude: Mapped[float | None]
    previous_price: Mapped[float | None]
    price_changed_at: Mapped[datetime | None]
    duplicate_of: Mapped[int | None]  # ID of the group's primary listing
    notified_at: Mapped[datetime | None]
    price_checked_at: Mapped[datetime | None]


class ScanRun(Base):
    __tablename__ = "scan_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    started_at: Mapped[datetime] = mapped_column(default=datetime.now)
    finished_at: Mapped[datetime | None]
    found: Mapped[int] = mapped_column(default=0)
    new: Mapped[int] = mapped_column(default=0)
    evaluated: Mapped[int] = mapped_column(default=0)
    error: Mapped[str | None] = mapped_column(Text)
    price_drops: Mapped[int | None]  # added in v1.1


class TokenUsage(Base):
    __tablename__ = "token_usage"

    id: Mapped[int] = mapped_column(primary_key=True)
    used_at: Mapped[datetime] = mapped_column(default=datetime.now, index=True)
    model: Mapped[str]
    listing_id: Mapped[int | None]
    tokens: Mapped[int]


_Session = sessionmaker(expire_on_commit=False)
_engine: Engine | None = None
_init_lock = threading.Lock()


def init_db(db_path: Path | str = config.DB_PATH) -> None:
    """Create or migrate the database. Calling again with the same path is a no-op."""
    global _engine
    db_path = Path(db_path)
    with _init_lock:
        if _engine is not None and _engine.url.database == str(db_path):
            return
        if _engine is not None:
            _engine.dispose()
        db_path.parent.mkdir(parents=True, exist_ok=True)
        # The scheduler writes from a background thread while Streamlit reads from session threads:
        # WAL allows reads during a write, and the timeout waits for the write lock.
        engine = create_engine(
            f"sqlite:///{db_path}", connect_args={"check_same_thread": False, "timeout": 30}
        )
        event.listen(engine, "connect", _enable_wal)
        Base.metadata.create_all(engine)
        _add_missing_columns(engine)
        with engine.begin() as connection:
            connection.execute(delete(Setting).where(Setting.key.in_(OBSOLETE_SETTINGS)))
        _Session.configure(bind=engine)
        _engine = engine


def _enable_wal(dbapi_connection: Any, _connection_record: Any) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.close()


def _add_missing_columns(engine: Engine) -> None:
    """Add columns introduced after a table was created (create_all only creates missing tables)."""
    inspector = inspect(engine)
    with engine.begin() as connection:
        for table in Base.metadata.sorted_tables:
            existing = {column["name"] for column in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in existing:
                    continue
                if not column.nullable:
                    raise RuntimeError(f"Cannot auto-migrate non-nullable column {table.name}.{column.name}")
                column_type = column.type.compile(dialect=engine.dialect)
                connection.execute(text(f'ALTER TABLE "{table.name}" ADD COLUMN "{column.name}" {column_type}'))


# --- Settings ---------------------------------------------------------------------


def get_settings() -> dict[str, Any]:
    """Settings stored in the database layered over the defaults from config.py."""
    settings = copy.deepcopy(config.DEFAULT_SETTINGS)
    with _Session() as session:
        for row in session.scalars(select(Setting)):
            default = settings.get(row.key)
            if isinstance(default, dict) and isinstance(row.value, dict):
                default.update(row.value)
            else:
                settings[row.key] = row.value
    return settings


def save_settings(values: dict[str, Any]) -> None:
    with _Session.begin() as session:
        for key, value in values.items():
            session.merge(Setting(key=key, value=value))


# --- Listings ---------------------------------------------------------------------


def known_prices(ids: list[int]) -> dict[int, float | None]:
    """Current price of every listing from `ids` that is already in the database."""
    if not ids:
        return {}
    statement = select(Listing.id, Listing.price).where(Listing.id.in_(ids))
    with _Session() as session:
        return {listing_id: price for listing_id, price in session.execute(statement)}


def add_listing(data: dict[str, Any]) -> bool:
    """Insert a new listing. An existing one is left untouched (including user flags)."""
    statement = sqlite_insert(Listing).values(**data).on_conflict_do_nothing(index_elements=["id"])
    with _Session.begin() as session:
        return cast(CursorResult[Any], session.execute(statement)).rowcount == 1


def get_listing(listing_id: int) -> Listing | None:
    with _Session() as session:
        return session.get(Listing, listing_id)


def listings_by_ids(ids: Iterable[int]) -> list[Listing]:
    with _Session() as session:
        return list(session.scalars(select(Listing).where(Listing.id.in_(list(ids))).order_by(Listing.id)))


def all_listings() -> list[Listing]:
    with _Session() as session:
        return list(session.scalars(select(Listing)))


def update_price(listing_id: int, *, price: float, price_per_m2: float | None) -> None:
    """Store a new price and remember the previous one (the SET clause reads the old row values)."""
    _update_listing(
        listing_id,
        previous_price=Listing.price,
        price=price,
        price_per_m2=price_per_m2 if price_per_m2 is not None else func.round(price / Listing.area, 2),
        price_changed_at=datetime.now(),
    )


def mark_price_checked(ids: list[int]) -> None:
    if ids:
        with _Session.begin() as session:
            session.execute(update(Listing).where(Listing.id.in_(ids)).values(price_checked_at=datetime.now()))


def listings_to_recheck(*, min_score: int, limit: int) -> list[Listing]:
    """Watched listings (favourite, contacted or scoring ≥ min_score) whose price wasn't checked recently."""
    last_check = func.coalesce(Listing.price_checked_at, Listing.first_seen_at)
    statement = (
        select(Listing)
        .where(
            Listing.rejected.is_(False),
            or_(Listing.favorite.is_(True), Listing.contacted.is_(True), Listing.score >= min_score),
            last_check < datetime.now() - timedelta(hours=config.PRICE_RECHECK_INTERVAL_HOURS),
        )
        .order_by(last_check.asc())
        .limit(limit)
    )
    with _Session() as session:
        return list(session.scalars(statement))


def listings_to_evaluate(limit: int) -> list[Listing]:
    """Listings not yet scored by AI (excluding rejected ones and those out of attempts)."""
    statement = (
        select(Listing)
        .where(
            Listing.evaluated_at.is_(None),
            Listing.rejected.is_(False),
            Listing.ai_attempts < config.MAX_AI_ATTEMPTS,
        )
        .order_by(Listing.first_seen_at.desc())
        .limit(limit)
    )
    with _Session() as session:
        return list(session.scalars(statement))


def save_evaluation(listing_id: int, *, score: int, analysis: dict[str, Any], model: str) -> None:
    _update_listing(
        listing_id,
        score=score,
        analysis=analysis,
        ai_model=model,
        evaluated_at=datetime.now(),
        ai_error=None,
    )


def save_evaluation_error(listing_id: int, error: str) -> None:
    _update_listing(listing_id, ai_error=error, ai_attempts=Listing.ai_attempts + 1)


def reset_evaluations() -> int:
    """Queue every non-rejected listing for re-evaluation (e.g. after the criteria changed)."""
    statement = (
        update(Listing)
        .where(Listing.rejected.is_(False))
        .values(evaluated_at=None, ai_attempts=0, ai_error=None)
    )
    with _Session.begin() as session:
        return cast(CursorResult[Any], session.execute(statement)).rowcount


def set_flag(listing_id: int, flag: str, value: bool) -> None:
    if flag not in FLAGS:
        raise ValueError(f"Nieznana flaga: {flag}")
    _update_listing(listing_id, **{flag: value})


def query_listings(
    *,
    sort: str = "score",
    min_score: int | None = None,
    only_favorites: bool = False,
    rejected: bool | None = False,
    include_duplicates: bool = True,
) -> list[Listing]:
    """rejected: False – hide rejected, True – only rejected, None – all.

    include_duplicates=False returns only the primary listing of every duplicate group.
    """
    orders: dict[str, ColumnElement[Any]] = {
        "score": nulls_last(Listing.score.desc()),
        "price": nulls_last(Listing.price.asc()),
        "price_per_m2": nulls_last(Listing.price_per_m2.asc()),
        "newest": func.coalesce(Listing.created_at, Listing.first_seen_at).desc(),
    }
    statement = select(Listing).order_by(orders[sort], Listing.first_seen_at.desc())
    if rejected is not None:
        statement = statement.where(Listing.rejected.is_(rejected))
    if only_favorites:
        statement = statement.where(Listing.favorite.is_(True))
    if min_score:
        statement = statement.where(Listing.score >= min_score)
    if not include_duplicates:
        statement = statement.where(Listing.duplicate_of.is_(None))
    with _Session() as session:
        return list(session.scalars(statement))


def _update_listing(listing_id: int, **values: Any) -> None:
    with _Session.begin() as session:
        session.execute(update(Listing).where(Listing.id == listing_id).values(**values))


# --- Duplicates and notifications ---------------------------------------------------


def save_duplicate_groups(primary_of: dict[int, int]) -> int:
    """Set duplicate_of from {member_id: primary_id}; listings missing from the map become standalone."""
    changed = 0
    with _Session.begin() as session:
        for listing_id, current in session.execute(select(Listing.id, Listing.duplicate_of)).all():
            desired = primary_of.get(listing_id)
            if current != desired:
                session.execute(update(Listing).where(Listing.id == listing_id).values(duplicate_of=desired))
                changed += 1
    return changed


def duplicate_members() -> dict[int, list[Listing]]:
    """{primary_id: [other listings of the same flat]}."""
    groups: dict[int, list[Listing]] = defaultdict(list)
    statement = select(Listing).where(Listing.duplicate_of.is_not(None)).order_by(nulls_last(Listing.price.asc()))
    with _Session() as session:
        for listing in session.scalars(statement):
            assert listing.duplicate_of is not None
            groups[listing.duplicate_of].append(listing)
    return dict(groups)


def mark_notified(listing_id: int) -> None:
    _update_listing(listing_id, notified_at=datetime.now())


def group_already_notified(listing_id: int) -> bool:
    """Whether this listing – or another listing of the same flat – was already announced."""
    with _Session() as session:
        primary = session.scalar(select(Listing.duplicate_of).where(Listing.id == listing_id)) or listing_id
        statement = select(func.count()).where(
            or_(Listing.id == primary, Listing.duplicate_of == primary),
            Listing.notified_at.is_not(None),
        )
        return bool(session.scalar(statement))


# --- Token usage ------------------------------------------------------------------


def record_token_usage(model: str, tokens: int, listing_id: int | None = None) -> None:
    with _Session.begin() as session:
        session.add(TokenUsage(model=model, tokens=tokens, listing_id=listing_id))


def tokens_used_since(since: datetime) -> int:
    with _Session() as session:
        total = func.coalesce(func.sum(TokenUsage.tokens), 0)
        return session.scalar(select(total).where(TokenUsage.used_at >= since)) or 0


def tokens_used_today() -> int:
    return tokens_used_since(datetime.now().replace(hour=0, minute=0, second=0, microsecond=0))


# --- Scan history -----------------------------------------------------------------


def start_scan() -> int:
    with _Session.begin() as session:
        run = ScanRun()
        session.add(run)
        session.flush()
        return run.id


def finish_scan(
    scan_id: int, *, found: int, new: int, evaluated: int, error: str | None, price_drops: int = 0
) -> None:
    statement = (
        update(ScanRun)
        .where(ScanRun.id == scan_id)
        .values(
            finished_at=datetime.now(),
            found=found,
            new=new,
            evaluated=evaluated,
            error=error,
            price_drops=price_drops,
        )
    )
    with _Session.begin() as session:
        session.execute(statement)


def last_finished_scan() -> ScanRun | None:
    statement = (
        select(ScanRun)
        .where(ScanRun.finished_at.is_not(None))
        .order_by(ScanRun.finished_at.desc())
        .limit(1)
    )
    with _Session() as session:
        return session.scalars(statement).first()


def close_interrupted_scans() -> None:
    """Close scans left unfinished by an app restart."""
    statement = (
        update(ScanRun)
        .where(ScanRun.finished_at.is_(None))
        .values(finished_at=datetime.now(), error="Skanowanie przerwane (restart aplikacji)")
    )
    with _Session.begin() as session:
        session.execute(statement)
