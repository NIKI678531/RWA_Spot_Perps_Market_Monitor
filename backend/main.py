"""Compatibility entrypoint so ``uvicorn main:app`` keeps working.

The real assembly lives in :mod:`app.main`. Nothing but the re-export belongs here.
"""

from __future__ import annotations

from app.main import app, create_app

__all__ = ["app", "create_app"]
