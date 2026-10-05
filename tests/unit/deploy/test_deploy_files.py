"""The compose stack keeps the three-role split; the wheel carries the package data (Task 27)."""

import shutil
import subprocess
import zipfile
from ipaddress import ip_address, ip_network
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
    assert services["app"]["pull_policy"] == "never"
    assert services["migrate"]["pull_policy"] == "build"


def _nets(name: str) -> set[str]:
    nets = _compose()[name]["networks"]
    return set(nets)  # type: ignore[call-overload]  # list or mapping of names


def test_network_split_isolates_the_database() -> None:
    doc = yaml.safe_load((DEPLOY / "compose.prod.yml").read_text(encoding="utf-8"))
    assert doc["networks"]["backend"]["internal"] is True
    assert not doc["networks"]["edge"].get("internal")
    assert _nets("db") == {"backend"}
    assert _nets("migrate") == {"backend"}
    assert _nets("app") == {"backend", "edge"}
    assert _nets("cloudflared") == {"edge"}
    assert not _nets("db") & _nets("cloudflared")


def test_forwarded_allow_ips_is_cloudflareds_fixed_edge_address() -> None:
    doc = yaml.safe_load((DEPLOY / "compose.prod.yml").read_text(encoding="utf-8"))
    fixed = ip_address(doc["services"]["cloudflared"]["networks"]["edge"]["ipv4_address"])
    ipam = doc["networks"]["edge"]["ipam"]["config"][0]
    assert fixed in ip_network(ipam["subnet"])
    assert fixed not in ip_network(ipam["ip_range"])  # dynamic addresses can never take it
    assert ip_network(ipam["ip_range"]).subnet_of(ip_network(ipam["subnet"]))  # type: ignore[arg-type]
    allowed = dict(
        line.split("=", 1)
        for line in (DEPLOY / "tutor.env.example").read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
    )["FORWARDED_ALLOW_IPS"]
    assert allowed == str(fixed)


def test_images_are_pinned_by_digest() -> None:
    for name in ("db", "cloudflared"):
        assert "@sha256:" in str(_compose()[name]["image"])
    docker = (DEPLOY / "Dockerfile").read_text(encoding="utf-8")
    assert docker.count("@sha256:") >= 2
    assert "@sha256:" in (DEPLOY / "backup.sh").read_text(encoding="utf-8")


def test_db_drops_capabilities_to_what_postgres_needs() -> None:
    db = _compose()["db"]
    assert db["cap_drop"] == ["ALL"]
    assert set(db["cap_add"]) == {"CHOWN", "DAC_OVERRIDE", "FOWNER", "SETGID", "SETUID"}  # type: ignore[call-overload]


def test_hardening_flags() -> None:
    for name in ("migrate", "app", "cloudflared"):  # db needs a writable root, see its caps
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


@pytest.mark.integration
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


@pytest.mark.parametrize("script", ["backup.sh", "check_backup.sh"])
def test_host_scripts_are_executable_in_git(script: str) -> None:
    # core.filemode is off on Windows, so the index mode is the only reliable record.
    git = shutil.which("git")
    assert git, "git must be on PATH"
    out = subprocess.run(  # noqa: S603 - fixed arguments
        [git, "ls-files", "-s", f"deploy/{script}"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert out.startswith("100755 "), out


def test_local_compose_publishes_only_the_app_on_loopback() -> None:
    doc = yaml.safe_load((DEPLOY / "compose.local.yml").read_text(encoding="utf-8"))
    assert doc["name"] == "tutor-local"  # cannot collide with the dev or production projects
    services = doc["services"]
    assert services["app"]["ports"] == ["127.0.0.1:8000:8000"]
    assert "ports" not in services["db"]
    assert "ports" not in services["migrate"]
    assert "cloudflared" not in services
    assert services["app"]["env_file"] == "local-docker.env"
    assert services["migrate"]["env_file"] == "local-migrate.env"
    assert services["db"]["env_file"] == "local-db.env"
    assert "@sha256:" in services["db"]["image"]
    gateway = ip_address(doc["networks"]["edge"]["ipam"]["config"][0]["gateway"])
    allowed = dict(
        line.split("=", 1)
        for line in (DEPLOY / "local-docker.env.example").read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
    )["FORWARDED_ALLOW_IPS"]
    assert allowed == str(gateway)
