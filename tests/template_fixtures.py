"""Template fixtures shared by the repository contract, the service tests and the MCP tests.

Not collected (no test_ prefix). The answer-key, reveal and pool names and the pool's answers
and explanations are unique strings, so a test can prove none of them reaches a client
(plan ruling 25, Review Focus 7).
"""

from __future__ import annotations

import dataclasses
from typing import Any

from tutor.domain.html_guard import TEMPLATE_MARKER
from tutor.domain.templates import (
    TEMPLATE_GUIDE_VERSION,
    ParamPair,
    ParamSpecInput,
    PoolEntryInput,
    TemplateInput,
    TemplateSpec,
    validate_template,
)

# Strings that must never reach a client before grading.
KEY_NAME = "zq_answer_key"
REVEAL_NAME = "zq_reveal_text"
POOL_NAME = "zq_pool_entries"
HIDDEN_ANSWER = "ZQ-HIDDEN-ANSWER"
HIDDEN_EXPLAIN = "ZQ-HIDDEN-EXPLAIN"
HIDDEN_REVEAL = "ZQ-HIDDEN-REVEAL"
LEAKS = (KEY_NAME, REVEAL_NAME, POOL_NAME, HIDDEN_ANSWER, HIDDEN_EXPLAIN, HIDDEN_REVEAL)

HTML = (
    f"<!doctype html><html lang='es'><head><meta charset='utf-8'>{TEMPLATE_MARKER}"
    "<title>Plantilla</title><style>button { padding: 4px }</style></head><body><main>"
    "<p id='q'></p><button type='button' id='go'>Responder</button>"
    "<script>var p = JSON.parse(document.getElementById('tutor-params').textContent);"
    "document.getElementById('q').textContent = JSON.stringify(p.params);</script>"
    "</main></body></html>"
)


def param(name: str, ptype: str, role: str = "prompt", **changes: Any) -> ParamSpecInput:
    return dataclasses.replace(ParamSpecInput(name, ptype, role, f"Etiqueta {name}"), **changes)


FLASHCARD = TemplateInput(
    name="Tarjetas de vocabulario",
    description="Una palabra y su traducción.",
    template_purpose="exercise",
    grading_kind="exact_text",
    params=(param("hint", "text", required=False), param(POOL_NAME, "pool", "pool")),
    text_match="keep_accents",
    pool_draw="one",
)
NUMERIC = TemplateInput(
    name="Respuesta numérica",
    description=None,
    template_purpose="exercise",
    grading_kind="numeric",
    params=(
        param("problem", "text"),
        param(KEY_NAME, "number", "answer_key"),
        param(REVEAL_NAME, "text", "reveal"),
    ),
    numeric_tolerance=0.01,
)
CHOICE = TemplateInput(
    name="Opción múltiple",
    description="Una pregunta con opciones.",
    template_purpose="exercise",
    grading_kind="choice_index",
    params=(
        param("question", "text"),
        param("options", "text_list"),
        param(KEY_NAME, "integer", "answer_key"),
    ),
    options_param="options",
)
TABLE = TemplateInput(
    name="Tabla de la lección",
    description=None,
    template_purpose="display",
    grading_kind="none",
    params=(param("rows", "text_table"),),
)


def spec(data: TemplateInput) -> TemplateSpec:
    result = validate_template(data, TEMPLATE_GUIDE_VERSION)
    assert isinstance(result, TemplateSpec), result
    return result


def pool(count: int = 4) -> tuple[PoolEntryInput, ...]:
    """Entries whose answers and explanations hold the secret strings."""
    return tuple(
        PoolEntryInput(
            f"cue {i}",
            f"{HIDDEN_ANSWER} {i}",
            (f"{HIDDEN_ANSWER}-alt {i}",),
            f"{HIDDEN_EXPLAIN} {i}",
        )
        for i in range(count)
    )


def flashcard_params(count: int = 4) -> tuple[ParamPair, ...]:
    return (ParamPair(POOL_NAME, "pool", pool(count)),)


def numeric_params(answer: float = 4.0, problem: str = "2 + 2") -> tuple[ParamPair, ...]:
    return (
        ParamPair("problem", "text", problem),
        ParamPair(KEY_NAME, "number", answer),
        ParamPair(REVEAL_NAME, "text", HIDDEN_REVEAL),
    )


def choice_params(key: int = 1) -> tuple[ParamPair, ...]:
    return (
        ParamPair("question", "text", "¿Cuál?"),
        ParamPair("options", "text_list", ["uno", "dos", "tres"]),
        ParamPair(KEY_NAME, "integer", key),
    )


def table_params() -> tuple[ParamPair, ...]:
    return (ParamPair("rows", "text_table", [["1810", "Grito"], ["1821", "Fin"]]),)
