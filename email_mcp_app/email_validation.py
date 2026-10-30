from email.utils import parseaddr


def validate_email_address(address):
    if not isinstance(address, str):
        raise ValueError("Email address must be a string")

    address = address.strip()

    display_name, parsed_address = parseaddr(address)

    if (
        not address
        or display_name
        or parsed_address != address
        or address.count("@") != 1
    ):
        raise ValueError("Invalid email address")

    local, domain = address.rsplit("@", 1)

    if (
        not local
        or not domain
        or "." not in domain
        or domain.startswith(".")
        or domain.endswith(".")
        or any(char.isspace() for char in address)
        or "\r" in address
        or "\n" in address
    ):
        raise ValueError("Invalid email address")

    return address


def validate_email_request(to, subject, body):
    recipient = validate_email_address(to)

    if not isinstance(subject, str) or not subject.strip():
        raise ValueError("Email subject is required")

    if "\r" in subject or "\n" in subject:
        raise ValueError("Email subject contains invalid characters")

    if not isinstance(body, str) or not body.strip():
        raise ValueError("Email body is required")

    return {
        "to": recipient,
        "subject": subject.strip(),
        "body": body,
    }
