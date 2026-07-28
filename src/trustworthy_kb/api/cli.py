"""Local Uvicorn entry point for the trusted answer API."""

from __future__ import annotations

import uvicorn

from trustworthy_kb.config import AnswerSettings


def main() -> None:
    settings = AnswerSettings(_env_file=".env")
    uvicorn.run(
        "trustworthy_kb.api.app:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=False,
        proxy_headers=False,
        access_log=False,
    )


__all__ = ["main"]
