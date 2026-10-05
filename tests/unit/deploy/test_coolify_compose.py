"""The Coolify stack keeps compose.prod.yml's role split, hardening and forwarded-header trust."""

import re
from ipaddress import ip_address, ip_network
from pathlib import Path

import pytest
import yaml

from tutor.ops.provision_roles import KEYS as MIGRATE_KEYS

pytestmark = pytest.mark.unit

DEPLOY = Path(__file__).resolve().parents[3] / "deploy"
# ${NAME}, ${NAME:?}, ${NAME:-default}: group 2 is the modifier.
REF = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)([^}]*)\}")
# Docker's default address pools; Coolify's per-resource networks are allocated from them.
DOCKER_DEFAULT_POOLS = (ip_network("172.16.0.0/12"), ip_network("192.168.0.0/16"))


def _load(name: str) -> dict[str, object]:
    doc: dict[str, object] = yaml.safe_load((DEPLOY / name).read_text(encoding="utf-8"))
    return doc


def _services() -> dict[str, dict[str, object]]:
    services: dict[str, dict[str, object]] = _load("compose.coolify.yml")["services"]  # type: ignore[assignment]
    return services


def _env(service: str) -> dict[str, str]:
    env = _services()[service]["environment"]
    assert isinstance(env, dict), f"{service}: environment must be a mapping"
    return {str(k): str(v) for k, v in env.items()}


def _refs(service: str) -> set[str]:
    return {m.group(1) for value in _env(service).values() for m in REF.finditer(value)}


def _example_keys(name: str) -> set[str]:
    keys = set()
    for line in (DEPLOY / f"{name}.env.example").read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            keys.add(stripped.partition("=")[0])
    return keys


def _edge() -> dict[str, object]:
    networks: dict[str, dict[str, object]] = _load("compose.coolify.yml")["networks"]  # type: ignore[assignment]
    return networks["edge"]


def _cloudflared_address() -> str:
    nets: dict[str, dict[str, str]] = _services()["cloudflared"]["networks"]  # type: ignore[assignment]
    return nets["edge"]["ipv4_address"]


def test_no_env_files_and_nothing_published() -> None:
    # Coolify's checkout has no git-ignored deploy/*.env; values come from its UI.
    for name, svc in _services().items():
        assert "env_file" not in svc, name
        assert "ports" not in svc, name


def test_each_service_gets_exactly_the_keys_of_its_env_example() -> None:
    assert set(_env("db")) == _example_keys("db")
    assert set(_env("migrate")) == set(MIGRATE_KEYS) == _example_keys("migrate")
    assert set(_env("app")) == _example_keys("tutor")
    assert set(_env("cloudflared")) == _example_keys("tunnel")


def test_each_service_reads_only_its_own_secrets() -> None:
    owner = {"POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB"}
    assert _refs("db") == owner
    assert _refs("migrate") == owner | {"APP_DB_USER", "APP_DB_PASSWORD", "REPORT_DB_PASSWORD"}
    assert _refs("app") == {
        "APP_DB_USER",
        "APP_DB_PASSWORD",
        "POSTGRES_DB",
        "TUTOR_BASE_URL",
        "TUTOR_SUPPORT_EMAIL",
        "GOOGLE_CLIENT_ID",
        "GOOGLE_CLIENT_SECRET",
        "TUTOR_JWT_SIGNING_KEY",
        "TUTOR_OAUTH_STORAGE_KEY",
    }
    assert _refs("cloudflared") == {"TUNNEL_TOKEN"}


def test_every_variable_is_required() -> None:
    # ${NAME:?} stops the deploy when Coolify has no value, instead of starting with "".
    for name in _services():
        for value in _env(name).values():
            for m in REF.finditer(value):
                assert m.group(2) == ":?", f"{name}: {m.group(0)} must be ${{{m.group(1)}:?}}"


def test_app_fixed_settings_are_not_editable_in_coolify() -> None:
    env = _env("app")
    assert env["TUTOR_ENV"] == "prod"
    assert env["TUTOR_MCP_URL"] == "${TUTOR_BASE_URL:?}/mcp"
    assert env["TUTOR_OAUTH_STORAGE_DIR"] == "/data/oauth"
    assert env["TUTOR_TEST_LOGIN"] == ""
    assert env["FORWARDED_ALLOW_IPS"] == _cloudflared_address()
    assert (
        env["DATABASE_URL"]
        == "postgresql://${APP_DB_USER:?}:${APP_DB_PASSWORD:?}@db:5432/${POSTGRES_DB:?}"
    )


def test_migrate_owner_url_is_built_from_the_db_values() -> None:
    url = _env("migrate")["MIGRATION_DATABASE_URL"]
    assert url == "postgresql://${POSTGRES_USER:?}:${POSTGRES_PASSWORD:?}@db:5432/${POSTGRES_DB:?}"


def test_edge_subnet_is_outside_dockers_default_pools() -> None:
    ipam = _edge()["ipam"]["config"][0]  # type: ignore[index]
    subnet = ip_network(ipam["subnet"])
    fixed = ip_address(_cloudflared_address())
    assert fixed in subnet
    assert fixed not in ip_network(ipam["ip_range"])  # dynamic addresses can never take it
    assert ip_network(ipam["ip_range"]).subnet_of(subnet)  # type: ignore[arg-type]
    assert not any(subnet.overlaps(pool) for pool in DOCKER_DEFAULT_POOLS)


def test_tunnel_reaches_the_app_only_through_an_edge_alias() -> None:
    # If Coolify adds its own network, `app` resolves on two networks and the request could arrive
    # from an address FORWARDED_ALLOW_IPS does not trust. The alias exists on edge only.
    nets: dict[str, dict[str, list[str]] | None] = _services()["app"]["networks"]  # type: ignore[assignment]
    assert set(nets) == {"backend", "edge"}
    assert nets["edge"] == {"aliases": ["tutor-edge-app"]}
    assert not (nets["backend"] or {}).get("aliases")
    assert "tutor-edge-app" not in _services()


def test_network_split_isolates_the_database() -> None:
    networks: dict[str, dict[str, object]] = _load("compose.coolify.yml")["networks"]  # type: ignore[assignment]
    assert networks["backend"]["internal"] is True
    assert not networks["edge"].get("internal")
    assert set(_services()["db"]["networks"]) == {"backend"}  # type: ignore[call-overload]
    assert set(_services()["migrate"]["networks"]) == {"backend"}  # type: ignore[call-overload]
    assert set(_services()["cloudflared"]["networks"]) == {"edge"}  # type: ignore[call-overload]


def test_startup_order_and_one_shot_migrate() -> None:
    services = _services()
    migrate = services["migrate"]
    assert migrate["entrypoint"] == ["tutor-migrate"]
    assert migrate["restart"] == "no"
    assert migrate["exclude_from_hc"] is True  # an exited one-shot is not an unhealthy stack
    assert migrate["depends_on"] == {"db": {"condition": "service_healthy"}}
    assert services["app"]["depends_on"] == {
        "migrate": {"condition": "service_completed_successfully"}
    }
    assert services["cloudflared"]["depends_on"] == {"app": {"condition": "service_healthy"}}


def test_app_and_migrate_build_from_the_checkout() -> None:
    # No shared `image:` name: each builds from the same Dockerfile (cached), nothing is pulled.
    for name in ("migrate", "app"):
        svc = _services()[name]
        assert svc["build"] == {"context": "..", "dockerfile": "deploy/Dockerfile"}
        assert "image" not in svc
        assert "pull_policy" not in svc


def test_third_party_images_match_compose_prod() -> None:
    prod: dict[str, dict[str, object]] = _load("compose.prod.yml")["services"]  # type: ignore[assignment]
    for name in ("db", "cloudflared"):
        assert _services()[name]["image"] == prod[name]["image"]  # digests are bumped together


def test_hardening_matches_compose_prod() -> None:
    prod: dict[str, dict[str, object]] = _load("compose.prod.yml")["services"]  # type: ignore[assignment]
    keys = ("cap_drop", "cap_add", "read_only", "security_opt", "tmpfs", "restart", "command")
    for name, svc in _services().items():
        for key in keys:
            assert svc.get(key) == prod[name].get(key), f"{name}.{key}"


def test_volumes() -> None:
    assert _services()["db"]["volumes"] == ["pgdata:/var/lib/postgresql/data"]
    assert _services()["app"]["volumes"] == ["oauth:/data/oauth"]
    assert set(_load("compose.coolify.yml")["volumes"]) == {"pgdata", "oauth"}  # type: ignore[call-overload]
