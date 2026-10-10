"""Run with uvicorn app.api.main:app."""

from app.api.application import create_app

app = create_app()
