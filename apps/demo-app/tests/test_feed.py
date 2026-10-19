"""Shared live activity feed (run from apps/demo-app: python -m pytest -q tests)."""
import os
import sys
from datetime import datetime
from unittest import mock

os.environ.setdefault("APP_ACCESS_CODE", "s3cret")
os.environ.setdefault("DR_LIVE_URL", "")
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

with mock.patch("app.db.init_tables"):
    from fastapi.testclient import TestClient
    from app import db, main

client = TestClient(main.app)
CODE = {"X-Access-Code": os.environ["APP_ACCESS_CODE"]}
ME, OTHER = "abcd-1234-efgh", "zzzz-9999-yyyy"

ROWS = [
    {"id": 12, "amount": 42.10, "created_at": datetime(2026, 10, 19, 8, 0, 5), "region": "Southeast Asia", "device": OTHER},
    {"id": 11, "amount": 15.00, "created_at": datetime(2026, 10, 19, 8, 0, 1), "region": "East Asia", "device": ME},
    {"id": 10, "amount": 99.99, "created_at": datetime(2026, 10, 19, 8, 0, 0), "region": "East Asia", "device": None},
]


def test_feed_is_behind_the_access_gate():
    with mock.patch("app.db.get_feed", return_value=ROWS):
        with mock.patch("app.access.is_public", return_value=False):
            assert TestClient(main.app).get("/api/feed").status_code == 401
        assert client.get("/api/feed", headers=CODE).status_code == 200


def test_feed_is_anonymous_stable_and_marks_own_payments():
    with mock.patch("app.db.get_feed", return_value=ROWS) as q:
        r = client.get("/api/feed?after=9", headers={**CODE, "X-Device": ME})
        assert q.call_args.args == (9, 20)
    body = r.json()
    assert ME not in r.text and OTHER not in r.text  # raw device ids never leave the server
    p = body["payments"]
    assert [x["id"] for x in p] == [12, 11, 10]
    assert p[0]["label"].startswith("Customer ") and len(p[0]["label"]) == len("Customer 3F2A") and not p[0]["mine"]
    assert p[1]["label"] == "You" and p[1]["mine"]
    assert p[2]["label"] == "A customer"
    assert p[0]["created_at"] == "2026-10-19T08:00:05+00:00" and p[0]["region"] == "Southeast Asia"
    assert "device" not in p[0] and body["server_time"]
    # same device -> same label on every call and for every viewer; different devices -> different labels
    with mock.patch("app.db.get_feed", return_value=ROWS):
        again = client.get("/api/feed", headers=CODE).json()["payments"]
    assert again[0]["label"] == p[0]["label"]
    assert again[1]["label"] == main._customer_label(ME) != p[0]["label"]


def test_feed_query_excludes_probe_rows_and_is_parameterized():
    cur = mock.MagicMock()
    cur.fetchall.return_value = []
    conn = mock.MagicMock()
    conn.cursor.return_value = cur
    with mock.patch("app.db.get_connection", return_value=conn):
        assert db.get_feed(7, 20) == []
    sql, params = cur.execute.call_args.args
    assert "amount > 0" in sql and "region <> 'probe'" in sql and "id > %s" in sql and "ORDER BY id DESC" in sql
    assert params == (20, 7)


def test_feed_rejects_bad_after_and_hides_db_errors():
    assert client.get("/api/feed?after=-1", headers=CODE).status_code == 422
    assert client.get("/api/feed?after=1;DROP", headers=CODE).status_code == 422
    with mock.patch("app.db.get_feed", side_effect=Exception("login failed for server xyz")):
        r = client.get("/api/feed", headers=CODE)
    assert r.status_code == 503 and "xyz" not in r.text
