import resona_api.profiles_store as store
from fastapi.testclient import TestClient


def _app(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "PROFILES_PATH", tmp_path)
    from fastapi import FastAPI
    from resona_api.profiles_routes import router
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_put_get_list_delete_profile(tmp_path, monkeypatch):
    client = _app(tmp_path, monkeypatch)
    body = {"name": "arzt", "description": "AB", "steps": []}

    assert client.put("/profiles/arzt", json=body).status_code == 200
    assert client.get("/profiles/arzt").json()["description"] == "AB"

    listing = client.get("/profiles").json()
    assert {"name": "arzt", "description": "AB"} in listing["profiles"]

    assert client.delete("/profiles/arzt").status_code == 200
    assert client.get("/profiles/arzt").status_code == 404


def test_put_invalid_profile_returns_400(tmp_path, monkeypatch):
    client = _app(tmp_path, monkeypatch)
    resp = client.put("/profiles/bad", json={"name": "bad",
        "steps": [{"type": "magic"}]})
    assert resp.status_code == 400
