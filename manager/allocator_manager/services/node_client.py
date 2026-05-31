"""HTTP client for calling node agent endpoints.

Wraps httpx with:
- configurable timeout
- retry with exponential backoff (tenacity)
- per-node circuit breaker (simple counter-based)
"""

from dataclasses import dataclass

import httpx
import structlog
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from allocator_manager.config import settings

log = structlog.get_logger(__name__)


@dataclass
class BindResult:
    bus_id: str
    bound: bool


@dataclass
class UnbindResult:
    unbound: bool


class NodeCircuitOpenError(Exception):
    def __init__(self, node_url: str) -> None:
        self.node_url = node_url
        super().__init__(f"Circuit open for node {node_url}")


class NodeAgentClient:
    """Communicates with a single node agent. One instance per (session, node)."""

    # Class-level circuit breaker state per node URL
    _failure_counts: dict[str, int] = {}
    _circuit_open: set[str] = set()

    def __init__(self, node_url: str) -> None:
        self._base_url = node_url.rstrip("/")
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=settings.agent_request_timeout,
            headers={settings.agent_secret_header: settings.agent_secret},
        )

    @retry(
        retry=retry_if_exception_type((httpx.ConnectError, httpx.TimeoutException)),
        stop=stop_after_attempt(settings.agent_max_retries),
        wait=wait_exponential(multiplier=0.5, min=0.5, max=5),
        reraise=True,
    )
    async def bind(self, bus_id: str, logical_name: str, session_id: str) -> BindResult:
        self._check_circuit()
        try:
            response = await self._client.post(
                "/api/v1/usbip/bind",
                json={"bus_id": bus_id, "logical_name": logical_name, "session_id": session_id},
            )
            response.raise_for_status()
            data = response.json()
            self._record_success()
            return BindResult(bus_id=data.get("bus_id", bus_id), bound=data.get("bound", True))
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            self._record_failure()
            raise

    @retry(
        retry=retry_if_exception_type((httpx.ConnectError, httpx.TimeoutException)),
        stop=stop_after_attempt(settings.agent_max_retries),
        wait=wait_exponential(multiplier=0.5, min=0.5, max=5),
        reraise=True,
    )
    async def unbind(self, bus_id: str, logical_name: str) -> UnbindResult:
        self._check_circuit()
        try:
            response = await self._client.post(
                "/api/v1/usbip/unbind",
                json={"bus_id": bus_id, "logical_name": logical_name},
            )
            response.raise_for_status()
            self._record_success()
            return UnbindResult(unbound=True)
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            self._record_failure()
            raise

    def _check_circuit(self) -> None:
        if self._base_url in NodeAgentClient._circuit_open:
            raise NodeCircuitOpenError(self._base_url)

    def _record_failure(self) -> None:
        count = NodeAgentClient._failure_counts.get(self._base_url, 0) + 1
        NodeAgentClient._failure_counts[self._base_url] = count
        if count >= settings.agent_circuit_failure_threshold:
            NodeAgentClient._circuit_open.add(self._base_url)
            log.warning("circuit_opened", node_url=self._base_url, failures=count)

    def _record_success(self) -> None:
        NodeAgentClient._failure_counts[self._base_url] = 0
        NodeAgentClient._circuit_open.discard(self._base_url)

    async def aclose(self) -> None:
        await self._client.aclose()
