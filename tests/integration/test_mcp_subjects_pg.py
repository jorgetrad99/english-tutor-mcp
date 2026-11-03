"""The seven subjects tools through the real server on Postgres, as the login role, with the
caller identified by an access token in context (subjects spec 7.1; Review Focus 5)."""

import json

import pytest
from fastmcp import Client
from mcp_lesson import Clock, call, random_sub, text_of
from sqlalchemy import Engine

from tutor.domain.html_guard import MAX_ARTIFACT_BYTES
from tutor.mcp import rules

from .test_mcp_lesson_pg import server, signed_in

pytestmark = pytest.mark.integration

SUBJECT_ARGS = {
    "name": "Guitarra",
    "preference": "practice_first",
    "exercise_kinds": ["quiz", "exercise"],
    "minutes_per_session": 30,
    "sessions_per_week": 3,
}
NOTE = {
    "title": "Clase 3: el acorde de séptima",
    "lesson_ref": "Módulo 2 / Clase 3",
    "raw_text": "El acorde de séptima tiene cuatro notas.\n\nSe toca en el quinto traste.",
    "concepts": ["Acorde de séptima", "Quinto traste"],
    "links": [{"from": "Quinto traste", "to": "Acorde de séptima", "link_kind": "prerequisite"}],
    "quiz_items": [
        {"question": "¿Cuántas notas?", "options": ["Tres", "Cuatro"], "answer_index": 1}
    ],
}
HTML = (
    "<!doctype html><html><head><style>p { color: red }</style></head>"
    '<body><p>Canción</p><a href="#top">top</a><script>let a = 1;</script></body></html>'
)


@pytest.mark.asyncio
async def test_subjects_tools_on_postgres_with_the_login_role(login_engine: Engine) -> None:
    mcp = server(login_engine, Clock())
    with signed_in(random_sub()):
        async with Client(mcp) as client:
            first = await call(client, "get_subjects", {})
            assert (first["subjects"], len(first["onboarding_questions"])) == ([], 7)
            subject = (await call(client, "save_subject", SUBJECT_ARGS))["subject"]
            subject_id = subject["subject_id"]
            source = {"source_kind": "course", "title": "Curso de guitarra"}
            args = {"subject_id": subject_id, "source": source, "notes": [NOTE]}
            saved = await call(client, "save_notes", args)
            again = await call(client, "save_notes", args)
            [new], [existing] = saved["notes"], again["notes"]
            assert (new["note_outcome"], existing["note_outcome"]) == ("new", "existing")
            assert existing["note_id"] == new["note_id"]
            note_id = new["note_id"]

            found = await call(client, "search_notes", {"query": "acordes de séptima"})
            assert [hit["note_id"] for hit in found["notes"]] == [note_id]
            assert found["notes"][0]["source_title"] == "Curso de guitarra"
            assert found["response_rules"] == rules.SEARCH_NOTES

            artifact = await call(
                client,
                "save_artifact",
                {
                    "note_id": note_id,
                    "artifact_kind": "lesson_view",
                    "title": "Vista",
                    "html": HTML,
                },
            )
            fetched = await call(client, "get_artifact", {"artifact_id": artifact["artifact_id"]})
            assert fetched["html"] == HTML
            read = await call(client, "get_note", {"note_id": note_id, "passage_from": 1})
            assert read["concepts"] == ["Acorde de séptima", "Quinto traste"]
            assert read["passages"][0]["seq"] == 1
            assert "answer_index" not in read["quiz_items"][0]
            assert read["artifacts"][0]["artifact_id"] == artifact["artifact_id"]

    with signed_in(random_sub()):
        async with Client(mcp) as client:
            assert (await call(client, "get_subjects", {}))["subjects"] == []
            assert (await call(client, "search_notes", {"query": "acorde"}))["notes"] == []
            for tool, payload, field in (
                ("save_notes", {"subject_id": subject_id, "notes": [NOTE]}, "subject_id"),
                ("get_note", {"note_id": note_id}, "note_id"),
                ("get_artifact", {"artifact_id": artifact["artifact_id"]}, "artifact_id"),
                (
                    "save_artifact",
                    {"note_id": note_id, "artifact_kind": "other", "title": "x", "html": HTML},
                    "note_id",
                ),
            ):
                result = await client.call_tool(tool, payload, raise_on_error=False)
                assert result.is_error, tool
                body = json.loads(text_of(result))
                assert (body["code"], body["fields"]) == ("not_found", [field]), tool


def sized_html(size: int) -> str:
    """A complete document of exactly `size` UTF-8 bytes, mostly two-byte characters."""
    head, tail = "<!doctype html><html><body><p>", "</p></body></html>"
    room = size - len(head) - len(tail)
    return head + "é" * (room // 2) + "a" * (room % 2) + tail


@pytest.mark.asyncio
async def test_an_artifact_at_the_byte_cap_saves_and_one_byte_over_is_validation_failed(
    login_engine: Engine,
) -> None:
    """The domain cap sits under the database CHECK: neither side is internal_error."""
    at_cap, over = sized_html(MAX_ARTIFACT_BYTES), sized_html(MAX_ARTIFACT_BYTES + 1)
    assert (len(at_cap.encode()), len(over.encode())) == (
        MAX_ARTIFACT_BYTES,
        MAX_ARTIFACT_BYTES + 1,
    )
    with signed_in(random_sub()):
        async with Client(server(login_engine, Clock())) as client:
            subject_id = (await call(client, "save_subject", SUBJECT_ARGS))["subject"]["subject_id"]
            saved = await call(client, "save_notes", {"subject_id": subject_id, "notes": [NOTE]})
            note_id = saved["notes"][0]["note_id"]
            args = {"note_id": note_id, "artifact_kind": "lesson_view", "title": "Vista"}
            artifact = await call(client, "save_artifact", {**args, "html": at_cap})
            fetched = await call(client, "get_artifact", {"artifact_id": artifact["artifact_id"]})
            result = await client.call_tool(
                "save_artifact", {**args, "html": over}, raise_on_error=False
            )
            listed = await call(client, "get_note", {"note_id": note_id})
    assert fetched["html"] == at_cap
    assert result.is_error
    body = json.loads(text_of(result))
    assert (body["code"], body["fields"]) == ("validation_failed", ["html"])
    assert [a["artifact_id"] for a in listed["artifacts"]] == [artifact["artifact_id"]]
