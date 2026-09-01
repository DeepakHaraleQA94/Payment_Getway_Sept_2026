"""Centralized payment state-machine validation.

Enforces that only valid transitions occur server-side. The set of states mirrors
models.base.PaymentStatus; this module does not introduce new states.
"""

TERMINAL = {"failed", "refunded", "cancelled"}
REFUNDABLE = {"succeeded", "captured", "partially_refunded"}

ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "created": {"pending", "authorized", "failed", "cancelled"},
    "pending": {"authorized", "captured", "succeeded", "failed", "cancelled"},
    "authorized": {"captured", "succeeded", "cancelled", "failed"},
    "captured": {"succeeded", "partially_refunded", "refunded"},
    "succeeded": {"partially_refunded", "refunded"},
    "partially_refunded": {"partially_refunded", "refunded"},
    # terminal states allow no further transitions
    "failed": set(),
    "refunded": set(),
    "cancelled": set(),
}


class InvalidTransition(ValueError):
    pass


def validate_transition(current: str, new: str) -> None:
    """Raise InvalidTransition if moving from `current` to `new` is not allowed."""
    if current == new:
        return
    allowed = ALLOWED_TRANSITIONS.get(current, set())
    if new not in allowed:
        raise InvalidTransition(f"Invalid payment transition: {current} -> {new}")


def is_refundable(status: str) -> bool:
    return status in REFUNDABLE
