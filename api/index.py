"""Vercel serverless entrypoint.

Vercel runs `app` as an ASGI application; there is no uvicorn process. Only
the web tier lives here — the Discord and Telegram listeners are long-running
connections and cannot run on a serverless function (see docs/deploy-vercel.md),
and the reminder sweep stays on GitHub Actions.
"""

import sys
from pathlib import Path

# The function's bundle root is the repo root, but it is not automatically on
# sys.path — without this, `from web.app import app` fails at cold start.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from web.app import app  # noqa: E402

__all__ = ["app"]
