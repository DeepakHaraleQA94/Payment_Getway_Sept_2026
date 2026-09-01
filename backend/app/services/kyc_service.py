"""KYC/AML boundary (foundation stub).

Regulated capability. Real provider integration stays behind a feature flag until
legal/provider prerequisites are satisfied. Do not claim regulatory approval.
"""

ENABLED_BY_DEFAULT = False


def provider_status() -> dict:
    return {
        "configured": False,
        "provider": None,
        "note": "KYC/AML disabled until a licensed provider is configured (feature-flagged).",
    }
