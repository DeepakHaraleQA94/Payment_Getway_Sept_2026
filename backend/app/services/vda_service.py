"""Digital-asset / VDA integration boundary (foundation stub).

Kept strictly behind configuration/feature flags. No live VDA settlement here.
"""

ENABLED_BY_DEFAULT = False


def boundary_status() -> dict:
    return {
        "enabled": False,
        "supported_assets": [],
        "note": "VDA/digital-asset settlement is a controlled boundary; disabled by default.",
    }
