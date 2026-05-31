"""Shared helpers for CLI commands."""

import os
from contextlib import asynccontextmanager

from allocator_client.client import AllocatorClient


def _get_settings() -> tuple[str, str]:
    manager_url = os.environ.get("ALLOCATOR_URL", "http://localhost:8000")
    api_key = os.environ.get("ALLOCATOR_API_KEY", "")
    if not api_key:
        import typer
        typer.echo("Error: ALLOCATOR_API_KEY environment variable not set", err=True)
        raise typer.Exit(1)
    return manager_url, api_key


@asynccontextmanager
async def get_client():
    manager_url, api_key = _get_settings()
    async with AllocatorClient(manager_url, api_key) as client:
        yield client
