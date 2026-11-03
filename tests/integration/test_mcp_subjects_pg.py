"""The subjects and template tools through the real server on Postgres, as the login role, with
the caller identified by an access token in context (subjects spec 7.1, 15.9; Review Focus 5;
plan rulings 27b and 30)."""

import json
import threading
import time
import zoneinfo
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pytest
from fastmcp import Client
from mcp_lesson import Clock, call, random_sub, text_of
from sqlalchemy import Engine, text

from tutor.db.uow import PgIdentity, pg_uow_factory
from tutor.domain.html_guard import MAX_TEMPLATE_BYTES, TEMPLATE_MARKER
from tutor.domain.subjects import SubjectInput
from tutor.domain.templates import TEMPLATE_GUIDE_VERSION
from tutor.mcp import rules
from tutor.services.context import Services
from tutor.services.subjects import save_subject
from tutor.services.template_files import guide_examples

from .test_mcp_lesson_pg import server, signed_in

pytestmark = pytest.mark.integration

SUBJECT_ARGS = {
    "name": "Guitarra",
    "preference": "practice_first",
    "exercise_kinds": ["quiz", "drill", "exercise"],
    "minutes_per_session": 30,
    "sessions_per_week": 3,
}
NOTE = {
    "title": "Clase 3: el acorde de séptima",
    "lesson_ref": "Módulo 2 / Clase 3",
    "raw_text": "El acorde de séptima tiene cuatro notas.\n\nSe toca en el quinto traste.",
    "concepts": ["Acorde de séptima", "Quinto traste"],
    "links": [{"from": "Quinto traste", "to": "Acorde de séptima", "link_kind": "prerequisite"}],
}


def example(name: str) -> dict[str, Any]:
    found = next(e for e in guide_examples() if e.name == name)
    return {"html": found.html, **json.loads(json.dumps(dict(found.wire)))}


def template_args(subject_id: str, source: str = "numeric", **changes: Any) -> dict[str, Any]:
    data = example(source)
    return {
        "subject_id": subject_id,
        **data["spec"],
        "html": data["html"],
        "guide_version": TEMPLATE_GUIDE_VERSION,
        "learner_confirmed": True,
        **changes,
    }


def instance_args(name: str, template_id: str) -> dict[str, Any]:
    data = example(name)["instance"]
    return {
        "template_id": template_id,
        "params": data["params"],
        "prompt_text": data["prompt_text"],
    }


async def failure(client: Client, tool: str, payload: dict[str, Any]) -> tuple[str, list[str]]:
    result = await client.call_tool(tool, payload, raise_on_error=False)
    assert result.is_error, tool
    body = json.loads(text_of(result))
    return body["code"], body["fields"]


@pytest.mark.asyncio
async def test_subjects_tools_on_postgres_with_the_login_role(login_engine: Engine) -> None:
    mcp = server(login_engine, Clock())
    with signed_in(random_sub()):
        async with Client(mcp) as client:
            first = await call(client, "get_subjects", {})
            assert (first["subjects"], len(first["onboarding_questions"])) == ([], 7)
            subject = (await call(client, "save_subject", SUBJECT_ARGS))["subject"]
            subject_id = subject["subject_id"]
            listed = await call(client, "list_templates", {"subject_id": subject_id})
            assert listed["templates"] == [] and listed["guide_text"]
            numeric = await call(client, "save_template", template_args(subject_id))
            flash = await call(client, "save_template", template_args(subject_id, "pool_recall"))
            source = {"source_kind": "course", "title": "Curso de guitarra"}
            lesson = {
                **NOTE,
                "instances": [
                    instance_args("numeric", numeric["template_id"]),
                    instance_args("pool_recall", flash["template_id"]),
                ],
            }
            args = {"subject_id": subject_id, "source": source, "notes": [lesson]}
            saved = await call(client, "save_notes", args)
            again = await call(client, "save_notes", args)
            [new], [existing] = saved["notes"], again["notes"]
            assert (new["note_outcome"], existing["note_outcome"]) == ("new", "existing")
            assert existing["note_id"] == new["note_id"]
            assert existing["instance_ids"] == new["instance_ids"] and len(new["instance_ids"]) == 2
            note_id = new["note_id"]

            found = await call(client, "search_notes", {"query": "acordes de séptima"})
            assert [hit["note_id"] for hit in found["notes"]] == [note_id]
            assert found["notes"][0]["source_title"] == "Curso de guitarra"
            assert found["response_rules"] == rules.SEARCH_NOTES

            read = await call(client, "get_note", {"note_id": note_id, "passage_from": 1})
            assert read["concepts"] == ["Acorde de séptima", "Quinto traste"]
            assert read["passages"][0]["seq"] == 1
            assert [i["grading_kind"] for i in read["instances"]] == ["numeric", "exact_text"]
            assert "answer" not in read["instances"][0]["params"]
            view = await call(client, "get_instance_view", {"instance_id": new["instance_ids"][1]})
            assert view["document"].count('id="tutor-params"') == 1
            made = await call(
                client,
                "create_instance",
                {"subject_id": subject_id, **instance_args("numeric", numeric["template_id"])},
            )
            assert made["instance_outcome"] == "new"
            replaced = await call(
                client,
                "save_template",
                template_args(
                    subject_id, name="Numérica 2", replaces_template_id=numeric["template_id"]
                ),
            )
            assert replaced["template_outcome"] == "superseded"

    with signed_in(random_sub()):
        async with Client(mcp) as client:
            assert (await call(client, "get_subjects", {}))["subjects"] == []
            assert (await call(client, "search_notes", {"query": "acorde"}))["notes"] == []
            for tool, payload, field in (
                ("save_notes", {"subject_id": subject_id, "notes": [NOTE]}, "subject_id"),
                ("get_note", {"note_id": note_id}, "note_id"),
                ("list_templates", {"subject_id": subject_id}, "subject_id"),
                ("get_instance_view", {"instance_id": made["instance_id"]}, "instance_id"),
                ("save_template", template_args(subject_id), "subject_id"),
            ):
                assert await failure(client, tool, payload) == ("not_found", [field]), tool


def sized_html(size: int) -> str:
    """A template of exactly `size` UTF-8 bytes, mostly two-byte characters."""
    head, tail = "<!doctype html><html><body><p>", f"</p>{TEMPLATE_MARKER}</body></html>"
    room = size - len(head) - len(tail)
    return head + "é" * (room // 2) + "a" * (room % 2) + tail


@pytest.mark.asyncio
async def test_a_template_at_the_byte_cap_saves_and_one_byte_over_is_validation_failed(
    login_engine: Engine,
) -> None:
    """The domain cap equals the database CHECK: neither side is internal_error."""
    at_cap, over = sized_html(MAX_TEMPLATE_BYTES), sized_html(MAX_TEMPLATE_BYTES + 1)
    assert (len(at_cap.encode()), len(over.encode())) == (
        MAX_TEMPLATE_BYTES,
        MAX_TEMPLATE_BYTES + 1,
    )
    with signed_in(random_sub()):
        async with Client(server(login_engine, Clock())) as client:
            subject_id = (await call(client, "save_subject", SUBJECT_ARGS))["subject"]["subject_id"]
            saved = await call(client, "save_template", template_args(subject_id, html=at_cap))
            read = await call(
                client,
                "list_templates",
                {
                    "subject_id": subject_id,
                    "template_id": saved["template_id"],
                    "include_html": True,
                },
            )
            refused = await failure(
                client, "save_template", template_args(subject_id, name="Otra", html=over)
            )
            listed = await call(client, "list_templates", {"subject_id": subject_id})
    assert read["templates"][0]["html"] == at_cap
    assert refused == ("validation_failed", ["html"])
    assert [t["template_id"] for t in listed["templates"]] == [saved["template_id"]]


@pytest.mark.asyncio
async def test_instance_concepts_on_postgres(login_engine: Engine, engine: Engine) -> None:
    """Plan ruling 30: an unknown concept is not_found, a single note concept is the default,
    and no concept row is created by an instance."""
    with signed_in(random_sub()):
        async with Client(server(login_engine, Clock())) as client:
            subject_id = (await call(client, "save_subject", SUBJECT_ARGS))["subject"]["subject_id"]
            numeric = (await call(client, "save_template", template_args(subject_id)))[
                "template_id"
            ]
            args = instance_args("numeric", numeric)
            unknown = await failure(
                client, "create_instance", {"subject_id": subject_id, **args, "concept": "Nada"}
            )
            in_note = await failure(
                client,
                "save_notes",
                {
                    "subject_id": subject_id,
                    "notes": [
                        {
                            "title": "C",
                            "raw_text": "Texto.",
                            "concepts": ["Suma"],
                            "instances": [{**args, "concept": "Resta"}],
                        }
                    ],
                },
            )
            saved = await call(
                client,
                "save_notes",
                {
                    "subject_id": subject_id,
                    "notes": [
                        {
                            "title": "C",
                            "raw_text": "Texto.",
                            "concepts": ["Suma"],
                            "instances": [args],
                        }
                    ],
                },
            )
            note = await call(client, "get_note", {"note_id": saved["notes"][0]["note_id"]})
    assert unknown == ("not_found", ["concept"])
    assert in_note == ("not_found", ["notes[0].instances[0].concept"])
    assert [i["concept"] for i in note["instances"]] == ["Suma"]
    with engine.connect() as conn:
        names = conn.execute(
            text("SELECT name FROM concepts WHERE subject_id = :s"), {"s": subject_id}
        ).scalars()
        assert sorted(names) == ["Suma"]


def test_concurrent_subject_updates_keep_both_changes(login_engine: Engine) -> None:
    """Plan ruling 27b: the second save waits for the first one's row lock, then merges the
    optional answer it omits onto the first one's result instead of overwriting it."""
    svc = Services(
        uow=pg_uow_factory(login_engine),
        clock=lambda: datetime(2026, 10, 14, 15, tzinfo=UTC),
        valid_timezones=frozenset(zoneinfo.available_timezones()),
    )
    user_id = PgIdentity(login_engine).resolve(random_sub(), None, None, svc.clock()).id
    answers = SubjectInput(
        name="Guitarra",
        goal_text=None,
        context_text=None,
        preference="balanced",
        exercise_kinds=("quiz",),
        minutes_per_session=30,
        sessions_per_week=3,
        target_date=None,
        status=None,
    )
    subject_id: UUID = save_subject(svc, user_id, answers).subject.id
    errors: list[BaseException] = []

    def second() -> None:
        try:  # changes minutes and omits goal_text, which it must keep
            save_subject(svc, user_id, replace(answers, minutes_per_session=60), subject_id)
        except BaseException as exc:  # pragma: no cover - reported below
            errors.append(exc)

    worker = threading.Thread(target=second)
    with svc.uow(user_id) as uow:
        row = uow.subjects.get_for_update(subject_id)
        assert row is not None
        worker.start()
        time.sleep(0.5)
        assert worker.is_alive()  # waiting on the row lock
        uow.subjects.update(subject_id, replace(row.subject, goal_text="Tocar"), svc.clock())
    worker.join(timeout=10)
    assert not worker.is_alive() and errors == []
    with svc.uow(user_id) as uow:
        final = uow.subjects.get(subject_id)
    assert final is not None
    assert (final.subject.goal_text, final.subject.minutes_per_session) == ("Tocar", 60)
