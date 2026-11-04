import pytest
from fastapi.testclient import TestClient

from identity_mcp.api.app import app
from identity_mcp.data import seed


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    path = tmp_path_factory.mktemp("db") / "app.db"
    seed.build(path)
    mp = pytest.MonkeyPatch()
    mp.setenv("IDENTITY_MCP_DB", str(path))
    yield TestClient(app)
    mp.undo()


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


@pytest.mark.parametrize("path", [
    "/users", "/groups", "/group-members", "/applications", "/entitlements", "/access-grants", "/activity-events",
])
def test_list_shape_and_pagination(client, path):
    r = client.get(path, params={"limit": 3, "offset": 1})
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"items", "total", "limit", "offset"}
    assert len(body["items"]) == 3 and body["total"] > 3 and body["offset"] == 1


def test_limit_is_capped(client):
    assert client.get("/users", params={"limit": 500}).status_code == 422


def test_user_filters(client):
    assert client.get("/users", params={"status": "terminated"}).json()["total"] == 50
    assert client.get("/users", params={"status": "bogus"}).status_code == 422
    body = client.get("/users", params={"q": "patel", "department": "Sales"}).json()
    assert all(u["department"] == "Sales" and "patel" in (u["name"] + u["email"]).lower() for u in body["items"])


def test_get_by_id_and_404(client):
    assert client.get("/users/1").json()["id"] == 1
    for path in ("/users/99999", "/groups/99999", "/applications/99999", "/entitlements/99999",
                 "/access-grants/99999", "/activity-events/99999", "/users/99999/grants"):
        assert client.get(path).status_code == 404


def test_orphaned_access_via_api(client):
    # terminated users who still hold grants: the planted finding is reachable through the API
    terminated = client.get("/users", params={"status": "terminated", "limit": 200}).json()["items"]
    holders = {u["id"] for u in terminated if client.get(f"/users/{u['id']}/grants", params={"limit": 1}).json()["total"] > 0}
    assert len(holders) == seed.PLANTED["orphaned_users"]


def test_nested_resources(client):
    g = client.get("/groups", params={"is_privileged": True}).json()
    assert g["total"] == 4
    members = client.get(f"/groups/{g['items'][0]['id']}/members").json()
    assert members["total"] > 0 and {"user_name", "email", "added_at"} <= set(members["items"][0])
    assert client.get("/applications/1/entitlements").json()["total"] == 2


def test_grants_are_enriched_and_filterable(client):
    admin = client.get("/access-grants", params={"is_admin": True, "granted_via": "direct"}).json()
    assert admin["total"] > 0
    assert all(g["is_admin"] == 1 and g["granted_via"] == "direct" and g["app_name"] for g in admin["items"])


def test_events_date_filter(client):
    body = client.get("/activity-events", params={"since": "2026-10-01", "until": "2026-11-01", "limit": 200}).json()
    assert body["total"] > 0 and all("2026-10-01" <= e["ts"] < "2026-11-01" for e in body["items"])
    assert body["items"] == sorted(body["items"], key=lambda e: e["ts"], reverse=True)


def test_missing_db_is_503(tmp_path, monkeypatch):
    monkeypatch.setenv("IDENTITY_MCP_DB", str(tmp_path / "nope.db"))
    assert TestClient(app).get("/users").status_code == 503


def test_agent_endpoint_streams_ndjson(client, monkeypatch):
    from identity_mcp.agent import session

    async def fake_events(question, history=()):
        assert history == [("hi", "hello")]
        yield {"type": "tool_call", "name": "query_identity", "question": question, "filter_from": None}
        yield {"type": "answer", "text": "done", "steps": 1, "elapsed_ms": 5}

    monkeypatch.setattr(session, "events", fake_events)
    r = client.post("/api/agent", json={"question": " who? ", "history": [{"question": "hi", "answer": "hello", "rows": [[1]]}]})
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/x-ndjson")
    lines = [__import__("json").loads(line) for line in r.text.splitlines()]
    assert [e["type"] for e in lines] == ["tool_call", "answer"] and lines[0]["question"] == "who?"


def test_agent_endpoint_validates_the_body(client):
    assert client.post("/api/agent", json={"question": ""}).status_code == 422
    assert client.post("/api/agent", json={"question": "x", "history": [{"question": "a", "answer": None}]}).status_code == 422
