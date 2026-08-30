"""Reading environment variables that may be present but empty.

`os.getenv(name, default)` returns the default only when the variable is
*absent*. A variable that exists with an empty value comes back as `""`, so

    int(os.getenv("WEB_SOON_DAYS", "30"))

raises ValueError rather than yielding 30. That is not a hypothetical: adding
a key and leaving the box blank is ordinary behaviour in the Vercel, Railway
and GitHub Actions UIs, and at import time it takes the whole process down.

These treat blank as absent, and a malformed number as absent-with-a-warning
rather than a crash — a bad tuning value should not stop the app booting.
"""

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

TRUTHY = {"1", "true", "yes", "on"}


def env_str(name: str, default: str = "") -> str:
    """Stripped value, or `default` when unset or blank."""
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    return value.strip()


def env_int(name: str, default: int) -> int:
    raw = env_str(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        logger.warning("%s=%r is not a number; using %d", name, raw, default)
        return default


def env_bool(name: str, default: bool = False) -> bool:
    raw = env_str(name)
    if not raw:
        return default
    return raw.lower() in TRUTHY


def env_list(name: str, sep: str = ",") -> list[str]:
    """Split, trimmed, blanks dropped — `"a, ,b"` -> `["a", "b"]`."""
    return [part.strip() for part in env_str(name).split(sep) if part.strip()]


def env_optional(name: str) -> Optional[str]:
    return env_str(name) or None
