"""AI / voice boundary (foundation stub).

Interface placeholder for future AI assist / voice command features. Disabled until
an LLM/voice provider is explicitly configured.
"""

ENABLED_BY_DEFAULT = False


def boundary_status() -> dict:
    return {
        "enabled": False,
        "capabilities": ["assistant", "voice_commands"],
        "note": "AI/voice boundary is inert until an approved provider is configured.",
    }
