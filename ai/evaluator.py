"""Listing evaluation with the locally installed, logged-in Claude Code CLI – no API key needed.

Each listing is one non-interactive `claude -p` run that uses the user's Claude subscription. The CLI
validates the answer against the JSON schema of `Evaluation` (--json-schema). It runs without any tools,
hooks, plugins, MCP servers or skills: listing texts are third-party content and must never be able to
make Claude act on this computer (which is why --dangerously-skip-permissions is never used).
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import tempfile
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

import config
from ai.prompts import build_listing_prompt, build_system_prompt

if TYPE_CHECKING:
    from database import Listing

log = logging.getLogger(__name__)

# Scoring a listing needs moderate reasoning – "medium" is a good quality/usage balance.
EFFORT = "medium"
TIMEOUT_SECONDS = 180
DIAGNOSTICS_TIMEOUT_SECONDS = 20
# Never let a key from .env switch the CLI from the subscription to paid API billing.
STRIPPED_ENV_VARS = ("ANTHROPIC_API_KEY",)
LOGIN_HINT = "uruchom w terminalu `claude auth login`"
_MAX_ERROR_LENGTH = 300


class ConsistencyCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    is_consistent: bool = Field(
        description="Czy opis jest zgodny z parametrami ogłoszenia i filtrami kupującego"
    )
    issues: list[str] = Field(description="Wykryte sprzeczności; pusta lista, jeśli ich brak")


class Evaluation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    score: int = Field(ge=0, le=100, description="Dopasowanie oferty do kryteriów kupującego, 0–100")
    pros: list[str] = Field(description="Kluczowe zalety wynikające z parametrów i opisu")
    cons: list[str] = Field(
        description="Wady, ryzyka i red flags, w tym brak ważnych informacji w ogłoszeniu"
    )
    summary: str = Field(description="2–3 zdania o opłacalności i atrakcyjności oferty")
    consistency: ConsistencyCheck


@dataclass(frozen=True)
class EvaluationResult:
    evaluation: Evaluation
    tokens: int  # all tokens of the run (input incl. cache + output) – counts towards the daily limit


class EvaluationError(Exception):
    """A single listing could not be evaluated."""

    def __init__(self, message: str, *, tokens: int = 0) -> None:
        super().__init__(message)
        self.tokens = tokens


class FatalEvaluationError(EvaluationError):
    """Failure affecting every run (CLI missing, not logged in, usage limit…) – stop evaluating in this scan."""


@dataclass(frozen=True)
class CliStatus:
    version: str | None = None
    logged_in: bool = False
    auth_method: str | None = None
    subscription: str | None = None
    error: str | None = None


class Evaluator:
    def __init__(
        self,
        *,
        model: str,
        criteria: list[dict[str, str]],
        filters: dict[str, Any],
        target: dict[str, Any] | None = None,
    ) -> None:
        self.model = model
        self._target = target
        self._command = [
            config.CLAUDE_CLI,
            "--print",
            "--output-format", "json",
            "--json-schema", json.dumps(evaluation_schema(), ensure_ascii=False),
            "--system-prompt", build_system_prompt(criteria, filters, target),
            "--model", model,
            "--effort", EFFORT,
            # No tools at all, and no user/project settings (hooks, plugins), MCP servers or skills.
            "--tools", "",
            "--restricted",
            "--strict-mcp-config",
            "--disable-slash-commands",
            "--no-session-persistence",
        ]  # fmt: skip

    def evaluate(self, listing: Listing) -> EvaluationResult:
        # The listing goes through stdin – no argument-length limits, nothing visible in `ps`.
        envelope = _run_cli(self._command, build_listing_prompt(listing, self._target))
        tokens = _total_tokens(envelope.get("usage"))
        if envelope.get("is_error"):
            raise _classify_error(envelope, self.model, tokens)
        if envelope.get("stop_reason") == "refusal":
            raise EvaluationError("Model odmówił analizy tego ogłoszenia", tokens=tokens)
        try:
            data = envelope.get("structured_output") or parse_json_from_response(str(envelope.get("result") or ""))
            evaluation = Evaluation.model_validate(data)
        except (ValueError, ValidationError) as exc:
            raise EvaluationError(f"Niepoprawna odpowiedź modelu: {_shorten(str(exc))}", tokens=tokens) from exc
        log.info("Scored listing %s: %s/100 (%s tokens, model %s)", listing.id, evaluation.score, tokens, self.model)
        return EvaluationResult(evaluation, tokens)


def evaluation_schema() -> dict[str, Any]:
    """JSON schema of Evaluation with $defs inlined (a single self-contained object for --json-schema)."""
    schema = Evaluation.model_json_schema()
    definitions = schema.pop("$defs", {})

    def inline(node: Any) -> Any:
        if isinstance(node, dict):
            if "$ref" in node:
                return inline(definitions[node["$ref"].rsplit("/", 1)[-1]])
            return {key: inline(value) for key, value in node.items()}
        if isinstance(node, list):
            return [inline(item) for item in node]
        return node

    return dict(inline(schema))


def parse_json_from_response(text: str) -> dict[str, Any]:
    """The first JSON object in a model answer, tolerating Markdown fences or prose around it."""
    decoder = json.JSONDecoder()
    start = text.find("{")
    while start != -1:
        try:
            value, _ = decoder.raw_decode(text, start)
        except json.JSONDecodeError:
            start = text.find("{", start + 1)
            continue
        if isinstance(value, dict):
            return value
        start = text.find("{", start + 1)
    raise ValueError("odpowiedź nie zawiera obiektu JSON")


def cli_status() -> CliStatus:
    """Diagnostics: `claude --version` and `claude auth status` (neither uses any tokens)."""
    try:
        version = _run_plain([config.CLAUDE_CLI, "--version"]).strip()
        auth = json.loads(_run_plain([config.CLAUDE_CLI, "auth", "status", "--json"]))
    except FatalEvaluationError as exc:
        return CliStatus(error=str(exc))
    except json.JSONDecodeError:
        return CliStatus(version=version, error="Nieczytelna odpowiedź `claude auth status`")
    return CliStatus(
        version=version,
        logged_in=bool(auth.get("loggedIn")),
        auth_method=auth.get("authMethod"),
        subscription=auth.get("subscriptionType"),
    )


def _run_cli(command: list[str], prompt: str) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            command,
            input=prompt,
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS,
            cwd=tempfile.gettempdir(),  # keep the project's CLAUDE.md out of the evaluation context
            env=_cli_env(),
            check=False,
        )
    except FileNotFoundError as exc:
        raise FatalEvaluationError(_missing_cli_message()) from exc
    except subprocess.TimeoutExpired as exc:
        raise EvaluationError(f"Claude Code nie odpowiedział w ciągu {TIMEOUT_SECONDS} s") from exc
    try:
        envelope = json.loads(completed.stdout)
    except json.JSONDecodeError:
        envelope = None
    if not isinstance(envelope, dict):
        output = completed.stderr.strip() or completed.stdout.strip()
        details = _shorten(output or f"kod wyjścia {completed.returncode}")
        if completed.returncode != 0:  # the CLI itself failed (crash, broken install, auth prompt…)
            raise FatalEvaluationError(f"Claude Code zakończył się błędem: {details}")
        raise EvaluationError(f"Nieczytelna odpowiedź Claude Code: {details}")
    return envelope


def _run_plain(command: list[str]) -> str:
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=DIAGNOSTICS_TIMEOUT_SECONDS,
            env=_cli_env(),
            check=False,
        )
    except FileNotFoundError as exc:
        raise FatalEvaluationError(_missing_cli_message()) from exc
    except subprocess.TimeoutExpired as exc:
        raise FatalEvaluationError(
            f"`{' '.join(command)}` nie odpowiedziało w ciągu {DIAGNOSTICS_TIMEOUT_SECONDS} s"
        ) from exc
    if completed.returncode != 0 and not completed.stdout.strip():
        raise FatalEvaluationError(f"`{' '.join(command)}`: {_shorten(completed.stderr.strip())}")
    return completed.stdout


def _classify_error(envelope: dict[str, Any], model: str, tokens: int) -> EvaluationError:
    status = envelope.get("api_error_status")
    message = _shorten(str(envelope.get("result") or envelope.get("subtype") or "nieznany błąd"))
    lowered = message.lower()
    if status in (401, 403) or "login" in lowered or "log in" in lowered or "authenticat" in lowered:
        return FatalEvaluationError(f"Claude Code nie jest zalogowany – {LOGIN_HINT} ({message})", tokens=tokens)
    if status == 404:
        return FatalEvaluationError(f"Model {model} jest niedostępny w Claude Code ({message})", tokens=tokens)
    if status == 429 or "limit" in lowered:
        return FatalEvaluationError(
            f"Osiągnięto limit użycia Claude – dokończę przy następnym skanowaniu ({message})", tokens=tokens
        )
    if isinstance(status, int) and status >= 500:
        return FatalEvaluationError(f"Usługa Claude chwilowo niedostępna (HTTP {status})", tokens=tokens)
    return EvaluationError(f"Błąd Claude Code: {message}", tokens=tokens)


def _cli_env() -> dict[str, str]:
    return {name: value for name, value in os.environ.items() if name not in STRIPPED_ENV_VARS}


def _missing_cli_message() -> str:
    return (
        f"Nie znaleziono komendy `{config.CLAUDE_CLI}` – zainstaluj Claude Code (https://claude.com/claude-code), "
        f"{LOGIN_HINT} albo ustaw CLAUDE_CLI_PATH w .env"
    )


def _total_tokens(usage: Any) -> int:
    if not isinstance(usage, dict):
        return 0
    keys = ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")
    return sum(int(usage.get(key) or 0) for key in keys)


def _shorten(text: str) -> str:
    return text if len(text) <= _MAX_ERROR_LENGTH else text[: _MAX_ERROR_LENGTH - 1] + "…"
