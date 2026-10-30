import json
from unittest.mock import MagicMock

import pytest
from servers import gmail_server


def test_invalid_email_does_not_call_gmail(monkeypatch):
    mock_service = MagicMock()

    monkeypatch.setattr(
        gmail_server,
        "_get_service",
        mock_service,
    )

    with pytest.raises(ValueError):
        gmail_server.send_email(
            "invalid-email",
            "Test",
            "Hello",
        )

    mock_service.assert_not_called()


def test_valid_email_uses_mock_gmail(monkeypatch):
    mock_service = MagicMock()

    mock_service.users.return_value.messages.return_value.send.return_value.execute.return_value = {
        "id": "mock-message-123"
    }

    mock_service.users.return_value.getProfile.return_value.execute.return_value = {
        "emailAddress": "sender@example.com"
    }
    monkeypatch.setattr(
        gmail_server,
        "_get_service",
        lambda: mock_service,
    )
    result = json.loads(
        gmail_server.send_email(
            "alice@example.com",
            "Project Update",
            "Hello Alice",
        )
    )

    assert result["success"] is True
    assert result["message_id"] == "mock-message-123"
    assert result["to"] == "alice@example.com"

    mock_service.users.return_value.messages.return_value.send.assert_called_once()
