"""Database connection and operations via SQL Failover Group."""

import os
import logging
from contextlib import contextmanager
from typing import Any

import pymssql

logger = logging.getLogger(__name__)

# Connection settings from environment
SQL_SERVER = os.getenv("SQL_SERVER", "")
SQL_DB = os.getenv("SQL_DB", "paymentsdb")
SQL_USER = os.getenv("SQL_USER", "sqladmin")
SQL_PASSWORD = os.getenv("SQL_PASSWORD", "")


def get_connection():
    """Get a new database connection via the FOG listener."""
    return pymssql.connect(
        server=SQL_SERVER,
        user=SQL_USER,
        password=SQL_PASSWORD,
        database=SQL_DB,
        login_timeout=5,
        timeout=5,
    )


@contextmanager
def get_cursor():
    """Context manager for database cursor with auto-commit."""
    conn = get_connection()
    try:
        cursor = conn.cursor(as_dict=True)
        yield cursor
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_tables():
    """Create tables if they don't exist."""
    with get_cursor() as cursor:
        # Transactions table
        cursor.execute("""
            IF NOT EXISTS (SELECT * FROM sysobjects WHERE name='transactions' AND xtype='U')
            CREATE TABLE transactions (
                id INT IDENTITY(1,1) PRIMARY KEY,
                amount DECIMAL(10,2) NOT NULL,
                created_at DATETIME2 DEFAULT SYSUTCDATETIME(),
                region NVARCHAR(32) NOT NULL
            )
        """)
        
        # Per-device ledger: the balance a phone shows is computed here, so it survives a failover
        cursor.execute("""
            IF COL_LENGTH('transactions', 'device') IS NULL
            ALTER TABLE transactions ADD device NVARCHAR(64) NULL
        """)

        # Heartbeat table (written by DR engine)
        cursor.execute("""
            IF NOT EXISTS (SELECT * FROM sysobjects WHERE name='heartbeat' AND xtype='U')
            CREATE TABLE heartbeat (
                seq BIGINT PRIMARY KEY,
                client_ts DATETIME2 NOT NULL,
                written_via NVARCHAR(32)
            )
        """)
    logger.info("Database tables initialized")


def health_check() -> dict[str, Any]:
    """Perform health check: read + write test."""
    try:
        with get_cursor() as cursor:
            # Read test
            cursor.execute("SELECT 1 AS ok")
            cursor.fetchone()
            
            # Write test (insert + delete in transaction)
            cursor.execute("""
                BEGIN TRANSACTION
                DECLARE @probe_id INT
                INSERT INTO transactions (amount, region) VALUES (0.00, 'probe')
                SET @probe_id = SCOPE_IDENTITY()
                DELETE FROM transactions WHERE id = @probe_id
                COMMIT
            """)
            
            # Get server name
            cursor.execute("SELECT @@SERVERNAME AS server_name")
            row = cursor.fetchone()
            server_name = row["server_name"] if row else "unknown"
            
        return {"db_ok": True, "db_server": server_name}
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        return {"db_ok": False, "db_server": None, "error": str(e)}


def insert_transaction(amount: float, region: str, device: str | None = None) -> dict[str, Any]:
    """Insert a new transaction and return it."""
    with get_cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO transactions (amount, region, device)
            OUTPUT INSERTED.id, INSERTED.amount, INSERTED.created_at, INSERTED.region
            VALUES (%s, %s, %s)
            """,
            (amount, region, device),
        )
        row = cursor.fetchone()
        return {
            "id": row["id"],
            "amount": float(row["amount"]),
            "created_at": row["created_at"].isoformat() if row["created_at"] else None,
            "region": row["region"],
        }


def get_recent_transactions(limit: int = 10, device: str | None = None) -> list[dict[str, Any]]:
    """Get the most recent transactions (only this device's when a device id is given)."""
    with get_cursor() as cursor:
        cursor.execute(
            f"""
            SELECT TOP(%s) id, amount, created_at, region
            FROM transactions
            WHERE amount > 0 {"AND device = %s" if device else ""}
            ORDER BY created_at DESC
            """,
            (limit, device) if device else (limit,),
        )
        rows = cursor.fetchall()
        return [
            {
                "id": r["id"],
                "amount": float(r["amount"]),
                "created_at": r["created_at"].isoformat() if r["created_at"] else None,
                "region": r["region"],
            }
            for r in rows
        ]


def device_spent(device: str) -> tuple[float, int]:
    """Total paid and number of payments made by one device."""
    with get_cursor() as cursor:
        cursor.execute(
            "SELECT COALESCE(SUM(amount), 0) AS spent, COUNT(*) AS n FROM transactions WHERE device = %s AND amount > 0",
            (device,),
        )
        row = cursor.fetchone()
        return float(row["spent"]), int(row["n"])


def get_feed(after: int = 0, limit: int = 20) -> list[dict[str, Any]]:
    """Newest payments from all phones with id > `after` (health probes excluded). Rows carry the raw device
    id so the caller can anonymize it; it must never be sent to a browser as is."""
    with get_cursor() as cursor:
        cursor.execute(
            """
            SELECT TOP(%s) id, amount, created_at, region, device
            FROM transactions
            WHERE amount > 0 AND region <> 'probe' AND id > %s
            ORDER BY id DESC
            """,
            (limit, after),
        )
        return [
            {
                "id": r["id"],
                "amount": float(r["amount"]),
                "created_at": r["created_at"],
                "region": r["region"],
                "device": r["device"],
            }
            for r in cursor.fetchall()
        ]
