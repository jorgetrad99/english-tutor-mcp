"""The dev database volume of THIS compose project, for `uv run just dev-reset` (design D9).

Run: uv run python scripts/dev_pgdata.py            prints the volume name (read only)
     uv run python scripts/dev_pgdata.py --remove   removes that one volume (dev-reset, CONFIRM=1)
The name comes from compose itself: `docker compose config --format json`, run from the repo root
as dev-reset's `down` is, resolves the project name (directory, COMPOSE_PROJECT_NAME or `name:`)
and the `pgdata` volume's name, so a worktree or a renamed checkout names its own volume, never the
main checkout's. Only `pgdata` is ever removed (db-test is tmpfs). The config output can inline
deploy/dev.env when a profile is active, so it is parsed in memory and never printed.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, TextIO

ROOT = Path(__file__).resolve().parents[1]
VOLUME_KEY = "pgdata"

Runner = Callable[..., "subprocess.CompletedProcess[str]"]


class ComposeError(Exception):
    """The compose config does not name exactly this project's pgdata volume."""


def pgdata_volume(config: Mapping[str, Any]) -> str:
    """`<project>_pgdata`, only when compose resolves the volume to exactly that name."""
    project = config.get("name")
    if not isinstance(project, str) or not project:
        raise ComposeError("compose config has no project name")
    volumes = config.get("volumes")
    spec = volumes.get(VOLUME_KEY) if isinstance(volumes, Mapping) else None
    if not isinstance(spec, Mapping):
        raise ComposeError(f"compose config declares no {VOLUME_KEY} volume")
    if spec.get("external"):
        raise ComposeError(f"{VOLUME_KEY} is external: not this project's to delete")
    expected = f"{project}_{VOLUME_KEY}"
    if spec.get("name") != expected:
        raise ComposeError(f"{VOLUME_KEY} does not resolve to {expected}: refusing")
    return expected


def compose_config(docker: str, run: Runner) -> Mapping[str, Any]:
    result = run(
        [docker, "compose", "config", "--format", "json"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:  # its stderr can quote the env file: never echoed
        raise ComposeError("docker compose config failed; run it yourself to see why")
    try:
        config = json.loads(result.stdout)
    except json.JSONDecodeError:
        raise ComposeError("docker compose config printed no JSON") from None
    if not isinstance(config, Mapping):
        raise ComposeError("docker compose config printed no JSON object")
    return config


def main(
    argv: Sequence[str] | None = None,
    *,
    run: Runner = subprocess.run,
    docker: str | None = None,
    out: TextIO = sys.stdout,
    err: TextIO = sys.stderr,
) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument(
        "--remove", action="store_true", help="remove the volume (dev-reset, after CONFIRM=1)"
    )
    args = parser.parse_args(argv)
    docker = docker or shutil.which("docker")
    if docker is None:
        print("dev_pgdata: docker is not on PATH", file=err)
        return 2
    try:
        volume = pgdata_volume(compose_config(docker, run))
    except ComposeError as exc:
        print(f"dev_pgdata: {exc}", file=err)
        return 2
    if not args.remove:
        print(volume, file=out)
        return 0
    print(f"dev_pgdata: removing volume {volume}", file=out)
    removed: subprocess.CompletedProcess[str] = run([docker, "volume", "rm", volume], check=False)
    return removed.returncode


if __name__ == "__main__":
    sys.exit(main())
