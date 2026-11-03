"""Write deploy/dev.env for the dev compose profile from deploy/dev.env.example.

Run: uv run python scripts/dev_env.py [--force] [--target PATH]   (uv run just dev does it)
Every <KEY> placeholder in the example becomes a fresh random value: the signing key, the Fernet
key and the two database role passwords; DATABASE_URL takes the same password as APP_DB_PASSWORD.
The file is git-ignored, written with mode 0600 and never overwritten without --force (the
passwords are already in the database roles; after --force, migrate-dev re-applies them on the
next `just dev`). Nothing secret is printed.
"""

from __future__ import annotations

import argparse
import os
import re
import secrets
from collections.abc import Callable, Sequence
from pathlib import Path

from cryptography.fernet import Fernet

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "deploy" / "dev.env.example"
TARGET = ROOT / "deploy" / "dev.env"
PLACEHOLDER = re.compile(r"<([A-Z][A-Z0-9_]*)>")
GENERATORS: dict[str, Callable[[], str]] = {
    "TUTOR_JWT_SIGNING_KEY": lambda: secrets.token_urlsafe(48),
    "TUTOR_OAUTH_STORAGE_KEY": lambda: Fernet.generate_key().decode(),
    "TUTOR_FRAME_TOKEN_KEY": lambda: secrets.token_urlsafe(48),
    "APP_DB_PASSWORD": lambda: secrets.token_hex(24),
    "REPORT_DB_PASSWORD": lambda: secrets.token_hex(24),
}


def render(example: str) -> str:
    """The example with every <KEY> placeholder in a value replaced by a generated value."""
    values = {key: make() for key, make in GENERATORS.items()}

    def fill(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in values:
            raise ValueError(f"no generator for the placeholder <{name}>")
        return values[name]

    lines: list[str] = []
    for line in example.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            lines.append(line)
            continue
        key, sep, value = line.partition("=")
        if not sep:
            raise ValueError(f"not KEY=VALUE in the example: {line}")
        lines.append(f"{key}={PLACEHOLDER.sub(fill, value)}")
    return "\n".join(lines) + "\n"


def generate(target: Path = TARGET, example: Path = EXAMPLE, *, force: bool = False) -> bool:
    """Write `target` from `example`; False (and nothing written) when it exists and not force."""
    if target.exists() and not force:
        return False
    content = render(example.read_text(encoding="utf-8"))
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(content)
    target.chmod(0o600)  # no-op on Windows
    return True


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python scripts/dev_env.py", description=__doc__)
    parser.add_argument("--force", action="store_true", help="regenerate every secret")
    parser.add_argument(
        "--target", default=str(TARGET), help="file to write (default deploy/dev.env)"
    )
    args = parser.parse_args(argv)
    target = Path(args.target)
    if generate(target, force=args.force):
        print(f"{target} written (random secrets, git-ignored)")
    else:
        print(f"{target} exists; keep it, or pass --force to regenerate every secret")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
