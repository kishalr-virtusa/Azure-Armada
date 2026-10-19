"""Customer payments on the live map: the SQL reader must never break or slow the map."""
import threading
from datetime import datetime
from decimal import Decimal
from unittest import mock

from dr_agent import service


def _reset(monkeypatch):
    monkeypatch.setattr(service, "_pay_cache", {"t": 0.0, "v": [], "busy": False})


def test_reader_returns_empty_list_when_sql_connect_raises(monkeypatch):
    def boom():
        raise RuntimeError("SQL_PASSWORD not set")
    monkeypatch.setattr(service.runner, "_sql_connect", boom)
    assert service.read_payments() == []


def test_reader_returns_empty_list_when_the_connection_fails(monkeypatch):
    def connect():
        raise OSError("login timeout: failover in progress")
    monkeypatch.setattr(service.runner, "_sql_connect", lambda: connect)
    assert service.read_payments(5) == []


def test_reader_maps_rows_and_uses_a_parameterized_query(monkeypatch):
    cur = mock.MagicMock()
    cur.fetchall.return_value = [(8, Decimal("42.10"), datetime(2026, 10, 19, 8, 0, 5), "Southeast Asia"),
                                 (7, Decimal("15.00"), datetime(2026, 10, 19, 8, 0, 1), "East Asia")]
    conn = mock.MagicMock()
    conn.__enter__.return_value = conn
    conn.cursor.return_value = cur
    monkeypatch.setattr(service.runner, "_sql_connect", lambda: (lambda: conn))
    rows = service.read_payments(3)
    sql, params = cur.execute.call_args.args
    assert params == (3,) and "id > %s" in sql and "region <> 'probe'" in sql and "amount > 0" in sql
    assert rows[0] == {"id": 8, "amount": 42.1, "created_at": "2026-10-19T08:00:05+00:00",
                       "region": "Southeast Asia", "served": "B"}
    assert rows[1]["served"] == "A"


def test_live_map_payload_carries_payments(monkeypatch):
    monkeypatch.setattr(service, "get_status", lambda max_age_s=5.0: {})
    monkeypatch.setattr(service.runner, "_probe", lambda r: {"ok": True})
    monkeypatch.setattr(service.runner, "tm_resolve", lambda: {"dns_region": "A"})
    monkeypatch.setattr(service, "list_exercises", lambda: [])
    pays = [{"id": 9, "amount": 1.5, "created_at": None, "region": "East Asia", "served": "A"}]
    monkeypatch.setattr(service, "recent_payments", lambda: pays)
    d = service._compute_live_map()
    assert d["payments"] == pays and d["last_payment_id"] == 9

    def broken():
        raise RuntimeError("boom")
    monkeypatch.setattr(service, "recent_payments", broken)
    d = service._compute_live_map()
    assert d["payments"] == [] and d["last_payment_id"] is None


def test_recent_payments_never_blocks_and_tolerates_errors(monkeypatch):
    _reset(monkeypatch)
    started, release = threading.Event(), threading.Event()

    def slow():
        started.set()
        release.wait(5)
        raise TimeoutError("hung login")
    monkeypatch.setattr(service.runner, "_sql_connect", slow)
    assert service.recent_payments() == []          # returns at once while the read is in flight
    assert started.wait(2)
    assert service.recent_payments() == []          # still no second read started
    release.set()
    for _ in range(50):
        if not service._pay_cache["busy"]:
            break
        threading.Event().wait(0.02)
    assert service._pay_cache["busy"] is False and service._pay_cache["v"] == []
