"""The root compose `dev` profile (dev-environment design D6, D7, D10).

`db` and `db-test` keep no profile and publish on loopback only (superuser tutor:tutor); the dev
services carry the profile, bind to loopback, run the `dev` Dockerfile target from the bind-mounted
./src, and are the only place TUTOR_DEV_AUTH is set. app-dev never holds the owner URL or the
report password. No production file (compose or env example) carries the flag or the dev target.
"""

import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[3]
DEPLOY = ROOT / "deploy"
DEV_SERVICES = ("migrate-dev", "app-dev")
# In deploy/dev.env for migrate-dev (migrations, provision_roles); blanked for the running app.
MIGRATE_ONLY_CREDENTIALS = ("MIGRATION_DATABASE_URL", "REPORT_DB_PASSWORD")
PROD_COMPOSE = ("compose.prod.yml", "compose.coolify.yml", "compose.local.yml")
PROD_EXAMPLES = ("tutor", "local-docker", "local", "db", "migrate", "tunnel")


def _root_compose() -> dict[str, object]:
    doc: dict[str, object] = yaml.safe_load(
        (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    )
    return doc


def _services() -> dict[str, dict[str, object]]:
    services: dict[str, dict[str, object]] = _root_compose()["services"]  # type: ignore[assignment]
    return services


def _env(service: str) -> dict[str, str]:
    env = _services()[service].get("environment", {})
    assert isinstance(env, dict), f"{service}: environment must be a mapping"
    return {str(k): str(v) for k, v in env.items()}


def _example_keys(name: str) -> set[str]:
    keys = set()
    for line in (DEPLOY / f"{name}.env.example").read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            keys.add(stripped.partition("=")[0])
    return keys


def _dockerfile_stage(name: str) -> str:
    text = (DEPLOY / "Dockerfile").read_text(encoding="utf-8")
    stages = re.split(r"(?m)^FROM ", text)[1:]
    [stage] = [s for s in stages if re.match(rf"\S+ AS {name}\s", s)]
    return stage


def test_databases_keep_no_profile_and_publish_their_ports_on_loopback() -> None:
    # Superuser tutor:tutor: never reachable from the LAN (host-side URLs use localhost).
    services = _services()
    for name in ("db", "db-test"):
        assert "profiles" not in services[name], name
    assert services["db"]["ports"] == ["127.0.0.1:5432:5432"]
    assert services["db-test"]["ports"] == ["127.0.0.1:5433:5432"]
    assert services["db"]["volumes"] == ["pgdata:/var/lib/postgresql/data"]
    assert _root_compose()["volumes"] == {"pgdata": None}


def test_dev_services_are_the_only_ones_under_the_dev_profile() -> None:
    services = _services()
    assert set(services) == {"db", "db-test", *DEV_SERVICES}
    for name in DEV_SERVICES:
        assert services[name]["profiles"] == ["dev"], name


def test_the_flag_is_set_only_by_services_under_the_dev_profile() -> None:
    for name, svc in _services().items():
        env = _env(name)
        if "TUTOR_DEV_AUTH" in env:
            assert svc["profiles"] == ["dev"], name
    assert _env("migrate-dev")["TUTOR_DEV_AUTH"] == "1"
    assert _env("app-dev")["TUTOR_DEV_AUTH"] == "1"
    assert "TUTOR_DEV_AUTH" not in _example_keys("dev")  # the file alone enables nothing


def test_published_ports_bind_to_loopback_only() -> None:
    services = _services()
    assert services["app-dev"]["ports"] == ["127.0.0.1:8000:8000"]
    assert "ports" not in services["migrate-dev"]
    for name, svc in services.items():
        ports = svc.get("ports", [])
        assert isinstance(ports, list)
        for port in ports:
            assert str(port).startswith("127.0.0.1:"), (name, port)


def test_dev_services_run_the_dev_target_from_the_bind_mounted_source() -> None:
    services = _services()
    migrate = services["migrate-dev"]
    assert migrate["build"] == {"context": ".", "dockerfile": "deploy/Dockerfile", "target": "dev"}
    assert migrate["image"] == "tutor:dev" and migrate["pull_policy"] == "build"
    assert migrate["restart"] == "no"
    assert migrate["depends_on"] == {"db": {"condition": "service_healthy"}}
    entrypoint = migrate["entrypoint"]
    assert isinstance(entrypoint, list)
    script = entrypoint[-1]
    steps = [step.strip() for step in script.split("&&")]
    assert steps == [
        "alembic upgrade head",
        "python -m tutor.ops.provision_roles",
        "python -m tutor.ops.dev_seed",
    ]
    mounts = migrate["volumes"]
    assert isinstance(mounts, list)
    assert set(mounts) == {"./src:/src/src", "./alembic:/src/alembic"}

    app = services["app-dev"]
    assert "build" not in app  # the image migrate-dev built
    assert app["image"] == "tutor:dev" and app["pull_policy"] == "never"
    assert app["depends_on"] == {"migrate-dev": {"condition": "service_completed_successfully"}}
    assert app["volumes"] == ["./src:/src/src"]
    assert "command" not in app and "entrypoint" not in app  # the dev target's uvicorn CMD


def test_dev_env_feeds_the_app_services_only_and_is_git_ignored() -> None:
    services = _services()
    assert services["migrate-dev"]["env_file"] == "deploy/dev.env"
    assert services["app-dev"]["env_file"] == "deploy/dev.env"
    for name in ("db", "db-test"):
        assert "env_file" not in services[name]
    git = shutil.which("git")
    assert git, "git must be on PATH"
    ignored = subprocess.run(  # noqa: S603 - fixed arguments
        [git, "check-ignore", "-q", "deploy/dev.env"], cwd=ROOT, check=False
    )
    assert ignored.returncode == 0, "deploy/dev.env must be git-ignored"
    assert "deploy/dev.env" in (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert not (DEPLOY / "dev.env").exists() or True  # may exist locally; never tracked
    tracked = subprocess.run(  # noqa: S603 - fixed arguments
        [git, "ls-files", "--error-unmatch", "deploy/dev.env"],
        cwd=ROOT,
        check=False,
        capture_output=True,
    )
    assert tracked.returncode != 0


def test_app_dev_never_holds_the_owner_url_or_the_report_password() -> None:
    # One generated file feeds both dev services; compose `environment` overrides `env_file`, so
    # the running app sees these keys blank (Settings treats blank as unset) and migrate-dev keeps
    # the values from the file.
    dev_keys = _example_keys("dev")
    app, migrate = _env("app-dev"), _env("migrate-dev")
    for key in MIGRATE_ONLY_CREDENTIALS:
        assert key in dev_keys, key
        assert app.get(key) == "", key
        assert key not in migrate, key


def test_the_inspector_is_not_a_compose_service() -> None:
    # Plan ruling 12: the Inspector (v2) refuses to bind a non-loopback host inside a container
    # without an override flag, so it runs on host Node (`uv run just inspector-dev`) against
    # http://localhost:8000/mcp instead; no third-party container ever joins the dev network.
    assert "inspector" not in _services()
    text = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert "DANGEROUSLY_" not in text


def test_dockerfile_dev_target_reloads_the_editable_source_as_non_root() -> None:
    dev = _dockerfile_stage("dev")
    assert dev.startswith("build AS dev")
    assert "uv sync --locked\n" in dev and "--no-dev" not in dev  # the dev group, editable
    assert "USER 10001:10001" in dev
    assert "WATCHFILES_FORCE_POLLING=true" in dev  # bind mounts on Docker Desktop
    assert "PYTHONDONTWRITEBYTECODE=1" in dev
    assert '"uvicorn", "--factory", "tutor.dev:create", "--reload"' in dev
    assert '"--reload-dir", "/src/src"' in dev
    assert '"--host", "0.0.0.0", "--port", "8000"' in dev
    assert '"--no-access-log"' in dev
    assert "HEALTHCHECK" in dev and "'/mcp'" in dev  # no discovery route under dev auth
    runtime = _dockerfile_stage("runtime")
    assert 'ENTRYPOINT ["python", "-m", "tutor"]' in runtime
    assert "tutor.dev" not in runtime and "--reload" not in runtime


def test_runtime_is_the_last_dockerfile_stage() -> None:
    # A build without --target (docker build, or a compose build block without `target:`) builds
    # the last stage, so the product image must be last and the dev stage above it.
    text = (DEPLOY / "Dockerfile").read_text(encoding="utf-8")
    froms = [line for line in text.splitlines() if line.startswith("FROM ")]
    assert re.fullmatch(r"FROM \S+ AS runtime", froms[-1]), froms[-1]
    names = [line.rsplit(" AS ", 1)[-1] for line in froms]
    assert names.index("dev") < names.index("runtime")


@pytest.mark.parametrize("name", PROD_COMPOSE)
def test_prod_compose_files_build_the_runtime_target_and_never_carry_the_flag(name: str) -> None:
    text = (DEPLOY / name).read_text(encoding="utf-8")
    assert "TUTOR_DEV_AUTH" not in text
    assert "target: dev" not in text and "tutor:dev" not in text
    services: dict[str, dict[str, object]] = yaml.safe_load(text)["services"]
    builds = [svc["build"] for svc in services.values() if "build" in svc]
    assert builds, f"{name}: no build block"
    for build in builds:
        assert isinstance(build, dict)
        assert build.get("target") == "runtime", (name, build)
    for svc in services.values():
        assert "profiles" not in svc


@pytest.mark.parametrize("name", PROD_EXAMPLES)
def test_prod_env_examples_never_list_the_flag(name: str) -> None:
    assert "TUTOR_DEV_AUTH" not in _example_keys(name)
