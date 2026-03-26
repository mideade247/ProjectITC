"""
PostgreSQL MCP Server
Exposes email logging/querying operations as MCP tools.
"""

import json
import os
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

load_dotenv(Path(__file__).parent.parent / ".env")

mcp = FastMCP("postgres-server")

DATABASE_URL = os.getenv("DATABASE_URL")


def _connect():
    import psycopg2
    import psycopg2.extras

    if not DATABASE_URL:
        raise ValueError("DATABASE_URL not set. Add it to your .env file.")
    conn = psycopg2.connect(DATABASE_URL)
    return conn


def _rows_to_list(cursor) -> list:
    """Convert cursor rows (RealDictRow) to plain dicts with serializable values."""
    rows = cursor.fetchall()
    result = []
    for row in rows:
        d = dict(row)
        for k, v in d.items():
            if isinstance(v, datetime):
                d[k] = v.isoformat()
        result.append(d)
    return result


@mcp.tool()
def log_email(
    direction: str,
    from_address: str,
    to_address: str,
    subject: str,
    gmail_message_id: str,
    body_preview: str = "",
    status: str = "sent",
) -> str:
    """
    Log an email event to PostgreSQL.
    direction: 'sent' or 'received'
    Returns JSON with the new row id.
    Silently ignores duplicate gmail_message_id (idempotent).
    """
    import psycopg2.extras

    conn = _connect()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cur.execute(
            """
            INSERT INTO email_logs
                (direction, from_address, to_address, subject, body_preview,
                 gmail_message_id, status, logged_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
            ON CONFLICT (gmail_message_id) DO NOTHING
            RETURNING id
            """,
            (direction, from_address, to_address, subject,
             body_preview[:500], gmail_message_id, status),
        )
        conn.commit()
        row = cur.fetchone()
        if row:
            return json.dumps({"success": True, "id": row["id"]})
        return json.dumps({"success": True, "id": None, "note": "already logged"})
    finally:
        cur.close()
        conn.close()


@mcp.tool()
def get_email_logs(
    limit: int = 20,
    direction: str = "",
    search: str = "",
) -> str:
    """
    Retrieve email logs from PostgreSQL.
    direction: filter by 'sent' or 'received' (leave empty for all)
    search: search substring in subject, from_address, or to_address
    Returns JSON array ordered by most recent first.
    """
    import psycopg2.extras

    conn = _connect()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        conditions = ["1=1"]
        params: list = []

        if direction:
            conditions.append("direction = %s")
            params.append(direction)

        if search:
            conditions.append(
                "(subject ILIKE %s OR from_address ILIKE %s OR to_address ILIKE %s)"
            )
            params += [f"%{search}%", f"%{search}%", f"%{search}%"]

        params.append(limit)
        cur.execute(
            f"SELECT * FROM email_logs WHERE {' AND '.join(conditions)} "
            f"ORDER BY logged_at DESC LIMIT %s",
            params,
        )
        return json.dumps(_rows_to_list(cur), indent=2)
    finally:
        cur.close()
        conn.close()


@mcp.tool()
def get_email_stats() -> str:
    """
    Return summary statistics: total sent, total received, and a per-day breakdown.
    """
    import psycopg2.extras

    conn = _connect()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cur.execute(
            "SELECT direction, COUNT(*) AS total FROM email_logs GROUP BY direction"
        )
        totals = {r["direction"]: r["total"] for r in _rows_to_list(cur)}

        cur.execute(
            """
            SELECT
                direction,
                DATE_TRUNC('day', logged_at)::date AS date,
                COUNT(*) AS count
            FROM email_logs
            GROUP BY direction, DATE_TRUNC('day', logged_at)
            ORDER BY date DESC
            LIMIT 30
            """
        )
        by_day = _rows_to_list(cur)

        return json.dumps({"totals": totals, "by_day": by_day}, indent=2)
    finally:
        cur.close()
        conn.close()


@mcp.tool()
def search_emails_in_db(keyword: str, limit: int = 10) -> str:
    """
    Full-text search across all logged emails (subject + body_preview).
    Returns JSON array of matching rows.
    """
    import psycopg2.extras

    conn = _connect()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cur.execute(
            """
            SELECT * FROM email_logs
            WHERE subject ILIKE %s OR body_preview ILIKE %s
               OR from_address ILIKE %s OR to_address ILIKE %s
            ORDER BY logged_at DESC
            LIMIT %s
            """,
            [f"%{keyword}%"] * 4 + [limit],
        )
        return json.dumps(_rows_to_list(cur), indent=2)
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    mcp.run()
