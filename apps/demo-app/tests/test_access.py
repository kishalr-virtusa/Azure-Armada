"""Private/public access gate (run from apps/demo-app: python -m pytest -q tests)."""
import os
import sys
from unittest import mock

os.environ["APP_ACCESS_CODE"] = "s3cret"
os.environ["DR_LIVE_URL"] = ""
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

with mock.patch("app.db.init_tables"):
    from fastapi.testclient import TestClient
    from app import access, main

client = TestClient(main.app)


def test_private_by_default_and_api_blocked():
    r = client.get("/")
    assert r.status_code == 401 and "private preview" in r.text
    assert client.get("/api/recent").status_code == 401


def test_form_wrong_and_right_code():
    assert client.post("/access", data={"code": "nope", "next": "/"}).status_code == 401
    r = client.post("/access", data={"code": "s3cret", "next": "/"}, follow_redirects=False)
    assert r.status_code == 303


def test_code_link_sets_iframe_friendly_cookie():
    r = client.get("/?code=s3cret", follow_redirects=False)
    cookie = r.headers["set-cookie"]
    assert r.status_code == 303 and "HttpOnly" in cookie and "Secure" in cookie
    assert "SameSite=None" in cookie and "Partitioned" in cookie


def test_cookie_header_and_public_mode_grant_access():
    with mock.patch("app.db.health_check", return_value={"db_ok": True}):
        c = TestClient(main.app)
        c.cookies.set(access.COOKIE, access._token())
        assert c.get("/api/config").status_code == 200
        assert TestClient(main.app).get("/api/config", headers={"X-Access-Code": "s3cret"}).status_code == 200
        with mock.patch.object(access, "is_public", return_value=True):
            assert TestClient(main.app).get("/api/config").status_code == 200


def test_reconnect_token_grants_access_on_other_region():
    r = client.get("/?k=" + access._token(), follow_redirects=False)
    assert r.status_code == 303 and "contoso_access=" in r.headers["set-cookie"]
    assert client.get("/?k=wrong", follow_redirects=False).status_code == 401
