"""Brings the full stack up under docker-compose for the test session.

Session scope: one `up -d --build`, health-wait, then `down -v`. A per-test
autouse fixture unfreezes the node so a freeze test cannot leak state.
"""

import subprocess
import time
from pathlib import Path

import httpx
import pytest

E2E_DIR = Path(__file__).resolve().parent.parent
COMPOSE = ["docker", "compose", "-f", str(E2E_DIR / "docker-compose.test.yml")]
MANAGER_URL = "http://localhost:18080"
STUB_URL = "http://localhost:15000"


def _wait_http(url: str, timeout: float = 180.0):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        try:
            r = httpx.get(url, timeout=3)
            if r.status_code == 200:
                return r
        except Exception as exc:  # noqa: BLE001
            last = exc
        time.sleep(2)
    raise RuntimeError(f"timed out waiting for {url}: {last}")


def _wait_stub_registered(timeout: float = 90.0) -> str:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = httpx.get(f"{STUB_URL}/health", timeout=3)
            if r.status_code == 200 and r.json().get("node_id"):
                return r.json()["node_id"]
        except Exception:  # noqa: BLE001
            pass
        time.sleep(2)
    raise RuntimeError("stub agent never registered a node_id")


@pytest.fixture(scope="session")
def stack():
    subprocess.run(COMPOSE + ["up", "-d", "--build"], cwd=E2E_DIR, check=True)
    try:
        _wait_http(f"{MANAGER_URL}/health")
        node_id = _wait_stub_registered()
        yield {"manager_url": MANAGER_URL, "stub_url": STUB_URL, "node_id": node_id}
    finally:
        subprocess.run(COMPOSE + ["down", "-v"], cwd=E2E_DIR, check=False)


@pytest.fixture
def manager_url(stack):
    return stack["manager_url"]


@pytest.fixture
def stub_url(stack):
    return stack["stub_url"]


@pytest.fixture(autouse=True)
def _unfreeze_after(stack):
    """Safety net: unfreeze stub-1 after every test (idempotent, no auth)."""
    yield
    try:
        httpx.post(f"{stack['manager_url']}/api/v1/nodes/stub-1/unfreeze", timeout=5)
    except Exception:  # noqa: BLE001
        pass
