"""tutor.domain.templates: the domain-neutral template contract (subjects spec 15)."""

import dataclasses
import json
from decimal import Decimal
from html.parser import HTMLParser
from typing import Any
from uuid import UUID

import pytest
from hypothesis import given
from hypothesis import strategies as st

from tutor.domain.errors import FieldError
from tutor.domain.html_guard import MAX_TEMPLATE_BYTES, TEMPLATE_MARKER
from tutor.domain.templates import (
    GUIDE_SECTIONS,
    INLINE_GUIDE_MAX_CHARS,
    MAX_INSTANCES,
    MAX_PARAMS_JSON_BYTES,
    MAX_POOL_ENTRIES,
    MAX_PUBLIC_PAYLOAD_BYTES,
    MAX_TEMPLATE_PARAMS,
    PARAM_TYPES,
    PROMPT_TEXT_MAX,
    TEMPLATE_GUIDE_VERSION,
    ChoiceAnswer,
    Draw,
    Graded,
    NumberAnswer,
    ParamPair,
    ParamSpecInput,
    PoolEntryInput,
    SelfReportAnswer,
    SequenceAnswer,
    TemplateInput,
    TemplateSpec,
    ValueAnswer,
    ValuesAnswer,
    compose_document,
    content_key,
    contract_constants,
    draw,
    encode_payload,
    grade,
    normalise_answer,
    param_specs_from_json,
    param_specs_to_json,
    parse_answer,
    parse_number,
    pool_size,
    public_params,
    public_payload,
    render_text,
    seed_for,
    spec_hash,
    validate_params,
    validate_template,
)

pytestmark = pytest.mark.unit

V = TEMPLATE_GUIDE_VERSION
INSTANCE = UUID("00000000-0000-0000-0000-0000000000aa")
SUBJECT = UUID("00000000-0000-0000-0000-000000000001")
TEMPLATE = UUID("00000000-0000-0000-0000-000000000002")
NOTE = UUID("00000000-0000-0000-0000-000000000003")


def param(name: str, ptype: str, role: str = "prompt", **changes: Any) -> ParamSpecInput:
    return dataclasses.replace(ParamSpecInput(name, ptype, role, name.title()), **changes)


def template(grading: str, *params: ParamSpecInput, **changes: Any) -> TemplateInput:
    base = TemplateInput(
        name="Plantilla",
        description="Cuándo usarla.",
        template_purpose="display" if grading == "none" else "exercise",
        grading_kind=grading,
        params=params,
    )
    return dataclasses.replace(base, **changes)


def ok(data: TemplateInput) -> TemplateSpec:
    result = validate_template(data, V)
    assert isinstance(result, TemplateSpec), result
    return result


def fields(data: TemplateInput, version: int = V) -> tuple[str, ...]:
    result = validate_template(data, version)
    assert isinstance(result, tuple), "expected field errors"
    return tuple(e.field for e in result)


POOL = param("deck", "pool", "pool")
FLASHCARD = template("exact_text", POOL, pool_draw="one", text_match="keep_accents")
CHOICE = template(
    "choice_index",
    param("question", "text"),
    param("options", "text_list"),
    param("right", "integer", "answer_key"),
    param("why", "text", "reveal"),
    options_param="options",
)
CHOICE_POOL = template(
    "choice_index", POOL, pool_draw="one_of_choices", pool_count=4, needs_view=True
)
NUMERIC = template(
    "numeric",
    param("problem", "text"),
    param("answer", "number", "answer_key"),
    param("worked_solution", "text", "reveal"),
    numeric_tolerance=0.01,
)
TIMELINE = template("exact_sequence", POOL, pool_draw="ordered", pool_count=4)
STEPS = template("exact_sequence", param("steps", "text_list", "answer_key"))
SET = template("exact_set", param("question", "text"), param("parts", "text_list", "answer_key"))
SELF = template(
    "self_report", param("task", "text"), param("tempo", "integer", required=False, min=20)
)
TABLE = template("none", param("table", "text_table"), param("note", "text", required=False))


def entries(count: int, *, ordered: bool = False) -> tuple[PoolEntryInput, ...]:
    return tuple(
        PoolEntryInput(
            cue=f"cue {i}",
            answer=f"answer {i}",
            accept=(f"alt {i}",),
            explain=f"because {i}",
            order=float(i) if ordered else None,
        )
        for i in range(count)
    )


def params_ok(spec: TemplateSpec, *pairs: ParamPair) -> dict[str, object]:
    result = validate_params(spec, pairs, path="")
    assert isinstance(result, dict), result
    return result


def param_fields(spec: TemplateSpec, *pairs: ParamPair, path: str = "") -> tuple[str, ...]:
    result = validate_params(spec, pairs, path=path)
    assert isinstance(result, tuple), "expected field errors"
    return tuple(e.field for e in result)


def choice_params() -> tuple[ParamPair, ...]:
    return (
        ParamPair("question", "text", "¿Qué acorde es?"),
        ParamPair("options", "text_list", ["Do", "Re", "Mi"]),
        ParamPair("right", "integer", 1),
        ParamPair("why", "text", "Porque sí."),
    )


# --- constants ---------------------------------------------------------------------------------


def test_caps_match_ruling_24() -> None:
    assert MAX_INSTANCES == 12
    assert MAX_TEMPLATE_BYTES == 40_000
    assert MAX_TEMPLATE_PARAMS == 8
    assert (MAX_PARAMS_JSON_BYTES, MAX_PUBLIC_PAYLOAD_BYTES) == (32_768, 8_192)
    assert (MAX_POOL_ENTRIES, PROMPT_TEXT_MAX, INLINE_GUIDE_MAX_CHARS) == (200, 500, 6_000)
    assert len(GUIDE_SECTIONS) == 8


def test_contract_constants_name_every_type_role_kind_and_rule() -> None:
    constants = contract_constants()
    assert set(constants["param_types"]) == set(PARAM_TYPES)
    assert set(constants["param_roles"]) == {"prompt", "answer_key", "reveal", "pool"}
    assert set(constants["grading_kinds"]) == {
        "none",
        "choice_index",
        "exact_text",
        "exact_set",
        "exact_sequence",
        "numeric",
        "self_report",
    }
    assert set(constants["text_match"]) == {"fold_all", "keep_accents", "exact_case"}
    assert constants["marker"] == TEMPLATE_MARKER
    assert constants["max_template_bytes"] == MAX_TEMPLATE_BYTES


# --- validate_template --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("data", "practice_kind"),
    [
        (FLASHCARD, "drill"),
        (CHOICE, "quiz"),
        (CHOICE_POOL, "drill"),
        (NUMERIC, "quiz"),
        (TIMELINE, "drill"),
        (STEPS, "quiz"),
        (SET, "quiz"),
        (SELF, "exercise"),
        (TABLE, None),
    ],
)
def test_every_row_of_the_compatibility_table_is_accepted(
    data: TemplateInput, practice_kind: str | None
) -> None:
    spec = ok(data)
    assert spec.practice_kind == practice_kind


def test_defaults_are_filled() -> None:
    spec = ok(CHOICE)
    assert (spec.name, spec.description, spec.purpose) == (
        "Plantilla",
        "Cuándo usarla.",
        "exercise",
    )
    assert (spec.text_match, spec.numeric_tolerance, spec.pool_draw, spec.pool_count) == (
        None,
        None,
        "none",
        None,
    )
    question, options, right, _ = spec.params
    assert (question.max_length, question.min_items, question.min) == (500, None, None)
    assert (options.min_items, options.max_items, options.max_length) == (1, 12, 80)
    assert (right.min, right.max) == (-1_000_000_000, 1_000_000_000)
    assert ok(SET).text_match == "fold_all"
    assert ok(NUMERIC).numeric_tolerance == Decimal("0.01")
    assert ok(template("numeric", param("n", "number", "answer_key"))).numeric_tolerance == 0
    assert ok(TABLE).params[0].max_items == 40
    assert ok(FLASHCARD).text_match == "keep_accents"


def test_names_and_labels_are_collapsed() -> None:
    spec = ok(dataclasses.replace(SET, name="  Varias   partes ", description="   "))
    assert (spec.name, spec.description) == ("Varias partes", None)
    labelled = template("self_report", param("task", "text", label="  La   tarea "))
    assert ok(labelled).params[0].label == "La tarea"


@pytest.mark.parametrize(
    ("data", "field"),
    [
        # Review Focus 9
        (dataclasses.replace(CHOICE, options_param=None), "options_param"),
        (dataclasses.replace(CHOICE, options_param="question"), "options_param"),
        (dataclasses.replace(CHOICE, options_param="missing"), "options_param"),
        (
            template("none", param("t", "text"), param("k", "text", "answer_key")),
            "params[1].param_role",
        ),
        (template("none", POOL, pool_draw="one"), "pool_draw"),
        (
            template("exact_set", param("k", "text_list", "answer_key"), POOL, pool_draw="one"),
            "pool_draw",
        ),
        (dataclasses.replace(NUMERIC, text_match="fold_all"), "text_match"),
        (
            template("exact_text", POOL, param("k", "text", "answer_key"), pool_draw="one"),
            "params[1].param_role",
        ),
        (dataclasses.replace(FLASHCARD, pool_draw="ordered", pool_count=3), "pool_draw"),
        (template("exact_text", param("k", "integer", "answer_key")), "params[0].param_type"),
        (template("exact_text", param("q", "text")), "params"),  # no answer key
        (template("numeric", param("k", "text", "answer_key")), "params[0].param_type"),
        (template("exact_set", param("k", "text", "answer_key")), "params[0].param_type"),
        (
            template(
                "choice_index",
                param("o", "text_list"),
                param("k", "text", "answer_key"),
                options_param="o",
            ),
            "params[1].param_type",
        ),
        (template("self_report", param("k", "text", "answer_key")), "params[0].param_role"),
        (
            template(
                "exact_text", param("k", "text", "answer_key"), param("j", "text", "answer_key")
            ),
            "params[1].param_role",
        ),
        (dataclasses.replace(FLASHCARD, pool_draw="none"), "params[0].param_type"),
        (template("exact_text", param("q", "text"), pool_draw="one"), "params"),  # no pool param
        (
            template("exact_text", POOL, param("x", "pool", "pool"), pool_draw="one"),
            "params[1].param_type",
        ),
        (template("exact_text", param("p", "pool", "prompt"), pool_draw="one"), "params"),
        (dataclasses.replace(CHOICE_POOL, pool_count=None), "pool_count"),
        (dataclasses.replace(CHOICE_POOL, pool_count=9), "pool_count"),
        (dataclasses.replace(CHOICE_POOL, pool_count=1), "pool_count"),
        (dataclasses.replace(TIMELINE, pool_count=13), "pool_count"),
        (dataclasses.replace(FLASHCARD, pool_count=2), "pool_count"),
        (dataclasses.replace(SET, numeric_tolerance=0.5), "numeric_tolerance"),
        (dataclasses.replace(NUMERIC, numeric_tolerance=-1), "numeric_tolerance"),
        (dataclasses.replace(NUMERIC, numeric_tolerance=2_000_000), "numeric_tolerance"),
        (dataclasses.replace(NUMERIC, numeric_tolerance=0.0000001), "numeric_tolerance"),
        (dataclasses.replace(NUMERIC, numeric_tolerance=float("nan")), "numeric_tolerance"),
        (dataclasses.replace(TABLE, template_purpose="exercise"), "grading_kind"),
        (dataclasses.replace(SET, template_purpose="display"), "grading_kind"),
        (dataclasses.replace(SET, text_match="loose"), "text_match"),
        (dataclasses.replace(SET, template_purpose="page"), "template_purpose"),
        (dataclasses.replace(SET, grading_kind="essay"), "grading_kind"),
        (dataclasses.replace(SET, pool_draw="many"), "pool_draw"),
        (dataclasses.replace(SET, name=" "), "name"),
        (dataclasses.replace(SET, name="n" * 81), "name"),
        (dataclasses.replace(SET, description="d" * 301), "description"),
        (dataclasses.replace(SET, params=()), "params"),
        (
            dataclasses.replace(
                SET, params=(*SET.params, *(param(f"p{i}", "text") for i in range(7)))
            ),
            "params",
        ),
        (
            dataclasses.replace(CHOICE, options_param="options", grading_kind="exact_set"),
            "params[2].param_type",
        ),
    ],
)
def test_review_focus_9_templates_that_break_the_rules(data: TemplateInput, field: str) -> None:
    assert field in fields(data)


def test_options_param_only_for_authored_choices() -> None:
    assert fields(dataclasses.replace(CHOICE_POOL, options_param="deck")) == ("options_param",)
    assert fields(dataclasses.replace(SET, options_param="parts")) == ("options_param",)
    optional = dataclasses.replace(
        CHOICE,
        params=(
            *CHOICE.params[:1],
            param("options", "text_list", required=False),
            *CHOICE.params[2:],
        ),
    )
    assert fields(optional) == ("options_param",)


def test_answer_keys_and_pools_must_be_required() -> None:
    data = template("exact_text", param("k", "text", "answer_key", required=False))
    assert fields(data) == ("params[0].required",)


@pytest.mark.parametrize(
    ("spec", "field"),
    [
        (param("Bad", "text"), "params[0].name"),
        (param("1x", "text"), "params[0].name"),
        (param("x" * 33, "text"), "params[0].name"),
        (param("drawn", "text"), "params[0].name"),
        (param("ui_lang", "text"), "params[0].name"),
        (param("labels", "text"), "params[0].name"),
        (param("instance_id", "text"), "params[0].name"),
        (param("t", "image"), "params[0].param_type"),
        (param("t", "text", "secret"), "params[0].param_role"),
        (param("t", "text", label=" "), "params[0].label"),
        (param("t", "text", label="l" * 61), "params[0].label"),
        (param("t", "text", max_length=4001), "params[0].max_length"),
        (param("t", "text", max_length=0), "params[0].max_length"),
        (param("t", "text_list", max_length=201), "params[0].max_length"),
        (param("t", "text_list", max_items=25), "params[0].max_items"),
        (param("t", "text_list", min_items=0), "params[0].min_items"),
        (param("t", "text_list", min_items=5, max_items=3), "params[0].min_items"),
        (param("t", "text_table", max_items=41), "params[0].max_items"),
        (param("t", "text", max_items=3), "params[0].max_items"),
        (param("t", "text", min_items=1), "params[0].min_items"),
        (param("t", "integer", max_length=3), "params[0].max_length"),
        (param("t", "text", min=1), "params[0].min"),
        (param("t", "boolean", max=1), "params[0].max"),
        (param("t", "integer", min=1.5), "params[0].min"),
        (param("t", "integer", max=2_000_000_000), "params[0].max"),
        (param("t", "number", min=0.1234567), "params[0].min"),
        (param("t", "number", min=5, max=1), "params[0].min"),
    ],
)
def test_parameter_spec_errors(spec: ParamSpecInput, field: str) -> None:
    assert fields(template("self_report", spec)) == (field,)


def test_duplicate_parameter_names_are_refused() -> None:
    assert fields(template("self_report", param("t", "text"), param("t", "integer"))) == (
        "params[1].name",
    )


def test_stale_guide_version() -> None:
    # Review Focus 9
    result = validate_template(SET, V + 1)
    assert result == (FieldError("guide_version", "stale"),)
    assert fields(SET, 0) == ("guide_version",)


def test_errors_are_reported_in_field_order() -> None:
    bad = dataclasses.replace(SET, name="", template_purpose="page", params=(param("Bad", "text"),))
    assert fields(bad, V + 1) == ("guide_version", "name", "template_purpose", "params[0].name")


def test_param_specs_round_trip_through_json() -> None:
    for data in (CHOICE, NUMERIC, TABLE, FLASHCARD, SELF):
        spec = ok(data)
        stored = json.loads(json.dumps(param_specs_to_json(spec.params)))
        assert param_specs_from_json(stored) == spec.params
    with pytest.raises(ValueError, match="unknown"):
        param_specs_from_json([{**param_specs_to_json(ok(SET).params)[0], "param_type": "x"}])


def test_spec_hash_changes_with_any_part() -> None:
    spec = ok(NUMERIC)
    assert spec_hash(spec) == spec_hash(ok(NUMERIC))
    assert spec_hash(spec) != spec_hash(dataclasses.replace(spec, name="Otra"))
    assert spec_hash(spec) != spec_hash(dataclasses.replace(spec, numeric_tolerance=Decimal(1)))
    assert spec_hash(spec) != spec_hash(dataclasses.replace(spec, params=spec.params[:2]))
    assert len(spec_hash(spec)) == 64


# --- validate_params ----------------------------------------------------------------------------


def test_every_parameter_type_is_accepted_and_canonical() -> None:
    spec = ok(
        template(
            "self_report",
            param("a", "text"),
            param("b", "text_list"),
            param("c", "text_table"),
            param("d", "integer"),
            param("e", "integer_list"),
            param("f", "number"),
            param("g", "number_list"),
            param("h", "boolean"),
        )
    )
    values = params_ok(
        spec,
        ParamPair("h", "boolean", True),
        ParamPair("a", "text", "  línea 1\n  línea 2"),
        ParamPair("b", "text_list", [" uno ", "dos"]),
        ParamPair("c", "text_table", [["1810", " Grito "], ["1821", ""]]),
        ParamPair("d", "integer", -3),
        ParamPair("e", "integer_list", [0, 5, 7]),
        ParamPair("f", "number", 3.25),
        ParamPair("g", "number_list", [1, 2.5]),
    )
    assert list(values) == list("abcdefgh")  # keys sorted
    assert values == {
        "a": "  línea 1\n  línea 2",  # text kept as sent (code, tablature)
        "b": ["uno", "dos"],
        "c": [["1810", "Grito"], ["1821", ""]],
        "d": -3,
        "e": [0, 5, 7],
        "f": 3.25,
        "g": [1.0, 2.5],
        "h": True,
    }


def test_optional_parameters_are_stored_as_null() -> None:
    values = params_ok(ok(SELF), ParamPair("task", "text", "Toca la escala."))
    assert values == {"task": "Toca la escala.", "tempo": None}


@pytest.mark.parametrize(
    ("pairs", "field"),
    [
        ((ParamPair("task", "text", "x"), ParamPair("nope", "text", "x")), "params[1].name"),
        ((ParamPair("task", "integer", 3),), "params[0]"),  # the wrong value field
        ((), "params"),  # a required parameter missing
        ((ParamPair("task", "text", "   "),), "params[0].text"),
        ((ParamPair("task", "text", "t" * 501),), "params[0].text"),
        ((ParamPair("task", "text", 3),), "params[0].text"),
        ((ParamPair("task", "text", "ok"), ParamPair("tempo", "integer", 19)), "params[1].integer"),
        (
            (ParamPair("task", "text", "ok"), ParamPair("tempo", "integer", True)),
            "params[1].integer",
        ),
        (
            (ParamPair("task", "text", "ok"), ParamPair("tempo", "integer", 2.0)),
            "params[1].integer",
        ),
        ((ParamPair("task", "text", "a"), ParamPair("task", "text", "b")), "params[1].name"),
    ],
)
def test_parameter_errors(pairs: tuple[ParamPair, ...], field: str) -> None:
    assert param_fields(ok(SELF), *pairs) == (field,)


@pytest.mark.parametrize(
    ("ptype", "value", "field"),
    [
        ("text_list", [], "v.text_list"),
        ("text_list", ["x"] * 13, "v.text_list"),
        ("text_list", ["ok", " "], "v.text_list[1]"),
        ("text_list", ["ok", "x" * 81], "v.text_list[1]"),
        ("text_list", "x", "v.text_list"),
        ("text_list", [1], "v.text_list"),
        ("text_table", [], "v.text_table"),
        ("text_table", [["a"]] * 41, "v.text_table"),
        ("text_table", [["a"] * 9], "v.text_table[0]"),
        ("text_table", [[]], "v.text_table[0]"),
        ("text_table", [["a", "x" * 201]], "v.text_table[0][1]"),
        ("text_table", ["row"], "v.text_table"),
        ("integer_list", [1, 2.5], "v.integer_list[1]"),
        ("integer_list", [2_000_000_000], "v.integer_list[0]"),
        ("integer_list", 3, "v.integer_list"),
        ("number", float("inf"), "v.number"),
        ("number", 1.0000001, "v.number"),
        ("number", "3", "v.number"),
        ("number", 1e10, "v.number"),
        ("number_list", [1, "x"], "v.number_list[1]"),
        ("boolean", 1, "v.boolean"),
    ],
)
def test_parameter_value_errors(ptype: str, value: object, field: str) -> None:
    spec = ok(template("self_report", param("v", ptype)))
    path = field.replace("v.", "params[0].")
    assert param_fields(spec, ParamPair("v", ptype, value)) == (path,)


def test_bounds_and_counts_from_the_spec() -> None:
    spec = ok(
        template(
            "self_report",
            param("n", "number", min=0, max=1),
            param("l", "text_list", min_items=2, max_items=3, max_length=3),
        )
    )
    assert params_ok(spec, ParamPair("n", "number", 1), ParamPair("l", "text_list", ["abc", "d"]))
    assert param_fields(
        spec, ParamPair("n", "number", 1.5), ParamPair("l", "text_list", ["a"])
    ) == (
        "params[0].number",
        "params[1].text_list",
    )


def test_paths_carry_the_prefix() -> None:
    # Plan ruling 22: notes[0].instances[2].params[1].pool[7].answer
    spec = ok(FLASHCARD)
    pool = list(entries(8))
    pool[7] = PoolEntryInput("cue", "")
    found = param_fields(spec, ParamPair("deck", "pool", tuple(pool)), path="notes[0].instances[2]")
    assert found == ("notes[0].instances[2].params[0].pool[7].answer",)


@pytest.mark.parametrize(
    ("pool", "field"),
    [
        ((), "params[0].pool"),
        (entries(1), "params[0].pool"),
        (entries(201), "params[0].pool"),
        ((PoolEntryInput(" ", "a"), *entries(2)), "params[0].pool[0].cue"),
        ((PoolEntryInput("c" * 201, "a"), *entries(2)), "params[0].pool[0].cue"),
        ((PoolEntryInput("c", "a" * 201), *entries(2)), "params[0].pool[0].answer"),
        ((PoolEntryInput("c", "a", accept=("x",) * 7), *entries(2)), "params[0].pool[0].accept"),
        ((PoolEntryInput("c", "a", accept=("",)), *entries(2)), "params[0].pool[0].accept[0]"),
        ((PoolEntryInput("c", "a", explain="e" * 301), *entries(2)), "params[0].pool[0].explain"),
        ((PoolEntryInput("c", "a", order=1.0), *entries(2)), "params[0].pool[0].order"),
        (("not an entry", "x"), "params[0].pool"),
    ],
)
def test_pool_entry_rules(pool: tuple[Any, ...], field: str) -> None:
    assert param_fields(ok(FLASHCARD), ParamPair("deck", "pool", pool)) == (field,)


def test_pool_is_canonical() -> None:
    pool = (PoolEntryInput(" the bill ", " la cuenta ", ("cuenta",), "  "), *entries(1))
    values = params_ok(ok(FLASHCARD), ParamPair("deck", "pool", pool))
    deck = values["deck"]
    assert isinstance(deck, list)
    assert deck[0] == {
        "cue": "the bill",
        "answer": "la cuenta",
        "accept": ["cuenta"],
        "explain": None,
        "order": None,
    }


def test_ordered_pool_needs_distinct_orders_and_enough_entries() -> None:
    spec = ok(TIMELINE)
    assert params_ok(spec, ParamPair("deck", "pool", entries(4, ordered=True)))
    same = (*entries(3, ordered=True), PoolEntryInput("c", "a", order=0.0))
    assert param_fields(spec, ParamPair("deck", "pool", same)) == ("params[0].pool",)
    assert param_fields(spec, ParamPair("deck", "pool", entries(3, ordered=True))) == (
        "params[0].pool",
    )
    missing = (*entries(4, ordered=True), PoolEntryInput("c", "a"))
    assert param_fields(spec, ParamPair("deck", "pool", missing)) == ("params[0].pool[4].order",)
    nan = (*entries(4, ordered=True), PoolEntryInput("c", "a", order=float("nan")))
    assert param_fields(spec, ParamPair("deck", "pool", nan)) == ("params[0].pool[4].order",)


def test_choices_pool_needs_enough_distinct_answers() -> None:
    spec = ok(CHOICE_POOL)
    assert params_ok(spec, ParamPair("deck", "pool", entries(4)))
    repeated = tuple(PoolEntryInput(f"c{i}", "La" if i % 2 else "la ") for i in range(6))
    assert param_fields(spec, ParamPair("deck", "pool", repeated)) == ("params[0].pool",)


def test_choice_key_must_index_the_options() -> None:
    spec = ok(CHOICE)
    pairs = list(choice_params())
    pairs[2] = ParamPair("right", "integer", 3)
    assert param_fields(spec, *pairs) == ("params[2].integer",)
    pairs[2] = ParamPair("right", "integer", -1)
    assert param_fields(spec, *pairs) == ("params[2].integer",)
    assert params_ok(spec, *choice_params())["right"] == 1


def test_authored_sequence_needs_two_to_twelve_distinct_items() -> None:
    spec = ok(STEPS)
    assert params_ok(spec, ParamPair("steps", "text_list", ["a", "b"]))
    for items in (["a"], ["a", "A "], [f"s{i}" for i in range(12)] + []):
        if len(items) == 12:
            assert params_ok(spec, ParamPair("steps", "text_list", items))
            continue
        assert param_fields(spec, ParamPair("steps", "text_list", items)) == (
            "params[0].text_list",
        )


def test_review_focus_6_params_json_cap() -> None:
    # Review Focus 6: 32,768 bytes of parameters in all, pool included.
    spec = ok(
        template("exact_text", param("intro", "text", max_length=4000), POOL, pool_draw="one")
    )
    pool = tuple(PoolEntryInput(f"c{i}", "a" * 150, explain="e" * 30) for i in range(200))
    found = param_fields(spec, ParamPair("intro", "text", "x"), ParamPair("deck", "pool", pool))
    assert found == ("params",)


def test_review_focus_6_public_payload_cap() -> None:
    # Prompt parameters plus the largest possible draw must stay within 8,192 bytes.
    spec = ok(
        template(
            "exact_text",
            *(param(f"t{i}", "text", max_length=4000) for i in range(3)),
            param("k", "text", "answer_key"),
        )
    )
    pairs = [ParamPair(f"t{i}", "text", "x" * 2800) for i in range(3)]
    assert param_fields(spec, *pairs, ParamPair("k", "text", "k")) == ("params",)
    pairs[2] = ParamPair("t2", "text", "x" * 2500)
    assert params_ok(spec, *pairs, ParamPair("k", "text", "k"))


def test_public_params_and_pool_size() -> None:
    values = params_ok(ok(CHOICE), *choice_params())
    assert public_params(ok(CHOICE), values) == {
        "question": "¿Qué acorde es?",
        "options": ["Do", "Re", "Mi"],
    }
    assert pool_size(ok(CHOICE), values) is None
    pool_values = params_ok(ok(FLASHCARD), ParamPair("deck", "pool", entries(5)))
    assert public_params(ok(FLASHCARD), pool_values) == {}
    assert pool_size(ok(FLASHCARD), pool_values) == 5


# --- content key --------------------------------------------------------------------------------


def test_content_key_changes_on_any_part_and_ignores_param_order() -> None:
    base = content_key(SUBJECT, TEMPLATE, {"a": 1, "b": [2]}, "Haz esto.", NOTE)
    assert base == content_key(SUBJECT, TEMPLATE, {"b": [2], "a": 1}, "Haz esto.", NOTE)
    assert len(base) == 64
    others = {
        content_key(TEMPLATE, TEMPLATE, {"a": 1, "b": [2]}, "Haz esto.", NOTE),
        content_key(SUBJECT, SUBJECT, {"a": 1, "b": [2]}, "Haz esto.", NOTE),
        content_key(SUBJECT, TEMPLATE, {"a": 2, "b": [2]}, "Haz esto.", NOTE),
        content_key(SUBJECT, TEMPLATE, {"a": 1, "b": [2]}, "Haz eso.", NOTE),
        content_key(SUBJECT, TEMPLATE, {"a": 1, "b": [2]}, "Haz esto.", None),
    }
    assert base not in others and len(others) == 5


# --- draws --------------------------------------------------------------------------------------


def test_seed_is_the_first_eight_bytes_of_sha256_as_signed() -> None:
    seed = seed_for(INSTANCE)
    assert -(2**63) <= seed < 2**63
    assert seed == seed_for(UUID(str(INSTANCE)))
    assert seed != seed_for(SUBJECT)


def test_pinned_draws_for_a_pinned_seed() -> None:
    # A change of the generator or of the draw rules fails here (spec 15.12).
    flash = params_ok(ok(FLASHCARD), ParamPair("deck", "pool", entries(10)))
    choices = params_ok(ok(CHOICE_POOL), ParamPair("deck", "pool", entries(10)))
    timeline = params_ok(ok(TIMELINE), ParamPair("deck", "pool", entries(10, ordered=True)))
    steps = params_ok(ok(STEPS), ParamPair("steps", "text_list", ["a", "b", "c", "d"]))
    assert draw(ok(FLASHCARD), flash, 42) == PINNED["flash"]
    assert draw(ok(CHOICE_POOL), choices, 42) == PINNED["choices"]
    assert draw(ok(TIMELINE), timeline, 42) == PINNED["timeline"]
    assert draw(ok(STEPS), steps, 42) == PINNED["steps"]
    assert draw(ok(SET), {}, 42) == Draw()


PINNED: dict[str, Draw] = {
    "flash": Draw(entries=(7,)),
    "choices": Draw(entries=(7, 6, 4, 3), order=(3, 2, 1, 0)),
    "timeline": Draw(entries=(8, 3, 6, 5), order=(1, 3, 0, 2)),
    "steps": Draw(order=(1, 3, 0, 2)),
}


@given(st.integers(min_value=-(2**63), max_value=2**63 - 1))
def test_draws_are_deterministic_distinct_and_never_in_the_right_order(seed: int) -> None:
    choices = params_ok(ok(CHOICE_POOL), ParamPair("deck", "pool", entries(6)))
    shown = draw(ok(CHOICE_POOL), choices, seed)
    assert shown == draw(ok(CHOICE_POOL), choices, seed)
    assert len(set(shown.entries)) == 4 and sorted(shown.order) == [0, 1, 2, 3]
    timeline = params_ok(ok(TIMELINE), ParamPair("deck", "pool", entries(5, ordered=True)))
    shown = draw(ok(TIMELINE), timeline, seed)
    assert len(set(shown.entries)) == 4
    deck = timeline["deck"]
    assert isinstance(deck, list)
    orders = [deck[shown.entries[k]]["order"] for k in shown.order]
    assert orders != sorted(orders)  # never the sorted order
    steps = params_ok(ok(STEPS), ParamPair("steps", "text_list", ["a", "b"]))
    assert draw(ok(STEPS), steps, seed).order == (1, 0)  # never the key order
    flash = params_ok(ok(FLASHCARD), ParamPair("deck", "pool", entries(3)))
    assert 0 <= draw(ok(FLASHCARD), flash, seed).entries[0] < 3


def test_choices_draw_skips_entries_with_the_same_answer() -> None:
    pool = (
        PoolEntryInput("A4", "La"),
        PoolEntryInput("A5", "la"),
        PoolEntryInput("A3", "LA "),
        PoolEntryInput("C4", "Do"),
        PoolEntryInput("D4", "Re"),
        PoolEntryInput("E4", "Mi"),
    )
    spec = ok(CHOICE_POOL)
    values = params_ok(spec, ParamPair("deck", "pool", pool))
    for seed in range(50):
        shown = draw(spec, values, seed)
        answers = [normalise_answer(pool[i].answer, "fold_all") for i in shown.entries]
        assert len(set(answers)) == 4


# --- payload and document -----------------------------------------------------------------------

LEAKS = ("answer 0", "alt 0", "because 0", "worked out", "SECRET_KEY")


def test_review_focus_7_public_payload_drops_keys_reveals_and_pools() -> None:
    # Review Focus 7
    spec = ok(NUMERIC)
    values = params_ok(
        spec,
        ParamPair("problem", "text", "2 + 2"),
        ParamPair("answer", "number", 4),
        ParamPair("worked_solution", "text", "worked out"),
    )
    payload = public_payload(INSTANCE, spec, values, draw(spec, values, 1))
    assert payload == {
        "v": 1,
        "instance_id": str(INSTANCE),
        "template_purpose": "exercise",
        "grading_kind": "numeric",
        "ui_lang": "es",
        "params": {"problem": "2 + 2"},
        "drawn": None,
        "labels": {"problem": "Problem"},
    }
    flash = ok(FLASHCARD)
    pool_values = params_ok(flash, ParamPair("deck", "pool", entries(3)))
    shown = Draw(entries=(1,))
    text = json.dumps(public_payload(INSTANCE, flash, pool_values, shown))
    assert '"drawn": {"cue": "cue 1"}' in text
    assert not any(leak in text for leak in LEAKS)
    assert "cue 0" not in text and "answer 1" not in text  # no undrawn entry, no answer


def test_choices_payload_shows_answers_only_as_options() -> None:
    spec = ok(CHOICE_POOL)
    values = params_ok(spec, ParamPair("deck", "pool", entries(6)))
    shown = Draw(entries=(2, 0, 5, 1), order=(1, 3, 0, 2))
    payload = public_payload(INSTANCE, spec, values, shown)
    assert payload["drawn"] == {
        "cue": "cue 2",
        "options": ["answer 0", "answer 1", "answer 2", "answer 5"],
    }
    assert "because" not in json.dumps(payload)


def test_sequence_payloads() -> None:
    steps = params_ok(ok(STEPS), ParamPair("steps", "text_list", ["a", "b", "c"]))
    shown = Draw(order=(2, 0, 1))
    assert public_payload(INSTANCE, ok(STEPS), steps, shown)["drawn"] == {"cues": ["c", "a", "b"]}
    assert public_payload(INSTANCE, ok(STEPS), steps, shown)["params"] == {}
    timeline = params_ok(ok(TIMELINE), ParamPair("deck", "pool", entries(5, ordered=True)))
    shown = Draw(entries=(4, 1, 3, 0), order=(3, 0, 2, 1))
    assert public_payload(INSTANCE, ok(TIMELINE), timeline, shown)["drawn"] == {
        "cues": ["cue 0", "cue 4", "cue 3", "cue 1"]
    }


class _Elements(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.ids: list[str] = []
        self.data: dict[str, str] = {}
        self._current: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        for name, value in attrs:
            if name == "id" and value:
                self.ids.append(value)
                self._current = value

    def handle_endtag(self, tag: str) -> None:
        self._current = None

    def handle_data(self, data: str) -> None:
        if self._current is not None:
            self.data[self._current] = self.data.get(self._current, "") + data


HOSTILE = (
    "</script><script>alert(1)</script>",
    "<!--",
    "&amp;",
    "a" + chr(0x2028) + "b" + chr(0x2029) + "c",
    "</SCRIPT >",
    "';alert(1);'",
)


@pytest.mark.parametrize("value", HOSTILE)
def test_review_focus_8_parameters_cannot_become_markup(value: str) -> None:
    # Review Focus 8
    spec = ok(template("exact_text", param("question", "text"), param("k", "text", "answer_key")))
    values = params_ok(spec, ParamPair("question", "text", value), ParamPair("k", "text", "k"))
    encoded = encode_payload(public_payload(INSTANCE, spec, values, Draw()))
    for raw in ("<", ">", "&", "'", chr(0x2028), chr(0x2029)):
        assert raw not in encoded
    html = f"<!doctype html><html><body><p>Hola</p>{TEMPLATE_MARKER}<p id='end'>x</p></body></html>"
    document = compose_document(html, encoded, "window.tutorSubmit = function () {};")
    parsed = _Elements()
    parsed.feed(document)
    assert parsed.ids == ["tutor-params", "tutor-bridge", "end"]
    assert json.loads(parsed.data["tutor-params"])["params"]["question"] == value


def test_compose_document_inserts_payload_and_bridge_at_the_marker_only() -> None:
    html = f"<!doctype html><html><body>{TEMPLATE_MARKER}</body></html>"
    document = compose_document(html, '{"v":1}', "var x = 1;")
    assert document == (
        '<!doctype html><html><body><script type="application/json" id="tutor-params">'
        '{"v":1}</script><script id="tutor-bridge">var x = 1;</script></body></html>'
    )
    for bad in ("<!doctype html>", html + TEMPLATE_MARKER):
        with pytest.raises(ValueError, match="marker"):
            compose_document(bad, "{}", "")
    with pytest.raises(ValueError, match="end its script"):
        compose_document(html, "{}", "x = '</script>';")
    with pytest.raises(ValueError, match="end its script"):
        compose_document(html, '{"a":"<"}', "")


def test_encode_payload_sorts_keys_and_round_trips() -> None:
    text = encode_payload({"b": "é<", "a": [1, None]})
    assert text == '{"a":[1,null],"b":"é\\u003c"}'
    assert json.loads(text) == {"a": [1, None], "b": "é<"}


def test_encode_payload_escapes_single_quotes() -> None:
    # Security review P3: no quote can end a JavaScript string the payload is pasted into.
    text = encode_payload({"a": "it's"})
    assert "'" not in text and text == '{"a":"it\\u0027s"}'
    assert json.loads(text) == {"a": "it's"}


# --- text rendering -----------------------------------------------------------------------------


def test_render_text_for_an_authored_choice() -> None:
    values = params_ok(ok(CHOICE), *choice_params())
    text = render_text(ok(CHOICE), values, Draw(), "Elige el acorde.")
    assert text == (
        "Elige el acorde.\nQuestion: ¿Qué acorde es?\nOptions:\n1. Do\n2. Re\n3. Mi\n"
        "Responde con el número de la opción."
    )
    assert "Porque" not in text  # no reveal


def test_render_text_for_pools_and_sequences() -> None:
    flash = params_ok(ok(FLASHCARD), ParamPair("deck", "pool", entries(3)))
    text = render_text(ok(FLASHCARD), flash, Draw(entries=(2,)), "Traduce.")
    assert text == "Traduce.\nPista: cue 2\nResponde con una palabra o frase corta."
    steps = params_ok(ok(STEPS), ParamPair("steps", "text_list", ["a", "b"]))
    text = render_text(ok(STEPS), steps, Draw(order=(1, 0)), "Ordena.")
    assert text == "Ordena.\n- b\n- a\nResponde con los elementos en orden, separados por comas."


def test_render_text_never_shows_the_cue_when_the_view_is_needed() -> None:
    values = params_ok(ok(CHOICE_POOL), ParamPair("deck", "pool", entries(4)))
    text = render_text(ok(CHOICE_POOL), values, Draw((0, 1, 2, 3), (0, 1, 2, 3)), "Escucha.")
    assert text == "Escucha.\nEste ejercicio necesita la vista interactiva."


def test_render_text_for_a_display_and_other_kinds() -> None:
    table = params_ok(
        ok(TABLE),
        ParamPair("table", "text_table", [["1810", "Grito"], ["1821", "Fin"]]),
        ParamPair("note", "text", "e|--0--|"),
    )
    assert render_text(ok(TABLE), table, Draw(), "La línea del tiempo.") == (
        "La línea del tiempo.\nTable:\n1810 | Grito\n1821 | Fin\nNote:\n```\ne|--0--|\n```"
    )
    numbers = ok(
        template(
            "numeric",
            param("xs", "number_list"),
            param("on", "boolean"),
            param("n", "integer"),
            param("k", "number", "answer_key"),
            param("tags", "text_list"),
        )
    )
    values = params_ok(
        numbers,
        ParamPair("xs", "number_list", [1.50, -0.0]),
        ParamPair("on", "boolean", False),
        ParamPair("n", "integer", 7),
        ParamPair("k", "number", 1),
        ParamPair("tags", "text_list", ["a", "b"]),
    )
    assert render_text(numbers, values, Draw(), "Calcula.") == (
        "Calcula.\nXs: 1.5, 0\nOn: no\nN: 7\nTags: a; b\nResponde con un número."
    )
    self_values = params_ok(ok(SELF), ParamPair("task", "text", "Toca."))
    assert render_text(ok(SELF), self_values, Draw(), "Practica.").endswith(
        "Cuando termines, di del 1 al 4 cómo te fue."
    )


# --- normalisation and numbers ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "text_match", "expected"),
    [
        (" ¿La  Cuenta? ", "fold_all", "la cuenta"),
        ("«Año»", "fold_all", "ano"),
        ("Año!", "keep_accents", "año"),
        ("ÉL", "keep_accents", "él"),
        ("  Print(x)  ", "exact_case", "Print(x)"),
        ("ﬁ  x", "exact_case", "fi x"),  # NFKC
        ("'hola'", "fold_all", "hola"),
        ("", "fold_all", ""),
    ],
)
def test_normalise_answer(text: str, text_match: Any, expected: str) -> None:
    assert normalise_answer(text, text_match) == expected


def test_normalise_answer_keeps_distinctions_where_asked() -> None:
    assert normalise_answer("año", "keep_accents") != normalise_answer("ano", "keep_accents")
    assert normalise_answer("año", "fold_all") == normalise_answer("ano", "fold_all")
    assert normalise_answer("Print(x)", "exact_case") != normalise_answer("print(x)", "exact_case")


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("3,14", Decimal("3.14")),
        ("3.14", Decimal("3.14")),
        ("-2", Decimal(-2)),
        ("+7", Decimal(7)),
        ("1/4", Decimal("0.25")),
        ("-3/4", Decimal("-0.75")),
        (" 0,125 ", Decimal("0.125")),
        (".5", Decimal("0.5")),
        ("1000", Decimal(1000)),
        ("1,0", Decimal(1)),
        ("2.718", Decimal("2.718")),  # a dot is always the decimal point
        ("0,125", Decimal("0.125")),
    ],
)
def test_parse_number_accepts(text: str, expected: Decimal) -> None:
    assert parse_number(text) == expected


@pytest.mark.parametrize(
    "text", ["1,000.5", "1,000", "12,500", "1/0", "abc", "", "1e3", "1 000", "1" * 41]
)
def test_parse_number_refuses(text: str) -> None:
    assert parse_number(text) is None


# --- answers and grading ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("kind", "data", "expected"),
    [
        ("choice_index", {"answer_kind": "choice_index", "option_index": 2}, ChoiceAnswer(2)),
        ("exact_text", {"answer_kind": "value", "value": "x"}, ValueAnswer("x")),
        ("exact_set", {"answer_kind": "values", "values": ["a", "b"]}, ValuesAnswer(("a", "b"))),
        ("exact_sequence", {"answer_kind": "sequence", "values": ["a"]}, SequenceAnswer(("a",))),
        ("numeric", {"answer_kind": "number", "value": "3,5"}, NumberAnswer(Decimal("3.5"))),
        ("self_report", {"answer_kind": "self_report", "rating": 3}, SelfReportAnswer(3)),
        (
            "self_report",
            {"answer_kind": "self_report", "rating": 4, "tempo_bpm": 80, "minutes": 10},
            SelfReportAnswer(4, 80, 10),
        ),
    ],
)
def test_parse_answer_shapes(kind: Any, data: dict[str, object], expected: object) -> None:
    assert parse_answer(kind, data) == expected


@pytest.mark.parametrize(
    ("kind", "data"),
    [
        ("choice_index", {"answer_kind": "value", "value": "x"}),
        ("choice_index", {"answer_kind": "choice_index", "option_index": -1}),
        ("choice_index", {"answer_kind": "choice_index", "option_index": True}),
        ("exact_text", {"answer_kind": "value", "value": "x" * 201}),
        ("exact_set", {"answer_kind": "values", "values": ["a"] * 25}),
        ("exact_set", {"answer_kind": "values", "values": "a"}),
        ("exact_sequence", {"answer_kind": "sequence", "values": ["a"] * 13}),
        ("self_report", {"answer_kind": "self_report", "rating": 5}),
        ("self_report", {"answer_kind": "self_report", "rating": 2, "tempo_bpm": 10}),
        ("none", {"answer_kind": "value", "value": "x"}),
    ],
)
def test_parse_answer_refuses_wrong_shapes(kind: Any, data: dict[str, object]) -> None:
    assert parse_answer(kind, data) == (FieldError("answer", "invalid"),)


def test_parse_answer_reports_an_unreadable_number_on_the_value() -> None:
    assert parse_answer("numeric", {"answer_kind": "number", "value": "1,000"}) == (
        FieldError("answer.value", "invalid"),
    )


def test_grade_authored_choice() -> None:
    values = params_ok(ok(CHOICE), *choice_params())
    assert grade(ok(CHOICE), values, Draw(), ChoiceAnswer(1)) == Graded(
        True, 100, {"why": "Porque sí."}
    )
    assert grade(ok(CHOICE), values, Draw(), ChoiceAnswer(0)) == Graded(
        False, 0, {"why": "Porque sí."}
    )
    assert grade(ok(CHOICE), values, Draw(), ChoiceAnswer(3)) == (FieldError("answer", "invalid"),)


def test_grade_choice_key_comes_from_the_draw() -> None:
    spec = ok(CHOICE_POOL)
    values = params_ok(spec, ParamPair("deck", "pool", entries(6)))
    shown = Draw(entries=(2, 0, 5, 1), order=(1, 3, 0, 2))  # the drawn entry is shown third
    graded = grade(spec, values, shown, ChoiceAnswer(2))
    assert graded == Graded(
        True, 100, {"explain": ["because 2", "because 0", "because 5", "because 1"]}
    )
    assert isinstance(grade(spec, values, shown, ChoiceAnswer(0)), Graded)
    assert grade(spec, values, shown, ChoiceAnswer(4)) == (FieldError("answer", "invalid"),)


def test_grade_exact_text_accepts_the_answer_and_its_alternatives() -> None:
    spec = ok(FLASHCARD)  # keep_accents
    pool = (
        PoolEntryInput("the bill", "la cuenta", ("cuenta",), "La cuenta, por favor."),
        *entries(2),
    )
    values = params_ok(spec, ParamPair("deck", "pool", pool))
    shown = Draw(entries=(0,))
    for sent, correct in (("¡La  Cuenta!", True), ("cuenta", True), ("la cuénta", False)):
        result = grade(spec, values, shown, ValueAnswer(sent))
        assert isinstance(result, Graded) and result.correct is correct
    authored = ok(template("exact_text", param("k", "text_list", "answer_key")))
    keys = params_ok(authored, ParamPair("k", "text_list", ["uno", "one"]))
    assert grade(authored, keys, Draw(), ValueAnswer("ONE")) == Graded(True, 100, {})


def test_grade_exact_set_gives_partial_credit_by_overlap() -> None:
    values = params_ok(
        ok(SET),
        ParamPair("question", "text", "q"),
        ParamPair("parts", "text_list", ["a", "b", "c"]),
    )
    cases = (
        (("c", "B", "a"), True, 100),
        (("a", "b"), False, 67),
        (("a", "b", "c", "d"), False, 75),
        (("x",), False, 0),
        (("a", "a", " "), False, 33),
    )
    for answer, correct, score in cases:
        assert grade(ok(SET), values, Draw(), ValuesAnswer(answer)) == Graded(correct, score, {})


def test_grade_exact_sequence_scores_by_longest_common_subsequence() -> None:
    values = params_ok(ok(STEPS), ParamPair("steps", "text_list", ["a", "b", "c", "d"]))
    cases = (
        (("a", "b", "c", "d"), True, 100),
        (("b", "a", "c", "d"), False, 75),
        (("d",), False, 25),
    )
    for answer, correct, score in cases:
        assert grade(ok(STEPS), values, Draw(), SequenceAnswer(answer)) == Graded(
            correct, score, {}
        )
    timeline = params_ok(ok(TIMELINE), ParamPair("deck", "pool", entries(6, ordered=True)))
    shown = Draw(entries=(5, 2, 4, 0), order=(0, 1, 2, 3))
    right = SequenceAnswer(("cue 0", "cue 2", "cue 4", "cue 5"))
    graded = grade(ok(TIMELINE), timeline, shown, right)
    assert isinstance(graded, Graded) and (graded.correct, graded.score) == (True, 100)


def test_grade_numeric_with_tolerance() -> None:
    spec = ok(NUMERIC)
    values = params_ok(
        spec,
        ParamPair("problem", "text", "pi"),
        ParamPair("answer", "number", 3.14),
        ParamPair("worked_solution", "text", "worked out"),
    )
    for sent, correct in (("3,14", True), ("3.15", True), ("3.151", False), ("157/50", True)):
        number = parse_number(sent)
        assert number is not None
        result = grade(spec, values, Draw(), NumberAnswer(number))
        assert result == Graded(correct, 100 if correct else 0, {"worked_solution": "worked out"})


def test_grade_self_report_and_wrong_shapes() -> None:
    values = params_ok(ok(SELF), ParamPair("task", "text", "Toca."))
    assert grade(ok(SELF), values, Draw(), SelfReportAnswer(3)) == Graded(None, 75, {})
    assert grade(ok(SELF), values, Draw(), ValueAnswer("x")) == (FieldError("answer", "invalid"),)
    table = params_ok(ok(TABLE), ParamPair("table", "text_table", [["a"]]))
    assert grade(ok(TABLE), table, Draw(), ValueAnswer("x")) == (FieldError("answer", "invalid"),)


def test_spec_hash_survives_the_json_round_trip() -> None:
    # Stored specs come back from JSON: number bounds must already be floats, integer bounds ints.
    for data in (NUMERIC, SELF, CHOICE, TABLE):
        spec = ok(data)
        stored = param_specs_from_json(json.loads(json.dumps(param_specs_to_json(spec.params))))
        assert spec_hash(dataclasses.replace(spec, params=stored)) == spec_hash(spec)
