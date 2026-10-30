"""Run all release checks, reporting failures without skipping later checks."""

import subprocess
import sys
from pathlib import Path


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    checks = [
        ("Ruff", ["-m", "ruff", "check", "--isolated", "--select",
                  "E4,E7,E9,F,I,BLE,DTZ,RUF013,S110", "email_mcp_app", "scripts"]),
        ("Tests", ["-m", "pytest", "email_mcp_app/tests", "-v"]),
        ("SAST (Bandit)", ["-m", "bandit", "-r", "email_mcp_app", "-x", "email_mcp_app/tests"]),
        ("Secrets", ["scripts/check_secrets.py"]),
        ("Dependencies", ["-m", "pip_audit"]),
        ("Dependency consistency", ["-m", "pip", "check"]),
    ]
    failed = []
    for label, args in checks:
        print(f"\nRunning {label}", flush=True)
        if subprocess.run([sys.executable, *args], cwd=root, check=False).returncode:
            failed.append(label)
    print("\nFailed: " + ", ".join(failed) if failed else "\nAll checks passed.")
    return int(bool(failed))


if __name__ == "__main__":
    raise SystemExit(main())
