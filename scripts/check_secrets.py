"""Scan tracked and new non-ignored files without printing secret values."""

import subprocess
from pathlib import Path

from detect_secrets import SecretsCollection
from detect_secrets.settings import default_settings


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    files = subprocess.check_output(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=root,
    ).decode().split("\0")
    secrets = SecretsCollection()
    with default_settings():
        for name in sorted(set(files) - {""}):
            path = root / name
            if path.is_file():
                secrets.scan_file(str(path))
    findings = secrets.json()
    for name, matches in findings.items():
        for match in matches:
            print(f"{Path(name).relative_to(root)}:{match['line_number']}: {match['type']}")
    print(f"Secret scan: {sum(map(len, findings.values()))} potential secrets")
    return int(bool(findings))


if __name__ == "__main__":
    raise SystemExit(main())
