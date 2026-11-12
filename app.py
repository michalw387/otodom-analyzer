"""Streamlit UI: best listings, settings and the archive of rejected listings."""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any

import pandas as pd
import streamlit as st

import config
import database
import scheduler
from ai.evaluator import cli_status
from database import Listing
from formatting import decimal, pln, plural, token_usage
from notifications import webhook
from scraper.otodom import BASE_URL, LOCATION_PATTERN, MARKETS, MAX_ROOMS, RADIUS_OPTIONS_KM
from services import export, geo

config.configure_logging()

st.set_page_config(page_title="Otodom Analyzer", page_icon="🏠", layout="wide")

database.init_db()
scheduler.ensure_started()

PAGE_SIZE = 20
EXPORT_SELECTION = "export_selection"
_MD_SPECIAL = re.compile(r"([\\`*_{}\[\]()#+\-.!|$~<>])")

# White text on these backgrounds keeps sufficient contrast in both light and dark themes.
st.markdown(
    """
    <style>
    .score-badge {text-align: center; padding: .5rem .25rem; border-radius: .75rem; color: #fff;
                  font-weight: 700; font-size: 1.6rem; line-height: 1.2;}
    .score-badge small {display: block; font-size: .75rem; font-weight: 500;}
    .score-high {background: #2e7d32;}
    .score-mid {background: #a85d00;}
    .score-low {background: #c62828;}
    .score-none {background: #546e7a; font-size: 1rem;}
    </style>
    """,
    unsafe_allow_html=True,
)


# --- Formatting ---------------------------------------------------------------


def md(text: str) -> str:
    """Escape third-party text (listing data, AI output) before passing it to st.markdown."""
    return _MD_SPECIAL.sub(r"\\\1", text)


def when(moment: datetime | None) -> str | None:
    if moment is None:
        return None
    return f"{moment:%H:%M}" if moment.date() == datetime.now().date() else f"{moment:%d.%m.%Y %H:%M}"


def interval_label(minutes: int) -> str:
    return f"{minutes} min" if minutes < 60 else f"{minutes // 60} godz."


def score_badge(score: int | None) -> str:
    if score is None:
        return '<div class="score-badge score-none">brak oceny<small>czeka na AI</small></div>'
    level = "high" if score >= 75 else "mid" if score >= 50 else "low"
    return f'<div class="score-badge score-{level}">{score}/100<small>ocena AI</small></div>'


def option_index(options: list[Any], value: Any) -> int:
    return options.index(value) if value in options else 0


def as_int(value: Any) -> int | None:
    return int(value) if value not in (None, "") else None


# --- Status bar -----------------------------------------------------------------


@st.fragment(run_every="15s")
def status_bar() -> None:
    running = scheduler.is_scan_running()
    last = database.last_finished_scan()
    info_col, button_col = st.columns([4, 1], vertical_alignment="center")
    with info_col:
        if running:
            st.info("🔄 Trwa skanowanie Otodom i analiza ofert…")
        elif last:
            text = (
                f"Ostatnie skanowanie: **{when(last.finished_at)}** | Znalazłem {last.new} "
                f"{plural(last.new, 'nową ofertę', 'nowe oferty', 'nowych ofert')}"
            )
            if last.evaluated:
                text += f", oceniono {last.evaluated}"
            if last.price_drops:
                text += f", 📉 obniżki cen: {last.price_drops}"
            next_run = scheduler.next_scan_time()
            if next_run:
                text += f" | Następne: {next_run:%H:%M}"
            if last.error:
                st.warning(f"{text}  \n⚠️ {md(last.error)}")
            else:
                st.success(text)
        else:
            st.info("Jeszcze nie skanowano. Ustaw filtry w zakładce „Ustawienia i Kryteria” i uruchom skanowanie.")
        st.caption(token_usage_text(database.get_settings()))
    with button_col:
        if st.button("🔍 Skanuj i przeanalizuj teraz", type="primary", disabled=running, width="stretch"):
            scheduler.trigger_scan_now()
            st.rerun(scope="fragment")

    # Once a newer scan has finished, rerun the whole app so the listings refresh.
    last_id = last.id if last else None
    if st.session_state.setdefault("seen_scan_id", last_id) != last_id:
        st.session_state.seen_scan_id = last_id
        st.rerun()


def token_usage_text(settings: dict[str, Any]) -> str:
    return token_usage(database.tokens_used_today(), int(settings.get("daily_token_limit") or 0))


# --- Export selection ---------------------------------------------------------------


def export_selection() -> set[int]:
    selection: set[int] = st.session_state.setdefault(EXPORT_SELECTION, set())
    return selection


def toggle_export(listing_id: int) -> None:
    export_selection().symmetric_difference_update({listing_id})


def clear_export_selection() -> None:
    export_selection().clear()
    for key in [k for k in st.session_state if str(k).startswith("best-export-")]:
        del st.session_state[key]


def export_controls(visible: list[Listing], groups: dict[int, list[Listing]], target: dict[str, Any]) -> None:
    selection = export_selection()
    chosen = database.listings_by_ids(selection) if selection else visible
    label = "📥 Eksportuj wybrane do Excela" if selection else "📥 Eksportuj widoczne do Excela"
    rows = export.listing_rows(chosen, duplicates=groups, target=target)
    download_col, clear_col, _ = st.columns([2, 1, 2])
    download_col.download_button(
        f"{label} ({len(rows)})",
        data=export.to_xlsx(rows) if rows else b"",
        file_name=f"oferty-otodom-{date.today():%Y-%m-%d}.xlsx",
        mime=export.XLSX_MIME,
        disabled=not rows,
        width="stretch",
    )
    if selection:
        clear_col.button("Wyczyść zaznaczenie", on_click=clear_export_selection, width="stretch")


# --- Listing cards ----------------------------------------------------------------


def listing_list(
    listings: list[Listing],
    key: str,
    empty_message: str,
    *,
    groups: dict[int, list[Listing]] | None = None,
    target: dict[str, Any] | None = None,
) -> None:
    if not listings:
        st.info(empty_message)
        return
    pages = -(-len(listings) // PAGE_SIZE)
    st.caption(f"{len(listings)} {plural(len(listings), 'oferta', 'oferty', 'ofert')}")
    # The key includes the page count so the widget resets when filters shrink the list.
    page = st.session_state.get(f"{key}-page-{pages}", 1)
    for listing in listings[(page - 1) * PAGE_SIZE : page * PAGE_SIZE]:
        listing_card(listing, key, others=(groups or {}).get(listing.id, []), target=target)
    if pages > 1:
        st.number_input(f"Strona (z {pages})", min_value=1, max_value=pages, key=f"{key}-page-{pages}")


def listing_card(listing: Listing, key: str, *, others: list[Listing], target: dict[str, Any] | None) -> None:
    with st.container(border=True):
        image_col, info_col, score_col = st.columns([1.3, 4, 1.1])
        with image_col:
            if listing.image_url:
                st.image(listing.image_url, width="stretch")
        with info_col:
            st.markdown(f"#### [{md(listing.title)}]({listing.url})")
            facts = [
                f"**{pln(listing.price)}**" if listing.price else "**cena do negocjacji**",
                f"{pln(listing.price_per_m2)}/m²" if listing.price_per_m2 else None,
                f"{decimal(listing.area)} m²" if listing.area else None,
                f"{listing.rooms} pok." if listing.rooms else None,
                floor_text(listing),
            ]
            st.markdown(" · ".join(f for f in facts if f))
            if listing.location:
                st.caption(f"📍 {md(listing.location)}")
            trip = geo.commute(listing, target)
            if trip:
                st.caption(f"🧭 {md(trip.short())} (szacunek)")
            badges = [
                ":orange-badge[⭐ Ulubione]" if listing.favorite else "",
                ":blue-badge[📞 Skontaktowano]" if listing.contacted else "",
                ":gray-badge[🚫 Odrzucone]" if listing.rejected else "",
                price_change_badge(listing),
                f":violet-badge[🔁 {len(others) + 1} ogłoszenia tego mieszkania]" if others else "",
            ]
            if any(badges):
                st.markdown(" ".join(b for b in badges if b))
        with score_col:
            st.markdown(score_badge(listing.score), unsafe_allow_html=True)
        if others:
            duplicates_list(others)
        analysis_details(listing)
        action_buttons(listing, key)


def price_change_badge(listing: Listing) -> str:
    if not listing.previous_price or not listing.price or listing.previous_price == listing.price:
        return ""
    if listing.price < listing.previous_price:
        return f":green-badge[📉 Obniżka z {pln(listing.previous_price)}]"
    return f":orange-badge[📈 Podwyżka z {pln(listing.previous_price)}]"


def duplicates_list(others: list[Listing]) -> None:
    lines = []
    for other in others:
        details = [pln(other.price), f"ocena {other.score}/100" if other.score is not None else None]
        if other.rejected:
            details.append("odrzucone")
        lines.append(f"- [{md(other.title)}]({other.url}) – {' · '.join(d for d in details if d)}")
    with st.expander(f"🔁 To samo mieszkanie w innych ogłoszeniach ({len(others)})"):
        st.markdown("\n".join(lines))


def floor_text(listing: Listing) -> str | None:
    if not listing.floor:
        return None
    floor = f"{listing.floor}/{listing.building_floors}" if listing.building_floors else listing.floor
    return f"piętro {floor}"


def analysis_details(listing: Listing) -> None:
    with st.expander("Szczegóły analizy AI"):
        analysis = listing.analysis
        if analysis:
            st.markdown(f"**Podsumowanie:** {md(analysis['summary'])}")
            pros_col, cons_col = st.columns(2)
            with pros_col:
                st.markdown("**✅ Zalety**")
                st.markdown("\n".join(f"- {md(p)}" for p in analysis["pros"]) or "—")
            with cons_col:
                st.markdown("**⚠️ Wady i ryzyka**")
                st.markdown("\n".join(f"- {md(c)}" for c in analysis["cons"]) or "—")
            consistency = analysis.get("consistency") or {}
            issues = consistency.get("issues") or []
            if consistency.get("is_consistent", True) and not issues:
                st.caption("✔️ Opis zgodny z filtrami i parametrami ogłoszenia")
            else:
                st.warning("**Niespójności:** " + "; ".join(md(i) for i in issues))
            st.caption(f"Model: {listing.ai_model} · oceniono: {when(listing.evaluated_at)}")
        elif listing.ai_error and listing.ai_attempts >= config.MAX_AI_ATTEMPTS:
            st.error(f"Ocena AI nie powiodła się: {md(listing.ai_error)}")
        elif listing.ai_error:
            st.warning(f"Ponowię ocenę przy następnym skanowaniu. Ostatni błąd: {md(listing.ai_error)}")
        else:
            st.info("Oferta czeka na ocenę AI (przy najbliższym skanowaniu).")

        details = [
            ("Czynsz", pln(listing.rent)),
            ("Rok budowy", listing.build_year),
            ("Ogrzewanie", listing.heating),
            ("Rynek", listing.market),
            ("Dodano na Otodom", when(listing.created_at)),
            ("Znaleziono", when(listing.first_seen_at)),
            ("Zmiana ceny", when(listing.price_changed_at)),
        ]
        st.caption(" · ".join(f"{label}: {value}" for label, value in details if value))


def action_buttons(listing: Listing, key: str) -> None:
    fav_col, reject_col, contact_col, export_col = st.columns([1, 1, 1, 1.5], vertical_alignment="center")
    fav_col.button(
        "💔 Usuń z ulubionych" if listing.favorite else "⭐ Do ulubionych",
        key=f"{key}-fav-{listing.id}",
        on_click=database.set_flag,
        args=(listing.id, "favorite", not listing.favorite),
        width="stretch",
    )
    reject_col.button(
        "↩️ Przywróć" if listing.rejected else "🚫 Odrzuć",
        key=f"{key}-reject-{listing.id}",
        on_click=database.set_flag,
        args=(listing.id, "rejected", not listing.rejected),
        width="stretch",
    )
    contact_col.button(
        "↩️ Cofnij: skontaktowano" if listing.contacted else "📞 Oznacz: skontaktowano",
        key=f"{key}-contact-{listing.id}",
        on_click=database.set_flag,
        args=(listing.id, "contacted", not listing.contacted),
        width="stretch",
    )
    if key == "best":
        export_col.checkbox(
            "Zaznacz do eksportu",
            value=listing.id in export_selection(),
            key=f"best-export-{listing.id}",
            on_change=toggle_export,
            args=(listing.id,),
        )


# --- Tabs -------------------------------------------------------------------------


def best_listings_tab() -> None:
    settings = database.get_settings()
    sort_col, score_col, fav_col, rejected_col = st.columns([2, 2, 1, 1], vertical_alignment="bottom")
    sort = sort_col.selectbox(
        "Sortuj według", list(database.SORT_OPTIONS), format_func=lambda key: database.SORT_OPTIONS[key]
    )
    min_score = score_col.slider("Pokazuj oferty z oceną co najmniej", 0, 100, 0, step=5)
    only_favorites = fav_col.toggle("Tylko ulubione")
    hide_rejected = rejected_col.toggle("Ukryj odrzucone", value=True)
    listings = database.query_listings(
        sort=sort or "score",
        min_score=min_score,
        only_favorites=only_favorites,
        rejected=False if hide_rejected else None,
        include_duplicates=False,
    )
    groups = database.duplicate_members()
    export_controls(listings, groups, settings["target"])
    listing_list(
        listings,
        "best",
        "Brak ofert spełniających warunki. Uruchom skanowanie lub poluzuj filtry.",
        groups=groups,
        target=settings["target"],
    )


def archive_tab() -> None:
    st.caption("Odrzucone oferty nie są ponownie oceniane przez AI. Możesz je w każdej chwili przywrócić.")
    listing_list(
        database.query_listings(sort="newest", rejected=True),
        "archive",
        "Archiwum jest puste.",
        target=database.get_settings()["target"],
    )


def settings_tab() -> None:
    settings = database.get_settings()
    filters = settings["filters"]
    notifications = settings["notifications"]
    target = settings["target"]

    with st.form("settings"):
        st.subheader("🔎 Filtry wyszukiwania Otodom")
        location_col, radius_col = st.columns([3, 1])
        location = location_col.text_input(
            "Lokalizacja (ścieżka z adresu Otodom)",
            filters["location"],
            help="Fragment adresu wyników po „/mieszkanie/”, np. "
            "`mazowieckie/warszawa/warszawa/warszawa/mokotow` lub `malopolskie/krakow/krakow/krakow`.",
        )
        radius = radius_col.selectbox(
            "Promień",
            RADIUS_OPTIONS_KM,
            index=option_index(RADIUS_OPTIONS_KM, filters["radius_km"]),
            format_func=lambda km: f"+{km} km" if km else "bez promienia",
        )
        price_col, area_col, rooms_col = st.columns(3)
        price_min = price_col.number_input(
            "Cena min (zł)", min_value=0, value=as_int(filters["price_min"]), step=10_000, placeholder="dowolna"
        )
        price_max = price_col.number_input(
            "Cena max (zł)", min_value=0, value=as_int(filters["price_max"]), step=10_000, placeholder="dowolna"
        )
        area_min = area_col.number_input(
            "Powierzchnia min (m²)", min_value=0, value=as_int(filters["area_min"]), placeholder="dowolna"
        )
        area_max = area_col.number_input(
            "Powierzchnia max (m²)", min_value=0, value=as_int(filters["area_max"]), placeholder="dowolna"
        )
        rooms_min, rooms_max = rooms_col.slider(
            "Liczba pokoi",
            1,
            MAX_ROOMS,
            (filters["rooms_min"], filters["rooms_max"]),
            help=f"{MAX_ROOMS} oznacza {MAX_ROOMS} i więcej.",
        )
        price_per_m2_max = rooms_col.number_input(
            "Maks. cena za m² (zł)",
            min_value=0,
            value=as_int(filters["price_per_m2_max"]),
            step=500,
            placeholder="dowolna",
        )
        market = st.radio(
            "Rynek",
            list(MARKETS),
            index=option_index(list(MARKETS), filters["market"]),
            format_func=lambda m: MARKETS[m].capitalize(),
            horizontal=True,
        )
        custom_url = st.text_input(
            "Własny URL wyszukiwania (opcjonalnie)",
            filters["custom_url"],
            help="Wklej adres wyników z Otodom, aby użyć dowolnych filtrów ze strony. "
            "Gdy jest ustawiony, powyższe filtry są pomijane.",
        )

        st.subheader("🎯 Kryteria oceny AI")
        st.caption(
            "Kryterium o wadze „Krytyczna” to warunek konieczny – oferta, która go nie spełnia, dostaje maks. 30/100."
        )
        criteria_frame = st.data_editor(
            pd.DataFrame(settings["criteria"], columns=["text", "weight"]),
            num_rows="dynamic",
            hide_index=True,
            width="stretch",
            column_config={
                "text": st.column_config.TextColumn("Kryterium", required=True, width="large"),
                "weight": st.column_config.SelectboxColumn(
                    "Waga", options=config.CRITERION_WEIGHTS, required=True, default="Średnia"
                ),
            },
        )

        st.subheader("📍 Adres docelowy (dojazd)")
        label_col, address_col, mode_col = st.columns([1, 3, 1])
        target_label = label_col.text_input("Nazwa celu", target["label"], placeholder="np. Praca")
        target_address = address_col.text_input(
            "Adres",
            target["address"],
            placeholder="np. Marszałkowska 1, Warszawa",
            help="Adres jest geokodowany przez OpenStreetMap (Nominatim) przy zapisie. Odległość liczona jest "
            "w linii prostej od współrzędnych ogłoszenia, a czas dojazdu to szacunek.",
        )
        modes = list(geo.TRAVEL_MODES)
        travel_mode = mode_col.selectbox(
            "Dojazd",
            modes,
            index=option_index(modes, target["mode"]),
            format_func=lambda m: geo.TRAVEL_MODES[m].label.capitalize(),
        )
        if target["latitude"] is not None:
            st.caption(f"📌 Rozpoznany adres: {md(target['resolved_address'])}")

        st.subheader("🔔 Powiadomienia")
        notify_col, channel_col, threshold_col = st.columns([1, 1, 2], vertical_alignment="bottom")
        notify_enabled = notify_col.toggle("Włącz powiadomienia", notifications["enabled"])
        channels = list(webhook.CHANNELS)
        channel = channel_col.selectbox(
            "Kanał",
            channels,
            index=option_index(channels, notifications["channel"]),
            format_func=lambda key: webhook.CHANNELS[key],
        ) or channels[0]
        min_notify_score = threshold_col.slider(
            "Powiadamiaj o nowych ofertach z oceną AI od", 50, 100, int(notifications["min_score"])
        )
        webhook_url = st.text_input(
            "URL webhooka (ntfy.sh lub Discord)",
            notifications["webhook_url"],
            type="password",
            placeholder="https://ntfy.sh/moj-tajny-temat lub https://discord.com/api/webhooks/…",
            help="Puste pole = wartość NOTIFY_WEBHOOK_URL z .env. Nazwa tematu ntfy działa jak hasło – wybierz trudną.",
        )
        token_col, chat_col = st.columns(2)
        telegram_token = token_col.text_input(
            "Telegram: token bota",
            notifications["telegram_bot_token"],
            type="password",
            help="Od @BotFather. Puste pole = TELEGRAM_BOT_TOKEN z .env.",
        )
        telegram_chat = chat_col.text_input(
            "Telegram: chat ID",
            notifications["telegram_chat_id"],
            help="Puste pole = TELEGRAM_CHAT_ID z .env.",
        )
        price_drops = st.checkbox("Powiadamiaj o obniżkach cen zapisanych ofert", notifications["price_drops"])

        st.subheader("🤖 Silnik AI (Claude Code) i harmonogram")
        st.caption(
            "Oferty ocenia zainstalowany i zalogowany Claude Code w ramach Twojej subskrypcji – bez klucza API. "
            "Sprawdzisz go w sekcji Diagnostyka poniżej."
        )
        model_col, interval_col, tokens_col = st.columns(3)
        models = list(config.AI_MODELS)
        model = model_col.selectbox(
            "Model AI",
            models,
            index=option_index(models, settings["ai_model"]),
            format_func=lambda key: config.AI_MODELS[key],
        ) or models[0]
        interval = interval_col.selectbox(
            "Automatyczne skanowanie co",
            config.SCAN_INTERVALS_MINUTES,
            index=option_index(config.SCAN_INTERVALS_MINUTES, settings["scan_interval_minutes"]),
            format_func=interval_label,
        )
        token_limit = tokens_col.number_input(
            "Dzienny limit tokenów AI",
            min_value=0,
            value=int(settings["daily_token_limit"] or 0),
            step=10_000,
            help="0 = bez limitu. Po przekroczeniu oceny są wstrzymywane do północy – chroni limit "
            "subskrypcji Claude przed zużyciem przez skanowanie. Jedna ocena przez Claude Code to zwykle "
            "10–15 tys. tokenów.",
        )

        save_col, test_col, _ = st.columns([1, 1, 2])
        submitted = save_col.form_submit_button("💾 Zapisz ustawienia", type="primary", width="stretch")
        test_clicked = test_col.form_submit_button("🔔 Przetestuj powiadomienie", width="stretch")

    usage_caption = st.empty()  # filled after saving, so it shows the limit just saved

    new_notifications = {
        "enabled": notify_enabled,
        "channel": channel,
        "webhook_url": webhook_url.strip(),
        "telegram_bot_token": telegram_token.strip(),
        "telegram_chat_id": telegram_chat.strip(),
        "min_score": min_notify_score,
        "price_drops": price_drops,
    }
    if test_clicked:
        send_test_notification(new_notifications)

    if submitted:
        new_filters = {
            "location": location.strip().strip("/").lower(),
            "radius_km": radius,
            "price_min": price_min,
            "price_max": price_max,
            "area_min": area_min,
            "area_max": area_max,
            "rooms_min": rooms_min,
            "rooms_max": rooms_max,
            "price_per_m2_max": price_per_m2_max,
            "market": market,
            "custom_url": custom_url.strip(),
        }
        errors = validate_filters(new_filters)
        if notify_enabled:
            errors += webhook.config_errors(new_notifications)
        for error in errors:
            st.error(error)
        if not errors:
            database.save_settings(
                {
                    "filters": new_filters,
                    "criteria": criteria_from_frame(criteria_frame),
                    "ai_model": model,
                    "scan_interval_minutes": interval,
                    "daily_token_limit": token_limit,
                    "notifications": new_notifications,
                    "target": resolve_target(target, target_label, target_address, travel_mode),
                }
            )
            if interval != settings["scan_interval_minutes"]:
                scheduler.reschedule(interval)
            st.success("Zapisano. Nowe ustawienia zostaną użyte przy najbliższym skanowaniu.")
    usage_caption.caption(token_usage_text(database.get_settings()))

    st.divider()
    st.subheader("🔁 Ponowna ocena ofert")
    st.caption(
        "Po zmianie kryteriów lub adresu docelowego możesz ocenić ponownie wszystkie nieodrzucone oferty "
        f"(maks. {config.MAX_EVALUATIONS_PER_SCAN} na jedno skanowanie – zużywa limit subskrypcji Claude). "
        "Większe partie: `python scripts/reevaluate.py`."
    )
    if st.button("Oceń ponownie wszystkie oferty"):
        count = database.reset_evaluations()
        scheduler.trigger_scan_now()
        st.success(f"Zakolejkowano {count} {plural(count, 'ofertę', 'oferty', 'ofert')} – skanowanie uruchomione.")

    st.divider()
    diagnostics_section()


def diagnostics_section() -> None:
    st.subheader("🩺 Diagnostyka")
    st.caption(
        f"Sprawdza, czy komenda `{config.CLAUDE_CLI}` działa (`claude --version`) i czy Claude Code jest zalogowany "
        "(`claude auth status`). Test nie zużywa limitu subskrypcji."
    )
    if st.button("Sprawdź Claude Code"):
        with st.spinner("Sprawdzam Claude Code…"):
            status = cli_status()
        if status.error:
            st.error(f"❌ {md(status.error)}")
        elif not status.logged_in:
            st.warning(f"⚠️ Claude Code {md(status.version or '')} działa, ale nie jest zalogowany – "
                       "uruchom w terminalu `claude auth login`.")
        else:
            account = ", ".join(part for part in (status.auth_method, status.subscription) if part)
            st.success(f"✅ Claude Code {md(status.version or '')} działa i jest zalogowany ({md(account)}).")


def send_test_notification(notification_settings: dict[str, Any]) -> None:
    try:
        webhook.Notifier.from_settings(notification_settings).send(webhook.test_message())
    except webhook.NotificationError as exc:
        st.error(f"Nie udało się wysłać powiadomienia: {exc}")
    else:
        st.success(f"Wysłano powiadomienie testowe przez {webhook.CHANNELS[notification_settings['channel']]}.")


def resolve_target(current: dict[str, Any], label: str, address: str, mode: str) -> dict[str, Any]:
    """Geocode the target address only when it changed (Nominatim allows ~1 request per second)."""
    address = address.strip()
    target = {**current, "label": label.strip() or "Cel", "address": address, "mode": mode}
    if address == current["address"] and (current["latitude"] is not None or not address):
        return target
    target.update(latitude=None, longitude=None, resolved_address="")
    if address:
        try:
            found = geo.geocode(address)
        except geo.GeocodingError as exc:
            st.warning(f"{exc}. Odległości nie będą liczone, dopóki adres nie zostanie rozpoznany.")
        else:
            target.update(latitude=found.latitude, longitude=found.longitude, resolved_address=found.display_name)
            st.info(f"📌 Rozpoznano adres: {md(found.display_name)}")
    return target


def validate_filters(filters: dict[str, Any]) -> list[str]:
    errors = []
    if filters["custom_url"]:
        if not filters["custom_url"].startswith(f"{BASE_URL}/"):
            errors.append(f"Własny URL musi zaczynać się od {BASE_URL}/")
    elif not LOCATION_PATTERN.match(filters["location"]):
        errors.append("Lokalizacja: podaj ścieżkę z adresu Otodom, np. mazowieckie/warszawa/warszawa/warszawa")
    for label, low, high in (
        ("Cena", filters["price_min"], filters["price_max"]),
        ("Powierzchnia", filters["area_min"], filters["area_max"]),
    ):
        if low and high and low > high:
            errors.append(f"{label}: wartość minimalna jest większa niż maksymalna")
    return errors


def criteria_from_frame(frame: pd.DataFrame) -> list[dict[str, str]]:
    criteria = []
    for row in frame.to_dict("records"):
        text = row.get("text")
        if isinstance(text, str) and text.strip():
            weight = row.get("weight") if row.get("weight") in config.CRITERION_WEIGHTS else "Średnia"
            criteria.append({"text": text.strip(), "weight": str(weight)})
    return criteria


# --- Layout -----------------------------------------------------------------------

st.title("🏠 Otodom Analyzer")
st.caption("Osobisty asystent wyszukiwania mieszkań – monitoruje Otodom.pl i ocenia oferty z pomocą AI.")
status_bar()

best_tab, settings_tab_, archive_tab_ = st.tabs(
    ["🏆 Najlepsze Oferty", "⚙️ Ustawienia i Kryteria", "🗄️ Archiwum / Odrzucone"]
)
with best_tab:
    best_listings_tab()
with settings_tab_:
    settings_tab()
with archive_tab_:
    archive_tab()
