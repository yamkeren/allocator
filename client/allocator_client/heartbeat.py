"""Background heartbeat thread for active sessions.

Runs in a daemon thread so it doesn't block the main program.
Stops automatically when the session is released or on KeyboardInterrupt.
"""

import threading
import time

import structlog

log = structlog.get_logger(__name__)


class HeartbeatThread:
    def __init__(
        self,
        session_id: str,
        manager_url: str,
        api_key: str,
        interval: int = 30,
    ) -> None:
        self._session_id = session_id
        self._manager_url = manager_url
        self._api_key = api_key
        self._interval = interval
        self._stop_event = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        self._thread.join(timeout=5)

    def _run(self) -> None:
        import httpx

        while not self._stop_event.wait(timeout=self._interval):
            try:
                with httpx.Client(
                    base_url=self._manager_url,
                    headers={"X-API-Key": self._api_key},
                    timeout=5.0,
                ) as client:
                    resp = client.post(f"/api/v1/sessions/{self._session_id}/heartbeat")
                    if resp.status_code == 200:
                        log.debug("heartbeat_sent", session_id=self._session_id)
                    else:
                        log.warning(
                            "heartbeat_rejected",
                            session_id=self._session_id,
                            status=resp.status_code,
                        )
            except Exception as exc:
                log.warning(
                    "heartbeat_error",
                    session_id=self._session_id,
                    error=str(exc),
                )
