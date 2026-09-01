"""Risk engine (foundation stub interface).

Returns a deterministic 0-100 risk score. Real ML/rules integration is a boundary
kept behind configuration until a provider is configured.
"""


def score_payment(*, amount_minor: int, customer_email: str | None) -> int:
    score = 0
    if amount_minor > 500_00:  # > 500.00 major units
        score += 30
    if amount_minor > 5000_00:
        score += 30
    if not customer_email:
        score += 10
    return min(score, 100)
