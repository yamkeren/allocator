"""FastAPI routes via ASGITransport against the real test DB. The `client`
fixture overrides get_db, and patches the agent + queue kick."""

import uuid

AGENT = {"X-Agent-Secret": "dev-agent-secret"}
CID = {"X-Client-Id": "host-a"}


async def test_create_session_requires_client_id(client):
    resp = await client.post("/api/v1/sessions", json={"devices": ["wifi_0"]})
    assert resp.status_code == 400  # missing X-Client-Id


async def test_register_requires_agent_secret(client):
    body = {"name": "lab-1", "hostname": "lab-1.local", "ip_address": "10.0.0.5"}
    bad = await client.post("/internal/v1/nodes/register", json=body)
    assert bad.status_code == 401
    ok = await client.post("/internal/v1/nodes/register", json=body, headers=AGENT)
    assert ok.status_code == 200
    assert ok.json()["registered"] is True


async def test_create_session_pending_returns_201(client):
    resp = await client.post("/api/v1/sessions", json={"devices": ["wifi_0"]}, headers=CID)
    assert resp.status_code == 201
    assert resp.json()["status"] == "PENDING"


async def test_duplicate_devices_is_422(client):
    resp = await client.post("/api/v1/sessions", json={"devices": ["wifi_0", "wifi_0"]}, headers=CID)
    assert resp.status_code == 422


async def test_get_unknown_session_is_404(client):
    resp = await client.get(f"/api/v1/sessions/{uuid.uuid4()}", headers=CID)
    assert resp.status_code == 404


async def test_release_other_clients_session_is_403(client, make_node, make_device):
    node = await make_node()
    await make_device(node, "wifi_0")
    created = await client.post("/api/v1/sessions", json={"devices": ["wifi_0"]}, headers=CID)
    assert created.status_code == 201 and created.json()["status"] == "ACTIVE"
    sid = created.json()["session_id"]

    resp = await client.delete(f"/api/v1/sessions/{sid}", headers={"X-Client-Id": "host-b"})
    assert resp.status_code == 403


async def test_rename_conflict_is_409(client, make_node, make_device):
    node = await make_node()
    await make_device(node, "wifi_0", fingerprint="fpw")
    await make_device(node, "video_0", fingerprint="fpv")
    resp = await client.post(
        f"/api/v1/devices/{node.name}/video_0/rename",
        json={"name": "wifi_0", "force": False},
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "name_conflict"
