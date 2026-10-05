"""CI deploys only version tags from main, after the checks pass (docs/v0/coolify.md, Releases)."""

from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[3]


def _workflow() -> dict[str, object]:
    doc: dict[str, object] = yaml.safe_load(
        (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    )
    return doc


def _jobs() -> dict[str, dict[str, object]]:
    jobs: dict[str, dict[str, object]] = _workflow()["jobs"]  # type: ignore[assignment]
    return jobs


def _runs(job: str) -> list[str]:
    steps: list[dict[str, str]] = _jobs()[job]["steps"]  # type: ignore[assignment]
    return [step["run"] for step in steps if "run" in step]


def test_deploy_runs_only_for_version_tags_after_check() -> None:
    deploy = _jobs()["deploy"]
    assert deploy["needs"] == "check"
    assert deploy["if"] == "startsWith(github.ref, 'refs/tags/v')"
    assert deploy["environment"] == "production"
    assert deploy["concurrency"] == {"group": "production", "cancel-in-progress": False}


def test_only_the_deploy_job_can_write() -> None:
    assert _workflow()["permissions"] == {"contents": "read"}
    assert "permissions" not in _jobs()["check"]
    assert _jobs()["deploy"]["permissions"] == {"contents": "write"}


def test_deploy_checks_main_then_moves_production_then_deploys_then_smokes() -> None:
    runs = _runs("deploy")
    order = [
        next(i for i, run in enumerate(runs) if needle in run)
        for needle in (
            'git merge-base --is-ancestor "$GITHUB_SHA" origin/main',
            'git push --force origin "$GITHUB_SHA:refs/heads/production"',
            "sh deploy/coolify_deploy.sh",
            "/.well-known/oauth-authorization-server",
        )
    ]
    assert order == sorted(order)


def test_the_token_is_a_secret_and_nothing_else_is() -> None:
    env: dict[str, str] = _jobs()["deploy"]["env"]  # type: ignore[assignment]
    assert env == {
        "COOLIFY_URL": "${{ vars.COOLIFY_URL }}",
        "COOLIFY_APP_UUID": "${{ vars.COOLIFY_APP_UUID }}",
        "COOLIFY_TOKEN": "${{ secrets.COOLIFY_TOKEN }}",
        "TUTOR_BASE_URL": "${{ vars.TUTOR_BASE_URL }}",
    }
