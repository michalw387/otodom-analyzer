from __future__ import annotations

import json

import httpx2 as httpx
import pytest

from database import Listing
from notifications import webhook

TELEGRAM_TOKEN = "123456:ABC-secret_token"


def _notifier(channel: str, handler, **settings) -> tuple[webhook.Notifier, list[httpx.Request]]:
    requests: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return handler(request)

    client = httpx.Client(transport=httpx.MockTransport(record))
    return webhook.Notifier.from_settings({"channel": channel, **settings}, client=client), requests


def ok(_request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"ok": True})


@pytest.fixture
def listing(make_listing) -> Listing:
    return Listing(**make_listing(1, title="Słoneczne 3 pokoje", price=799_000.0, area=66.7, score=88))


def test_new_listing_message_has_key_facts(listing):
    analysis = {"pros": ["Balkon", "Metro 300 m", "Cicho", "Czwarta zaleta"], "cons": ["Brak windy"]}

    message = webhook.new_listing_message(listing, analysis, 88)

    assert message.title == "🏠 Nowa oferta 88/100"
    assert "Słoneczne 3 pokoje" in message.body
    assert "799\u00a0000\u00a0zł" in message.body and "66,7 m²" in message.body
    assert "✅ Metro 300 m" in message.body and "Czwarta zaleta" not in message.body  # top 3 only
    assert "⚠️ Brak windy" in message.body
    assert message.url == listing.url


def test_price_drop_message(listing):
    message = webhook.price_drop_message(listing, 800_000, 760_000)

    assert message.title == "📉 Obniżka ceny!"
    assert message.body.startswith("Z 800\u00a0000\u00a0zł na 760\u00a0000\u00a0zł (−5%)")


def test_ntfy_publishes_json_to_server_root():
    notifier, requests = _notifier("ntfy", ok, webhook_url="https://ntfy.sh/moj-temat")

    notifier.send(webhook.Notification("Tytuł ąę", "Treść", "https://www.otodom.pl/pl/oferta/x"))

    [request] = requests
    assert str(request.url) == "https://ntfy.sh/"
    assert json.loads(request.content) == {
        "topic": "moj-temat",
        "title": "Tytuł ąę",
        "message": "Treść",
        "tags": ["house"],
        "click": "https://www.otodom.pl/pl/oferta/x",
    }


def test_ntfy_self_hosted_under_a_path():
    notifier, requests = _notifier("ntfy", ok, webhook_url="https://example.com/ntfy/alerts/")

    notifier.send(webhook.test_message())

    assert str(requests[0].url) == "https://example.com/ntfy"
    assert json.loads(requests[0].content)["topic"] == "alerts"


def test_discord_disables_mentions_and_truncates():
    notifier, requests = _notifier("discord", ok, webhook_url="https://discord.com/api/webhooks/1/abc")

    notifier.send(webhook.Notification("@everyone kup teraz", "x" * 3000))

    payload = json.loads(requests[0].content)
    assert payload["allowed_mentions"] == {"parse": []}
    assert len(payload["content"]) == webhook.DISCORD_MAX_LENGTH
    assert payload["content"].startswith("@everyone kup teraz\n")


def test_telegram_uses_bot_api(monkeypatch):
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "-100123")  # .env fallback for an empty UI field
    notifier, requests = _notifier("telegram", ok, telegram_bot_token=TELEGRAM_TOKEN, telegram_chat_id="")

    notifier.send(webhook.Notification("Tytuł", "Treść", "https://www.otodom.pl/"))

    assert str(requests[0].url) == f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    assert json.loads(requests[0].content) == {"chat_id": "-100123", "text": "Tytuł\nTreść\nhttps://www.otodom.pl/"}


def test_http_error_does_not_leak_the_bot_token():
    def unauthorized(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"ok": False, "description": "Unauthorized"})

    notifier, _ = _notifier("telegram", unauthorized, telegram_bot_token=TELEGRAM_TOKEN, telegram_chat_id="1")

    with pytest.raises(webhook.NotificationError, match="HTTP 401") as excinfo:
        notifier.send(webhook.test_message())
    assert "secret_token" not in str(excinfo.value)


def test_connection_error_is_a_notification_error():
    def broken(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    notifier, _ = _notifier("telegram", broken, telegram_bot_token=TELEGRAM_TOKEN, telegram_chat_id="1")

    with pytest.raises(webhook.NotificationError, match="błąd połączenia") as excinfo:
        notifier.send(webhook.test_message())
    assert "secret_token" not in str(excinfo.value)


@pytest.mark.parametrize(
    ("settings", "expected"),
    [
        ({"channel": "ntfy", "webhook_url": "https://ntfy.sh/temat"}, []),
        ({"channel": "ntfy", "webhook_url": "https://ntfy.sh/"}, ["nazwę tematu"]),
        ({"channel": "discord", "webhook_url": "nie-url"}, ["poprawny URL"]),
        ({"channel": "telegram", "telegram_bot_token": "zly", "telegram_chat_id": ""}, ["token bota", "chat ID"]),
        ({"channel": "sms"}, ["Nieznany kanał"]),
    ],
)
def test_config_errors(settings, expected):
    errors = webhook.config_errors(settings)
    assert len(errors) == len(expected)
    for error, fragment in zip(errors, expected, strict=True):
        assert fragment in error


def test_webhook_url_falls_back_to_env(monkeypatch):
    monkeypatch.setenv("NOTIFY_WEBHOOK_URL", "https://ntfy.sh/z-env")
    notifier, requests = _notifier("ntfy", ok, webhook_url="")

    notifier.send(webhook.test_message())

    assert json.loads(requests[0].content)["topic"] == "z-env"


def test_from_settings_rejects_incomplete_configuration():
    with pytest.raises(webhook.NotificationError, match="URL"):
        webhook.Notifier.from_settings({"channel": "discord", "webhook_url": ""})
