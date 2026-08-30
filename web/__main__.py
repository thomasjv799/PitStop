"""`python -m web` — the development server.

In production run uvicorn directly so the worker count and proxy headers
are yours to set:

    uvicorn web.app:app --host 0.0.0.0 --port 8000 --proxy-headers
"""

import os

import uvicorn
from dotenv import load_dotenv

from utils.env import env_bool, env_int, env_str

load_dotenv()

if __name__ == "__main__":
    uvicorn.run(
        "web.app:app",
        host=env_str("WEB_HOST", "127.0.0.1"),
        port=env_int("WEB_PORT", 8000),
        reload=env_bool("WEB_RELOAD", False),
    )
