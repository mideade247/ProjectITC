import os
import secrets
from datetime import UTC, datetime, timedelta
from unittest.mock import Mock

import jwt
import pytest
import web_app
from fastapi import HTTPException


@pytest.fixture(autouse=True)
def signing_key(monkeypatch):
    monkeypatch.setattr(web_app, "SECRET_KEY", "test-signing-key-" * 4)


def test_login_token_round_trip():
    assert web_app.decode_token(web_app.create_token("alice@example.com")) == "alice@example.com"


@pytest.mark.parametrize("claims", [
    {"sub": "alice@example.com"},
    {"exp": datetime.now(UTC) + timedelta(hours=1)},
    {"sub": "alice@example.com", "exp": datetime.now(UTC) - timedelta(seconds=1)},
])
def test_invalid_claims_rejected(claims):
    token = jwt.encode(claims, web_app.SECRET_KEY, algorithm="HS256")
    with pytest.raises(HTTPException) as error:
        web_app.decode_token(token)
    assert error.value.status_code == 401


def test_wrong_signature_rejected():
    token = web_app.create_token("alice@example.com")
    web_app.SECRET_KEY = secrets.token_urlsafe(48)
    with pytest.raises(HTTPException) as error:
        web_app.decode_token(token)
    assert error.value.status_code == 401


def test_gmail_token_file_private(monkeypatch, tmp_path):
    monkeypatch.setattr(web_app, "_token_path_for", lambda _: tmp_path / "token.json")
    monkeypatch.setattr(web_app, "db_get_user", lambda _: {"gmail_token": '{"token": "synthetic"}'})
    path = web_app.ensure_token_file("alice@example.com")
    assert path.read_text() == '{"token": "synthetic"}'
    if os.name != "nt":
        assert path.stat().st_mode & 0o777 == 0o600


def test_reset_link_not_logged(monkeypatch, capsys, caplog):
    store = Mock()
    send = Mock()
    monkeypatch.setattr(web_app, "db_get_user", lambda _: {"email": "alice@example.com"})
    monkeypatch.setattr(web_app, "db_set_reset_token", store)
    monkeypatch.setattr(web_app, "_send_reset_email", send)
    web_app.forgot_password(web_app.ForgotPasswordRequest(email="alice@example.com"))
    reset_token = store.call_args.args[1]
    assert reset_token in send.call_args.args[1]
    output = capsys.readouterr()
    assert reset_token not in output.out + output.err + caplog.text
