"""FastAPI application package for the investment workbench."""

from fastapi import FastAPI

from lucking.config import Settings


def create_app(settings: Settings | None = None) -> FastAPI:
    """Import the application factory lazily to keep DTO imports acyclic."""
    from lucking.api.main import create_app as factory

    return factory(settings)

__all__ = ["create_app"]
