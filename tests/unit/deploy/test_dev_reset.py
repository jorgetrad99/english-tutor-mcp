"""`uv run just dev-reset` removes this compose project's pgdata volume, nothing else (design D9).

The volume name comes from compose itself (scripts/dev_pgdata.py runs `docker compose config
--format json`), never from a hard-coded project name, so running it from a worktree or a renamed
checkout can never delete the main checkout's database. The CONFIRM=1 gate stays in the justfile.
"""

import importlib.util
import io
import json
import re
import subprocess
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[3]
MARKER = "do-not-print-this-value"  # stands in for an inlined env file value


def _script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("dev_pgdata", ROOT / "scripts" / "dev_pgdata.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _config(project: str = "agent-abc", **volume: Any) -> dict[str, Any]:
    # What compose prints, including an inlined env file when a profile is active.
    return {
        "name": project,
        "services": {"app-dev": {"environment": {"MIGRATION_DATABASE_URL": MARKER}}},
        "volumes": {"pgdata": {"name": f"{project}_pgdata", **volume}},
    }


class FakeDocker:
    """Records every command; `docker compose config` answers with the given JSON."""

    def __init__(self, config: object, config_status: int = 0) -> None:
        self.stdout = json.dumps(config) if not isinstance(config, str) else config
        self.config_status = config_status
        self.calls: list[list[str]] = []

    def __call__(self, args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        self.calls.append(list(args))
        if args[1:3] == ["compose", "config"]:
            assert kwargs.get("cwd") == ROOT  # where dev-reset's `down` runs (justfile dir)
            assert kwargs.get("capture_output") is True
            return subprocess.CompletedProcess(args, self.config_status, self.stdout, "")
        return subprocess.CompletedProcess(args, 0, "", "")


def _run(
    argv: list[str], fake: FakeDocker, environ: dict[str, str] | None = None
) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    code = _script().main(argv, run=fake, docker="docker", out=out, err=err, environ=environ or {})
    return code, out.getvalue(), err.getvalue()


@pytest.mark.parametrize("project", ["english-tutor-mcp", "agent-a664ea1e0400c9fbd", "tutor_x"])
def test_the_volume_is_this_projects_pgdata(project: str) -> None:
    assert _script().pgdata_volume(_config(project)) == f"{project}_pgdata"


def test_print_mode_runs_only_compose_config_and_prints_only_the_name() -> None:
    fake = FakeDocker(_config("agent-abc"))
    code, out, err = _run([], fake)
    assert (code, out, err) == (0, "agent-abc_pgdata\n", "")
    assert fake.calls == [["docker", "compose", "config", "--format", "json"]]


def test_remove_mode_removes_exactly_that_one_volume() -> None:
    fake = FakeDocker(_config("agent-abc"))
    code, out, _ = _run(["--remove"], fake)
    assert code == 0
    assert fake.calls == [
        ["docker", "compose", "config", "--format", "json"],
        ["docker", "volume", "rm", "agent-abc_pgdata"],
    ]
    assert MARKER not in out


@pytest.mark.parametrize(
    ("config", "reason"),
    [
        (_config(external=True), "external"),
        ({**_config(), "volumes": {"pgdata": {"name": "english-tutor-mcp_pgdata"}}}, "refusing"),
        ({**_config(), "volumes": {"oauth": {"name": "agent-abc_oauth"}}}, "no pgdata"),
        ({**_config(), "volumes": {}}, "no pgdata"),
        ({**_config(), "name": ""}, "project name"),
        ([], "JSON object"),
        ("not json", "no JSON"),
    ],
)
def test_anything_unexpected_removes_nothing(config: object, reason: str) -> None:
    fake = FakeDocker(config)
    code, out, err = _run(["--remove"], fake)
    assert code == 2
    assert reason in err
    assert out == "" and MARKER not in err
    assert all(call[1] != "volume" for call in fake.calls)


def test_a_failing_compose_config_removes_nothing_and_echoes_none_of_its_output() -> None:
    fake = FakeDocker(f"error near {MARKER}", config_status=1)
    code, out, err = _run(["--remove"], fake)
    assert code == 2 and "compose config failed" in err
    assert MARKER not in out + err
    assert len(fake.calls) == 1


@pytest.mark.parametrize("value", ["english-tutor-mcp", "agent-abc", " x "])
def test_remove_refuses_while_compose_project_name_is_set_and_removes_nothing(value: str) -> None:
    # A COMPOSE_PROJECT_NAME left in the shell would make a worktree's compose resolve to the main
    # checkout's project, so dev-reset there would delete the main checkout's pgdata.
    fake = FakeDocker(_config(value.strip()))
    code, out, err = _run(["--remove"], fake, {"COMPOSE_PROJECT_NAME": value})
    assert code == 2
    assert "COMPOSE_PROJECT_NAME" in err and "unset" in err
    assert out == ""
    assert all(call[1] != "volume" for call in fake.calls)


@pytest.mark.parametrize("value", ["", "   "])
def test_a_blank_compose_project_name_does_not_block_remove(value: str) -> None:
    fake = FakeDocker(_config("agent-abc"))
    code, _, _ = _run(["--remove"], fake, {"COMPOSE_PROJECT_NAME": value})
    assert code == 0
    assert fake.calls[-1] == ["docker", "volume", "rm", "agent-abc_pgdata"]


def test_print_mode_still_shows_the_name_with_compose_project_name_set() -> None:
    fake = FakeDocker(_config("english-tutor-mcp"))
    code, out, _ = _run([], fake, {"COMPOSE_PROJECT_NAME": "english-tutor-mcp"})
    assert (code, out) == (0, "english-tutor-mcp_pgdata\n")


def test_main_reads_the_process_environment_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COMPOSE_PROJECT_NAME", "english-tutor-mcp")
    fake = FakeDocker(_config("english-tutor-mcp"))
    out, err = io.StringIO(), io.StringIO()
    assert _script().main(["--remove"], run=fake, docker="docker", out=out, err=err) == 2
    assert all(call[1] != "volume" for call in fake.calls)


def _recipe(name: str) -> list[str]:
    text = (ROOT / "justfile").read_text(encoding="utf-8")
    match = re.search(rf"(?m)^{re.escape(name)}:\n((?:    .*\n)+)", text)
    assert match, name
    return [line.strip() for line in match.group(1).splitlines()]


def test_dev_reset_keeps_the_confirm_gate_and_derives_the_volume_from_compose() -> None:
    down, remove = _recipe("dev-reset")
    assert down.startswith(
        '{{ if confirm == "1" { "docker compose --profile dev down --remove-orphans" }'
    )
    assert remove == (
        '{{ if confirm == "1" { "uv run python scripts/dev_pgdata.py --remove" } '
        'else { "exit 1" } }}'
    )
    for line in (down, remove):
        assert "volume rm" not in line and "--volumes" not in line and " -v" not in line
    # Nothing in the justfile names a project or a volume by hand any more.
    text = (ROOT / "justfile").read_text(encoding="utf-8")
    assert "english-tutor-mcp" not in text and "compose_project" not in text
    assert not re.search(r"[\w.-]+_pgdata(?!\.py)", text)  # no <project>_pgdata literal
    assert 'confirm := env("CONFIRM", "")' in text
    # The refusal line must run cleanly in both PowerShell and sh: no ; ( ) , ' in its message.
    message = down.split('else { "', 1)[1].rsplit('" } }}', 1)[0]
    assert message.startswith("echo ") and not set(message) & set(";(),'")
