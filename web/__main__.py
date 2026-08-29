"""`python -m web` — the development server.

In production run uvicorn directly so the worker count and proxy headers
are yours to set:

    uvicorn web.app:app --host 0.0.0.0 --port 8000 --proxy-headers
"""

import os

import uvicorn
from dotenv import load_dotenv

load_dotenv()

if __name__ == "__main__":
    uvicorn.run(
        "web.app:app",
        host=os.getenv("WEB_HOST", "127.0.0.1"),
        port=int(os.getenv("WEB_PORT", "8000")),
        reload=os.getenv("WEB_RELOAD", "0").strip().lower() in {"1", "true", "yes"},
    )
