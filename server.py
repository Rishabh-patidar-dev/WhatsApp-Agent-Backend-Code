"""Deployment entry point.

Kept so the existing Render start command (`uvicorn server:app`) keeps working.
The application itself now lives in app/ — start reading at app/main.py.
"""
from app.main import app  # noqa: F401
