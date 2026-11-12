from formatting import NBSP, token_usage


def test_token_usage_without_limit():
    assert token_usage(12_345, 0) == f"🪙 Tokeny AI dziś: 12{NBSP}345 (bez limitu)"


def test_token_usage_below_limit():
    assert token_usage(25_000, 100_000) == f"🪙 Tokeny AI dziś: 25{NBSP}000 / 100{NBSP}000 (25% limitu)"


def test_token_usage_over_limit_shows_real_percentage():
    text = token_usage(191_686, 100_000)
    assert "(192% limitu)" in text
    assert "limit wyczerpany" in text
