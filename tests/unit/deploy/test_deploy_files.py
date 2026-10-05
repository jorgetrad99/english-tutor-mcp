"""The compose stack keeps the three-role split; the wheel carries the package data (Task 27)."""

import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[3]
DEPLOY = ROOT / "deploy"


def _compose() -> dict[str, dict[str, object]]:
    doc = yaml.safe_load((DEPLOY / "compose.prod.yml").read_text(encoding="utf-8"))
    services: dict[str, dict[str, object]] = doc["services"]
    return services


def test_env_files_are_split_by_role() -> None:
    services = _compose()
    assert services["app"]["env_file"] == "tutor.env"
    assert services["migrate"]["env_file"] == "migrate.env"
    assert services["db"]["env_file"] == "db.env"


def test_app_waits_for_migrate_and_publishes_nothing() -> None:
    services = _compose()
    assert services["app"]["depends_on"] == {
        "migrate": {"condition": "service_completed_successfully"}
    }
    assert all("ports" not in svc for svc in services.values())
    assert services["migrate"]["restart"] == "no"


def test_hardening_flags() -> None:
    for name in ("migrate", "app", "cloudflared"):
        svc = _compose()[name]
        assert svc["cap_drop"] == ["ALL"]
        assert svc["read_only"] is True
        assert svc["security_opt"] == ["no-new-privileges:true"]


def test_dockerfile_is_non_root_without_dev_dependencies() -> None:
    text = (DEPLOY / "Dockerfile").read_text(encoding="utf-8")
    assert "USER 10001:10001" in text
    assert "--no-dev" in text
    assert "--group" not in text
    assert "chown -R tutor:tutor /data" in text
    assert "/.well-known/oauth-authorization-server" in text  # healthcheck on the MCP side


def test_wheel_contains_locale_templates_static_and_track(tmp_path: Path) -> None:
    uv = shutil.which("uv")
    assert uv, "uv must be on PATH (tests run through `uv run`)"
    subprocess.run(  # noqa: S603 - fixed arguments
        [uv, "build", "--wheel", "--out-dir", str(tmp_path)],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    names = zipfile.ZipFile(next(tmp_path.glob("tutor-*.whl"))).namelist()
    assert any(
        n.startswith("tutor/web/locale/") and n.endswith("/LC_MESSAGES/messages.po") for n in names
    )
    assert "tutor/web/templates/base.html" in names
    assert "tutor/web/templates/pages/login.html" in names
    assert any(n.startswith("tutor/web/static/") for n in names)
    assert "tutor/content/track_it_v0.yaml" in names
