"""Known target-display matching for physical glassless acceptance gates."""
from __future__ import annotations

import re

TARGET_DISPLAY_TERMS = (
    "spatiallabs",
    "odyssey 3d",
    "odyssey3d",
    "thinkvision 27 3d",
    "thinkvision27 3d",
    "lume pad",
    "lume pad 2",
    "lumepad",
    "lumepad2",
    "leiasr",
    "looking glass",
    "lookingglass",
    "simulated reality",
    "simulatedreality",
    "sr display",
)


def inventory_text_is_known_target(haystack: str) -> bool:
    text = haystack.lower()
    compact_text = compact_display_text(text)
    return any(term in text or compact_display_text(term) in compact_text for term in TARGET_DISPLAY_TERMS)


def compact_display_text(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())
