"""Polish number formatting shared by the UI, prompts, notifications and exports."""

from __future__ import annotations

NBSP = "\u00a0"


def thousands(value: float) -> str:
    """1200000 -> '1 200 000' (non-breaking spaces, no decimals)."""
    return f"{value:,.0f}".replace(",", NBSP)


def pln(value: float | None) -> str | None:
    return f"{thousands(value)}{NBSP}zł" if value else None


def decimal(value: float) -> str:
    """66.7 -> '66,7'."""
    return f"{value:g}".replace(".", ",")


def token_usage(used: int, limit: int) -> str:
    """Status line with today's AI token usage against the daily limit (0 = no limit)."""
    if not limit:
        return f"🪙 Tokeny AI dziś: {thousands(used)} (bez limitu)"
    # Not capped at 100%: the limit is checked before each evaluation, so the last one can overshoot it.
    text = f"🪙 Tokeny AI dziś: {thousands(used)} / {thousands(limit)} ({round(used / limit * 100)}% limitu)"
    if used >= limit:
        text += " – limit wyczerpany, oceny wstrzymane do północy"
    return text


def plural(n: int, one: str, few: str, many: str) -> str:
    """Polish plural form: 1 oferta, 2 oferty, 5 ofert."""
    if n == 1:
        return one
    if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
        return few
    return many
