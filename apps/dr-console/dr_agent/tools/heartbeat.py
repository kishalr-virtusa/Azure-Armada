"""Heartbeat writer used to measure RPO honestly (requirements R6.1).

While an exercise runs, a background thread inserts one row every `interval` seconds through the SQL
failover-group listener and reports every attempt (acked or failed) to `on_result`. After a failover the
engine asks the new primary which heartbeats survived (`newest_present`) and computes RPO.
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone
from typing import Callable, Optional

import pymssql

log = logging.getLogger(__name__)

Connect = Callable[[], "pymssql.Connection"]
OnResult = Callable[[int, datetime, bool, Optional[str]], None]

DDL = """
IF OBJECT_ID('dbo.heartbeat') IS NULL
CREATE TABLE dbo.heartbeat (
    seq BIGINT NOT NULL PRIMARY KEY,
    client_ts DATETIME2(3) NOT NULL,
    written_via NVARCHAR(32) NULL
)"""


def utcnow() -> datetime:
    """Naive UTC with millisecond precision (matches DATETIME2(3))."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    return now.replace(microsecond=(now.microsecond // 1000) * 1000)


def sql_connector(server: str, database: str, user: str, password: str, timeout: int = 5) -> Connect:
    """Factory for short-lived connections to the failover-group listener (no pooling across failover)."""
    def _connect():
        return pymssql.connect(server=server, user=user, password=password, database=database,
                               login_timeout=timeout, timeout=timeout, autocommit=False)
    return _connect


def ensure_table(connect: Connect) -> None:
    with connect() as conn:
        cur = conn.cursor()
        cur.execute(DDL)
        conn.commit()


class HeartbeatWriter(threading.Thread):
    def __init__(self, connect: Connect, on_result: OnResult, interval: float = 2.0, label: str = "dr-engine"):
        super().__init__(name="heartbeat-writer", daemon=True)
        self._connect = connect
        self._on_result = on_result
        self._interval = interval
        self._label = label
        self._stop = threading.Event()
        # time-based seq: unique across runs without reading the table first
        self._seq = int(time.time() * 1000)

    def stop(self, timeout: float = 10) -> None:
        self._stop.set()
        self.join(timeout)

    def write_once(self) -> tuple[int, datetime, bool, Optional[str]]:
        self._seq += 1
        seq, ts = self._seq, utcnow()
        try:
            with self._connect() as conn:
                cur = conn.cursor()
                cur.execute("INSERT INTO dbo.heartbeat (seq, client_ts, written_via) VALUES (%d, %s, %s)",
                            (seq, ts, self._label))
                conn.commit()  # acked only once the commit returns
            return seq, ts, True, None
        except Exception as e:  # noqa: BLE001 - any failure is a non-acked write
            return seq, ts, False, f"{type(e).__name__}: {e}"[:300]

    def run(self) -> None:
        log.info("heartbeat writer started (interval=%ss)", self._interval)
        while not self._stop.is_set():
            started = time.monotonic()
            result = self.write_once()
            try:
                self._on_result(*result)
            except Exception:  # noqa: BLE001 - never kill the writer because of the sink
                log.exception("heartbeat on_result failed")
            self._stop.wait(max(0.0, self._interval - (time.monotonic() - started)))
        log.info("heartbeat writer stopped")


def newest_present(connect: Connect, before: datetime) -> tuple[Optional[datetime], set[int]]:
    """Newest heartbeat timestamp and the set of seqs present with client_ts <= `before` (last 30 min)."""
    with connect() as conn:
        cur = conn.cursor()
        cur.execute("SELECT seq, client_ts FROM dbo.heartbeat "
                    "WHERE client_ts <= %s AND client_ts >= DATEADD(minute, -30, %s)", (before, before))
        rows = cur.fetchall()
    if not rows:
        return None, set()
    return max(r[1] for r in rows), {int(r[0]) for r in rows}


def replication_lag_seconds(connect: Connect) -> Optional[int]:
    """Geo-replication lag reported by the current primary (None if not available)."""
    try:
        with connect() as conn:
            cur = conn.cursor()
            cur.execute("SELECT TOP 1 replication_lag_sec FROM sys.dm_geo_replication_link_status")
            row = cur.fetchone()
        return None if row is None or row[0] is None else int(row[0])
    except Exception:  # noqa: BLE001
        log.exception("could not read replication lag")
        return None
