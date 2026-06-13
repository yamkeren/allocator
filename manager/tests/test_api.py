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


# ----- dashboard: overview + operator actions ------------------------------

def _find_device(overview, node_name, logical):
    node = next(n for n in overview["nodes"] if n["name"] == node_name)
    return next(d for d in node["devices"] if d["logical_name"] == logical)


async def test_overview_lists_nodes_and_devices(client, make_node, make_device):
    node = await make_node()
    await make_device(node, "wifi_0")

    resp = await client.get("/api/v1/overview")  # no auth headers
    assert resp.status_code == 200
    body = resp.json()
    assert body["manager"]["status"] == "healthy"
    dev = _find_device(body, node.name, "wifi_0")
    assert dev["status"] == "FREE"
    assert dev["owner"] is None


async def test_overview_shows_allocated_owner_and_active_session(client, make_node, make_device):
    node = await make_node()
    await make_device(node, "wifi_0")
    created = await client.post("/api/v1/sessions", json={"devices": ["wifi_0"]}, headers=CID)
    assert created.status_code == 201 and created.json()["status"] == "ACTIVE"
    sid = created.json()["session_id"]

    body = (await client.get("/api/v1/overview")).json()
    dev = _find_device(body, node.name, "wifi_0")
    assert dev["status"] == "ALLOCATED"
    assert dev["owner"] == {"client_id": "host-a", "session_id": sid}

    assert len(body["active_sessions"]) == 1
    sess = body["active_sessions"][0]
    assert sess["client_id"] == "host-a"
    assert sess["node_name"] == node.name
    assert [d["logical_name"] for d in sess["devices"]] == ["wifi_0"]


async def test_overview_lists_pending_sessions(client):
    created = await client.post("/api/v1/sessions", json={"devices": ["wifi_0"]}, headers=CID)
    assert created.json()["status"] == "PENDING"  # no nodes/devices exist

    body = (await client.get("/api/v1/overview")).json()
    assert len(body["pending_sessions"]) == 1
    assert body["pending_sessions"][0]["requested_devices"] == ["wifi_0"]


async def test_freeze_and_unfreeze_node(client, make_node):
    node = await make_node()

    frozen = await client.post(f"/api/v1/nodes/{node.name}/freeze")
    assert frozen.status_code == 200 and frozen.json()["frozen"] is True

    thawed = await client.post(f"/api/v1/nodes/{node.name}/unfreeze")
    assert thawed.status_code == 200 and thawed.json()["frozen"] is False


async def test_force_release_bypasses_ownership(client, make_node, make_device, agent):
    node = await make_node()
    await make_device(node, "wifi_0")
    created = await client.post("/api/v1/sessions", json={"devices": ["wifi_0"]}, headers=CID)
    assert created.json()["status"] == "ACTIVE"
    sid = created.json()["session_id"]

    # Operator (different client / no X-Client-Id) force-releases it.
    resp = await client.post(f"/api/v1/sessions/{sid}/force-release")
    assert resp.status_code == 204
    assert "wifi_0" in agent.unbinds

    body = (await client.get("/api/v1/overview")).json()
    assert _find_device(body, node.name, "wifi_0")["status"] == "FREE"
    assert body["active_sessions"] == []


async def test_force_release_unknown_session_is_404(client):
    resp = await client.post(f"/api/v1/sessions/{uuid.uuid4()}/force-release")
    assert resp.status_code == 404
