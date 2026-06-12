"""Two clients contend for the single wifi_0. The second is queued PENDING and
must wake to ACTIVE after the first releases (global skip-FIFO, end to end)."""

import time

from allocator_client.client import AllocatorClient


def _wait_status(client, session_id, target, timeout=30.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        status = client.get_session(session_id).status
        if status == target:
            return status
        time.sleep(0.5)
    return client.get_session(session_id).status


def test_queued_session_wakes_on_release(manager_url):
    c1 = AllocatorClient(manager_url=manager_url, client_id="e2e-q1")
    c2 = AllocatorClient(manager_url=manager_url, client_id="e2e-q2")
    try:
        s1 = c1.create_session(["wifi_0"])
        assert s1.status == "ACTIVE"

        s2 = c2.create_session(["wifi_0"])
        assert s2.status == "PENDING"  # only one wifi_0 -> queued

        c1.release_session(s1.session_id)

        # the queue processor (release kick + 15s safety sweep) promotes s2
        assert _wait_status(c2, s2.session_id, "ACTIVE") == "ACTIVE"

        c2.release_session(s2.session_id)
    finally:
        c1.close()
        c2.close()
