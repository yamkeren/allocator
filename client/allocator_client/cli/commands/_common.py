"""Shared helpers for CLI commands."""

from allocator_client.client import AllocatorClient


def get_client() -> AllocatorClient:
    """Return a client configured from the JSON config file.

    Use as `with get_client() as client:`. Configure with `allocator config set
    url …` (~/.config/allocator/config.json); defaults to http://localhost:8000
    and the host's name.
    """
    return AllocatorClient()
