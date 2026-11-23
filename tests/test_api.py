"""API tests: auth, tenant isolation, chat write/read path, clarifications, ontology review, privacy."""

import pytest
from fastapi.testclient import TestClient

from ontomem.backend.app import create_app


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    app = create_app(str(tmp_path_factory.mktemp("home")), seed_demo=True)
    with TestClient(app) as c:
        yield c


def login(client, username, password="secret123"):
    r = client.post("/api/auth/register", json={"username": username, "password": password})
    if r.status_code == 409:
        r = client.post("/api/auth/login", json={"username": username, "password": password})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def test_health_and_auth_required(client):
    assert client.get("/api/health").json()["status"] == "ok"
    assert client.get("/api/memory").status_code == 401
    assert client.get("/api/memory", headers={"Authorization": "Bearer nonsense"}).status_code == 401


def test_demo_account_is_seeded(client):
    r = client.post("/api/auth/login", json={"username": "asha", "password": "demo1234"})
    h = {"Authorization": f"Bearer {r.json()['token']}"}
    m = client.get("/api/memory", headers=h).json()
    assert m["stats"]["active"] > 8 and m["stats"]["retired"] >= 1
    labels = {(n["label"], n["polarity"]) for n in m["active"]}
    assert ("spicy food", "avoids") in labels and ("sushi", "likes") in labels


def test_chat_write_then_read(client):
    h = login(client, "alice")
    r = client.post("/api/chat", json={"text": "I love spicy food.", "ts": "2026-03-01T19:00:00"}, headers=h).json()
    assert r["trace"]["candidates"][0]["outcome"]["decision"] == "new"
    r = client.post("/api/chat", json={"text": "Spicy food doesn't agree with me any more.", "ts": "2026-06-01T19:00:00"}, headers=h).json()
    assert r["trace"]["candidates"][0]["outcome"]["decision"] == "supersede"
    r = client.post("/api/chat", json={"text": "Any dinner ideas?", "ts": "2026-06-02T19:00:00"}, headers=h).json()
    assert r["answer"]["recommendations"]
    assert all("Spicy" not in x["item"]["name"] for x in r["answer"]["recommendations"])
    tl = client.get("/api/memory/timeline", headers=h).json()
    assert [v["polarity"] for v in tl[0]["values"]] == ["likes", "avoids"]
    g = client.get("/api/memory/graph", headers=h).json()
    assert any(e["kind"] == "supersededBy" for e in g["edges"]) and any(n["kind"] == "concept" for n in g["nodes"])
    past = client.get("/api/memory", params={"as_of": "2026-04-01T00:00:00"}, headers=h).json()
    assert [n["polarity"] for n in past["active"]] == ["likes"]


def test_tenant_isolation(client):
    a, b = login(client, "bob"), login(client, "carol")
    client.post("/api/chat", json={"text": "I love jazz."}, headers=a)
    assert client.get("/api/memory", headers=b).json()["active"] == []
    node = client.get("/api/memory", headers=a).json()["active"][0]["id"]
    assert client.get(f"/api/memory/node/{node}", headers=b).status_code == 404
    q = "SELECT (COUNT(*) AS ?n) WHERE { ?p a <https://w3id.org/ontomem/pref#Preference> }"
    assert client.post("/api/sparql", json={"query": q}, headers=b).json()["rows"][0]["n"] == "0"


def test_clarification_flow(client):
    h = login(client, "dave")
    for t, ts in [("I avoid seafood.", "2026-01-01T10:00:00"), ("But I love sushi.", "2026-02-01T10:00:00"),
                  ("No seafood at all for me now.", "2026-03-01T10:00:00")]:
        client.post("/api/chat", json={"text": t, "ts": ts}, headers=h)
    qs = client.get("/api/clarifications", headers=h).json()
    assert qs and "sushi" in qs[0]["question"].lower()
    r = client.post(f"/api/clarifications/{qs[0]['node_id']}", json={"keep_exception": True}, headers=h).json()
    assert r["clarifications"] == []


def test_sparql_validate_and_export(client):
    h = login(client, "erin")
    client.post("/api/chat", json={"text": "I love sushi and ramen."}, headers=h)
    q = """PREFIX pref: <https://w3id.org/ontomem/pref#>
           SELECT ?label WHERE { ?p pref:status "active" ; pref:concept ?c . ?c <http://www.w3.org/2004/02/skos/core#prefLabel> ?label }"""
    rows = client.post("/api/sparql", json={"query": q}, headers=h).json()["rows"]
    assert {r["label"] for r in rows} == {"sushi", "ramen"}
    assert client.get("/api/validate/graph", headers=h).json()["conforms"] is True
    bad = client.post("/api/validate", json={"surface": "x", "polarity": "love", "who": "self", "aspect": "general",
                                             "intent": "standing", "context": "any", "strength": 2,
                                             "stated_at": "2026-01-01T00:00:00", "source_text": "x"}, headers=h).json()
    assert not bad["conforms"] and {v["field"] for v in bad["violations"]} >= {"polarity", "strength"}
    assert "Preference" in client.get("/api/me/export", params={"fmt": "turtle"}, headers=h).text


def test_ontology_review_and_rekey(client):
    h = login(client, "frank")
    client.post("/api/chat", json={"text": "I love bubble tea.", "ts": "2026-01-01T10:00:00"}, headers=h)
    props = client.get("/api/ontology/proposals").json()
    p = next(x for x in props if x["surface"] == "bubble tea")
    choice = next(i for i, o in enumerate(p["payload"]) if o["parent"] == "Tea")
    r = client.post(f"/api/ontology/proposals/{p['id']}", json={"approve": True, "choice": choice}, headers=h).json()
    assert r["status"] == "approved" and r["rekeyed"] >= 1
    act = client.get("/api/memory", headers=h).json()["active"]
    assert act[0]["key"]["what"] == "BubbleTea" and "Tea" in act[0]["path"]
    assert client.get("/api/ontology").json()["version"] != "1.0.0"


def test_compare_and_scenarios(client):
    h = login(client, "gina")
    sc = client.get("/api/scenarios").json()
    assert {s["id"] for s in sc} >= {"spicy", "sushi", "override", "paris", "mushrooms", "trap"}
    played = client.post("/api/scenarios/mushrooms/play", headers=h).json()
    assert played["memory"]["stats"]["third_party"] == 1
    r = client.post("/api/compare", json={"baseline": "S1", "query": "What should I cook?", "turns": [
        {"text": "I love mushrooms.", "ts": "2026-01-01T10:00:00"},
        {"text": "My wife hates mushrooms.", "ts": "2026-01-02T10:00:00"}]}, headers=h).json()
    last = r["steps"][-1]
    assert "likes mushrooms" in last["ours"]["active"] and "likes mushrooms" not in last["base"]["active"]


def test_erase(client):
    h = login(client, "hank")
    client.post("/api/chat", json={"text": "I love sushi."}, headers=h)
    assert client.delete("/api/me/data", headers=h).json()["erased_events"] == 1
    assert client.get("/api/memory", headers=h).json()["active"] == []


def test_mcp_tools_and_local_sdk(tmp_path, monkeypatch):
    monkeypatch.setenv("ONTOMEM_HOME", str(tmp_path))
    from ontomem.mcp_server.server import build_engine, tools
    from ontomem.sdk.client import OntoMemClient

    t = tools(build_engine())
    assert t["remember"]("I avoid seafood but I love sushi.")["stored"]
    r = t["recall"]("Any dinner ideas?")
    assert "avoids seafood" in r["memory_block"] and r["suggestions"]
    assert "proof" in t["explain"]("Any dinner ideas?")
    assert t["forget"]()["erased_events"] == 2
    local = OntoMemClient.local()
    local.say("I love jazz.")
    assert local.memory()[0]["key"]["what"] == "Genre_Jazz"


def test_mcp_server_registers_tools():
    import asyncio

    pytest.importorskip("mcp", reason="install the [mcp] extra")
    from ontomem.core.engine import OntoMemEngine
    from ontomem.mcp_server.server import create_server

    s = create_server(OntoMemEngine())
    names = {t.name for t in asyncio.run(s.list_tools())}
    assert names == {"remember", "recall", "explain", "clarify", "forget"}


def test_static_files_cannot_escape_the_web_app(tmp_path):
    """Regression: '/..%2f..%2fpyproject.toml' used to return files from outside frontend/dist."""
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>app</title>")
    (tmp_path / "secret.txt").write_text("TOP SECRET")
    import os

    os.environ["ONTOMEM_FRONTEND"] = str(dist)
    try:
        app = create_app(str(tmp_path / "home"), seed_demo=False)
    finally:
        del os.environ["ONTOMEM_FRONTEND"]
    with TestClient(app) as c:
        for url in ["/..%2fsecret.txt", "/..%2F..%2Fsecret.txt", "/%2e%2e/secret.txt", "/assets/..%2f..%2fsecret.txt"]:
            r = c.get(url)
            assert "TOP SECRET" not in r.text, url
        assert "<title>app</title>" in c.get("/memory").text  # client-side routes still get the app
        r = c.get("/api/does-not-exist")
        assert r.status_code == 404 and r.headers["content-type"].startswith("application/json")


def test_concurrent_chats_from_many_users(client):
    """Regression: a shared SQLite connection raised InterfaceError (500/401) under parallel requests."""
    import concurrent.futures as cf

    heads = [login(client, f"par{i}") for i in range(6)]
    texts = ["I love sushi.", "I hate horror movies.", "My son loves cricket.", "I avoid red meat."]

    def burst(h):
        return [client.post("/api/chat", json={"text": t}, headers=h).status_code for t in texts] + [
            client.get("/api/memory", headers=h).status_code]

    with cf.ThreadPoolExecutor(6) as ex:
        codes = [c for r in ex.map(burst, heads) for c in r]
    assert set(codes) == {200}
    assert [client.get("/api/memory", headers=h).json()["stats"]["active"] for h in heads] == [4] * 6


def test_markup_is_not_a_preference(client):
    h = login(client, "markup")
    r = client.post("/api/chat", json={"text": "<script>x</script> I love <b>pizza</b>"}, headers=h).json()
    assert [c["mapping"]["concept"] for c in r["trace"]["candidates"]] == ["Pizza"]


def test_failures_endpoint_rejects_paths(client):
    assert client.get("/api/eval/failures", params={"experiment": "../../etc"}).status_code == 400
