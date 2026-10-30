import pytest
from email_validation import (
    validate_email_address,
    validate_email_request,
)


def test_valid_email_address():
    result = validate_email_address("alice@example.com")

    assert result == "alice@example.com"


@pytest.mark.parametrize(
    "address",
    [
        "",
        "invalid-email",
        "alice@",
        "@example.com",
        "alice example@example.com",
        "alice@example.com\nBcc: other@example.com",
    ],
)
def test_invalid_email_address(address):
    with pytest.raises(ValueError):
        validate_email_address(address)


def test_valid_email_request():
    result = validate_email_request(
        "alice@example.com",
        "Project Update",
        "The project is progressing.",
    )

    assert result["to"] == "alice@example.com"
    assert result["subject"] == "Project Update"


def test_missing_subject():
    with pytest.raises(ValueError, match="subject"):
        validate_email_request(
            "alice@example.com",
            "",
            "Hello",
        )


def test_missing_body():
    with pytest.raises(ValueError, match="body"):
        validate_email_request(
            "alice@example.com",
            "Hello",
            "",
        )


def test_subject_header_injection():
    with pytest.raises(ValueError, match="invalid characters"):
        validate_email_request(
            "alice@example.com",
            "Hello\nBcc: other@example.com",
            "Test message",
        )


def test_non_string_email_address_raises_type_error():
    with pytest.raises(TypeError):
        validate_email_address(None)
