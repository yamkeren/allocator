"""Freeze excludes the node from new sessions; unfreeze re-enables it. And a
raw duplicate-devices POST is rejected by the API with 422 (the client library
would reject duplicates locally, so we bypass it with httpx)."""

import httpx

from allocator_client.client import AllocatorClient


def test_freeze_excludes_then_unfreeze_restores(manager_url):
    client = AllocatorClient(manager_url=manager_url, client_id="e2e-freeze")
    try:
        active = client.create_session(["wifi_0"])
        assert active.status == "ACTIVE"

        # freeze this session's node
        client.freeze_session_node(active.session_id)

        # a new session for hid_0 on the same (now frozen) node must queue
        pending = client.create_session(["hid_0"])
        assert pending.status == "PENDING"

        # unfreeze -> the queued hid_0 session can now allocate
        client.unfreeze_node("stub-1")
        import time
        deadline = time.time() + 30
        while time.time() < deadline:
            if client.get_session(pending.session_id).status == "ACTIVE":
                break
            time.sleep(0.5)
        assert client.get_session(pending.session_id).status == "ACTIVE"

        client.release_session(active.session_id)
        client.release_session(pending.session_id)
    finally:
        client.unfreeze_node("stub-1")
        client.close()


def test_duplicate_devices_rejected_by_api(manager_url):
    # Bypass the client library (which rejects duplicates locally) and POST raw.
    resp = httpx.post(
        f"{manager_url}/api/v1/sessions",
        json={"devices": ["wifi_0", "wifi_0"]},
        headers={"X-Client-Id": "e2e-dup"},
        timeout=10,
    )
    assert resp.status_code == 422
