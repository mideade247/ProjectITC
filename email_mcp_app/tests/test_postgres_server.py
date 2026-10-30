import json
import sqlite3
from unittest.mock import MagicMock

import pytest
from servers import postgres_server


@pytest.mark.parametrize("direction, search, expected", [
    ("", "", [2, 1]), ("sent", "", [1]), ("", "INVOICE", [1]),
    ("received", "' OR 1=1 --", []), ("received", "invoice", []),
])
def test_log_filters(monkeypatch, direction, search, expected):
    database = sqlite3.connect(":memory:")
    database.row_factory = sqlite3.Row
    database.execute("""CREATE TABLE email_logs (
        id INTEGER, direction TEXT, subject TEXT, from_address TEXT,
        to_address TEXT, logged_at INTEGER)""")
    database.executemany("INSERT INTO email_logs VALUES (?, ?, ?, ?, ?, ?)", [
        (1, "sent", "Invoice", "alice@example.com", "bob@example.com", 1),
        (2, "received", "Meeting", "bob@example.com", "alice@example.com", 2),
    ])
    real_cursor = database.cursor()
    connection = MagicMock()
    cursor = connection.cursor.return_value
    # SQLite exercises filter behavior; live PostgreSQL integration is separate.
    cursor.execute.side_effect = lambda query, params: real_cursor.execute(
        query.replace("%s", "?").replace("ILIKE", "LIKE"), params,
    )
    cursor.fetchall.side_effect = real_cursor.fetchall
    monkeypatch.setattr(postgres_server, "_connect", lambda: connection)
    try:
        rows = json.loads(postgres_server.get_email_logs(10, direction, search))
        assert [row["id"] for row in rows] == expected
    finally:
        database.close()
    cursor.close.assert_called_once()
    connection.close.assert_called_once()
