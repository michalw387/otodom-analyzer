"""Instant notifications via ntfy.sh, Discord webhooks or a Telegram bot.

All messages are sent as plain text, so listing titles (third-party content) cannot inject
formatting, @everyone mentions or HTML.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit, urlunsplit

import httpx2 as httpx

import config
from formatting import decimal, pln

if TYPE_CHECKING:
    from database import Listing

log = logging.getLogger(__name__)

CHANNELS = {"ntfy": "ntfy.sh", "discord": "Discord", "telegram": "Telegram"}
TELEGRAM_API = "https://api.telegram.org"
DISCORD_MAX_LENGTH = 2000
TELEGRAM_MAX_LENGTH = 4096
_TELEGRAM_TOKEN = re.compile(r"^\d+:[\w-]+$")


class NotificationError(Exception):
    """A notification could not be configured or delivered."""


@dataclass(frozen=True)
class Notification:
    title: str
    body: str
    url: str | None = None

    def as_text(self) -> str:
        return "\n".join(part for part in (self.title, self.body, self.url) if part)


# --- Message templates ------------------------------------------------------------


def new_listing_message(listing: Listing, analysis: dict[str, Any], score: int) -> Notification:
    lines = [listing.title, _price_line(listing)]
    if listing.location:
        lines.append(f"📍 {listing.location}")
    lines += [f"✅ {pro}" for pro in analysis.get("pros", [])[:3]]
    lines += [f"⚠️ {con}" for con in analysis.get("cons", [])[:3]]
    return Notification(title=f"🏠 Nowa oferta {score}/100", body="\n".join(lines), url=listing.url)


def price_drop_message(listing: Listing, old_price: float, new_price: float) -> Notification:
    percent = (old_price - new_price) / old_price * 100
    body = "\n".join(
        [
            f"Z {pln(old_price)} na {pln(new_price)} (−{decimal(round(percent, 1))}%)",
            listing.title,
            _price_line(listing, price=new_price),
        ]
    )
    return Notification(title="📉 Obniżka ceny!", body=body, url=listing.url)


def test_message() -> Notification:
    return Notification(
        title="🔔 Test powiadomień Otodom Analyzer",
        body="Jeśli to widzisz, powiadomienia działają.",
        url="https://www.otodom.pl/",
    )


def _price_line(listing: Listing, price: float | None = None) -> str:
    price = price if price is not None else listing.price
    parts = [
        pln(price) or "cena do negocjacji",
        f"{decimal(listing.area)} m²" if listing.area else None,
        f"{pln(price / listing.area)}/m²" if price and listing.area else None,
        f"ocena AI {listing.score}/100" if listing.score is not None else None,
    ]
    return "💰 " + " · ".join(p for p in parts if p)


# --- Delivery ---------------------------------------------------------------------


def config_errors(settings: dict[str, Any]) -> list[str]:
    """Problems that prevent sending with these notification settings (after .env fallbacks)."""
    channel = settings.get("channel")
    if channel not in CHANNELS:
        return [f"Nieznany kanał powiadomień: {channel}"]
    if channel == "telegram":
        errors = []
        token = config.setting_or_env(settings.get("telegram_bot_token"), "TELEGRAM_BOT_TOKEN")
        if not token or not _TELEGRAM_TOKEN.match(token):
            errors.append("Telegram: podaj token bota w formacie 123456:ABC… (w UI lub TELEGRAM_BOT_TOKEN w .env)")
        if not config.setting_or_env(settings.get("telegram_chat_id"), "TELEGRAM_CHAT_ID"):
            errors.append("Telegram: podaj chat ID (w UI lub TELEGRAM_CHAT_ID w .env)")
        return errors
    url = config.setting_or_env(settings.get("webhook_url"), "NOTIFY_WEBHOOK_URL")
    parts = urlsplit(url or "")
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return [f"{CHANNELS[channel]}: podaj poprawny URL webhooka (w UI lub NOTIFY_WEBHOOK_URL w .env)"]
    if channel == "ntfy" and not parts.path.strip("/"):
        return ["ntfy: URL musi zawierać nazwę tematu, np. https://ntfy.sh/moj-tajny-temat"]
    return []


class Notifier:
    def __init__(
        self,
        channel: str,
        *,
        webhook_url: str = "",
        telegram_bot_token: str = "",
        telegram_chat_id: str = "",
        client: httpx.Client | None = None,
    ) -> None:
        self.channel = channel
        self._webhook_url = webhook_url
        self._telegram_bot_token = telegram_bot_token
        self._telegram_chat_id = telegram_chat_id
        self._client = client

    @classmethod
    def from_settings(cls, settings: dict[str, Any], *, client: httpx.Client | None = None) -> Notifier:
        """Build a notifier from notification settings; empty fields fall back to .env values."""
        errors = config_errors(settings)
        if errors:
            raise NotificationError("; ".join(errors))
        return cls(
            settings["channel"],
            webhook_url=config.setting_or_env(settings.get("webhook_url"), "NOTIFY_WEBHOOK_URL") or "",
            telegram_bot_token=config.setting_or_env(settings.get("telegram_bot_token"), "TELEGRAM_BOT_TOKEN") or "",
            telegram_chat_id=config.setting_or_env(settings.get("telegram_chat_id"), "TELEGRAM_CHAT_ID") or "",
            client=client,
        )

    def send(self, notification: Notification) -> None:
        url, payload = self._request(notification)
        client = self._client or httpx.Client(timeout=10.0)
        try:
            response = client.post(url, json=payload)
        except httpx.HTTPError as exc:
            # Never include the URL – for Telegram it contains the bot token.
            raise NotificationError(f"{CHANNELS[self.channel]}: błąd połączenia ({type(exc).__name__})") from exc
        finally:
            if self._client is None:
                client.close()
        if response.status_code >= 400:
            raise NotificationError(
                f"{CHANNELS[self.channel]}: HTTP {response.status_code} {response.text[:200]}".strip()
            )
        log.info("Notification sent via %s: %s", self.channel, notification.title)

    def _request(self, notification: Notification) -> tuple[str, dict[str, Any]]:
        if self.channel == "ntfy":
            # JSON publishing (POST to the server root) keeps UTF-8 titles intact – HTTP headers can't.
            parts = urlsplit(self._webhook_url)
            base_path, _, topic = parts.path.rstrip("/").rpartition("/")
            server = urlunsplit((parts.scheme, parts.netloc, base_path or "/", "", ""))
            payload: dict[str, Any] = {
                "topic": topic,
                "title": notification.title,
                "message": notification.body,
                "tags": ["house"],
            }
            if notification.url:
                payload["click"] = notification.url
            return server, payload
        if self.channel == "discord":
            return self._webhook_url, {
                "content": notification.as_text()[:DISCORD_MAX_LENGTH],
                "allowed_mentions": {"parse": []},
            }
        return f"{TELEGRAM_API}/bot{self._telegram_bot_token}/sendMessage", {
            "chat_id": self._telegram_chat_id,
            "text": notification.as_text()[:TELEGRAM_MAX_LENGTH],
        }
