"""The template guide, its worked examples and the bridge scripts (subjects spec 15.5, 15.6).

Package files under `tutor.templates`, read once and cached. The contract tables and the
practice-kind line are rendered from `tutor.domain.templates.contract_constants()` into
`{{PLACEHOLDER}}` tokens at load, so the guide never states a number the domain does not hold
(plan ruling 21). Reads files, so it lives outside the domain.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from functools import cache
from importlib.resources import files
from typing import Any

from tutor.domain.templates import (
    GUIDE_SECTIONS,
    TEMPLATE_GUIDE_VERSION,
    Channel,
    GuideSection,
    ParamPair,
    ParamSpecInput,
    PoolEntryInput,
    TemplateInput,
    contract_constants,
)

PACKAGE = "tutor.templates"
EXAMPLES: tuple[tuple[str, GuideSection], ...] = (
    ("pool_recall", "example_pool_recall"),
    ("numeric", "example_numeric"),
    ("ordering", "example_ordering"),
    ("pool_choice_media", "example_pool_choice_media"),
    ("display_table", "display_templates"),
)
BRIDGES: Mapping[str, str] = {"artifact": "bridge_artifact.js", "web": "bridge_web.js"}


@dataclass(frozen=True, slots=True)
class GuideExample:
    section: GuideSection
    name: str
    html: str
    spec: TemplateInput
    params: tuple[ParamPair, ...]
    prompt_text: str
    wire: Mapping[str, Any]


def _read(*parts: str) -> str:
    node = files(PACKAGE)
    for part in parts:
        node = node.joinpath(part)
    return node.read_text(encoding="utf-8")


def _table(header: tuple[str, ...], rows: Sequence[tuple[str, ...]]) -> str:
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    lines += ["| " + " | ".join(row) + " |" for row in rows]
    return "\n".join(lines)


def _code(value: str) -> str:
    return f"`{value}`"


def _placeholders() -> dict[str, Callable[[Mapping[str, Any]], str]]:
    def param_types(c: Mapping[str, Any]) -> str:
        rows = [(_code(k), v["value"], v["caps"]) for k, v in c["param_types"].items()]
        return _table(("param_type", "value", "caps"), rows)

    def roles(c: Mapping[str, Any]) -> str:
        return _table(
            ("param_role", "meaning"), [(_code(k), v) for k, v in c["param_roles"].items()]
        )

    def grading(c: Mapping[str, Any]) -> str:
        rows = [
            (_code(k), v["without_pool"], v["with_pool"], v["answer"], v["score"])
            for k, v in c["grading_kinds"].items()
        ]
        return _table(("grading_kind", "without a pool", "with a pool", "answer", "score"), rows)

    def text_match(c: Mapping[str, Any]) -> str:
        kinds = ", ".join(_code(k) for k in c["text_match_kinds"])
        rows = [(_code(k), v) for k, v in c["text_match"].items()]
        return f"Only for {kinds}.\n\n" + _table(("text_match", "steps"), rows)

    def draws(c: Mapping[str, Any]) -> str:
        counts = "; ".join(
            f"{_code(k)} pool_count {low}-{high}"
            for k, (low, high) in c["pool_count_ranges"].items()
        )
        low, high = c["pool_entries"]
        rows = [(_code(k), v["picks"], v["drawn"], v["key"]) for k, v in c["pool_draws"].items()]
        return (
            _table(("pool_draw", "picks", "drawn (sent)", "answer key"), rows)
            + f"\n\nA pool holds {low}-{high} entries; {counts}."
        )

    def practice(c: Mapping[str, Any]) -> str:
        return "; ".join(f"{_code(k)} when {v}" for k, v in c["practice_kinds"].items())

    def channels(c: Mapping[str, Any]) -> str:
        return "\n".join(f"- {_code(k)}: {v}" for k, v in c["channels"].items())

    def limits(c: Mapping[str, Any]) -> str:
        return (
            f"1-{c['max_params']} parameters, names {_code(c['param_name_pattern'])}, unique, "
            f"not {', '.join(_code(n) for n in c['reserved_param_names'])}; label 1-"
            f"{c['label_max']}; template name 1-{c['name_max']}, description up to "
            f"{c['description_max']}; prompt_text 1-{c['prompt_text_max']}. An instance's "
            f"parameters are at most {c['params_json_max_bytes']:,} bytes of JSON in all; prompt "
            f"parameters plus the largest possible draw at most "
            f"{c['public_payload_max_bytes']:,} bytes; numeric_tolerance 0-"
            f"{c['numeric_tolerance_max']:,}."
        )

    return {
        "PARAM_TYPES": param_types,
        "PARAM_ROLES": roles,
        "GRADING_KINDS": grading,
        "TEXT_MATCH": text_match,
        "POOL_DRAWS": draws,
        "PRACTICE_KINDS": practice,
        "CHANNELS": channels,
        "LIMITS": limits,
        "MARKER": lambda c: _code(c["marker"]),
        "MAX_TEMPLATE_BYTES": lambda c: f"{c['max_template_bytes']:,}",
        "NUMBERS": lambda c: c["numbers"],
        "PAYLOAD_KEYS": lambda c: ", ".join(_code(k) for k in c["payload_keys"]),
        "GUIDE_VERSION": lambda c: str(TEMPLATE_GUIDE_VERSION),
    }


def _render(text: str) -> str:
    constants = contract_constants()
    for name, render in _placeholders().items():
        token = "{{" + name + "}}"
        if token in text:
            text = text.replace(token, render(constants))
    return text


@cache
def guide_section(section: GuideSection) -> str:
    """One section of the guide with its generated tables rendered."""
    if section not in GUIDE_SECTIONS:
        raise LookupError(section)
    return _render(_read("guide", f"{section}.md")).strip()


@cache
def inline_guide() -> str:
    """The decision path alone, returned by list_templates on a subject with no active template
    (spec 15.6). The contract is read with get_template_guide after the learner's yes, before
    writing HTML (plan ruling 33)."""
    return guide_section("choosing_exercises")


@cache
def example_files() -> Mapping[str, str]:
    """Every file of `guide/examples` (HTML and sample JSON), by name, as text."""
    folder = files(PACKAGE).joinpath("guide", "examples")
    return {
        node.name: node.read_text(encoding="utf-8")
        for node in sorted(folder.iterdir(), key=lambda n: n.name)
        if node.is_file()
    }


def guide_digest(
    constants: Mapping[str, Any], sections: Mapping[str, str], examples: Mapping[str, str]
) -> str:
    """SHA-256 of the three parts as one sorted-key JSON document."""
    data = {"constants": constants, "sections": sections, "examples": examples}
    text = json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def guide_sha256() -> str:
    """What `TEMPLATE_CONTRACT_SHA256` must equal: the contract constants, every rendered guide
    section and every example file (plan rulings 21 and 31). Text is read with universal
    newlines, so a checkout's line endings do not change it."""
    sections: dict[str, str] = {section: guide_section(section) for section in GUIDE_SECTIONS}
    return guide_digest(contract_constants(), sections, example_files())


def _param_spec(data: Mapping[str, Any]) -> ParamSpecInput:
    return ParamSpecInput(
        name=data["name"],
        param_type=data["param_type"],
        param_role=data["param_role"],
        label=data["label"],
        required=data.get("required", True),
        min_items=data.get("min_items"),
        max_items=data.get("max_items"),
        max_length=data.get("max_length"),
        min=data.get("min"),
        max=data.get("max"),
    )


def template_input(spec: Mapping[str, Any]) -> TemplateInput:
    """A wire-shaped template spec (save_template fields) as the domain input."""
    return TemplateInput(
        name=spec["name"],
        description=spec.get("description"),
        template_purpose=spec["template_purpose"],
        grading_kind=spec["grading_kind"],
        params=tuple(_param_spec(p) for p in spec["params"]),
        text_match=spec.get("text_match"),
        numeric_tolerance=spec.get("numeric_tolerance"),
        pool_draw=spec.get("pool_draw"),
        pool_count=spec.get("pool_count"),
        needs_view=spec.get("needs_view", False),
        options_param=spec.get("options_param"),
    )


def param_pairs(params: list[Mapping[str, Any]]) -> tuple[ParamPair, ...]:
    """Wire-shaped `{name, <value field>}` pairs as domain ParamPairs."""
    pairs: list[ParamPair] = []
    for item in params:
        fields = [k for k in item if k != "name"]
        if len(fields) != 1:
            raise ValueError("a parameter needs exactly one value field")
        field = fields[0]
        value: object = item[field]
        if field == "pool":
            value = tuple(
                PoolEntryInput(
                    cue=e["cue"],
                    answer=e["answer"],
                    accept=tuple(e.get("accept", ())),
                    explain=e.get("explain"),
                    order=e.get("order"),
                )
                for e in item[field]
            )
        pairs.append(ParamPair(item["name"], field, value))
    return tuple(pairs)


@cache
def guide_examples() -> tuple[GuideExample, ...]:
    """The four worked exercise examples and the display example, with their sample instance."""
    examples: list[GuideExample] = []
    for name, section in EXAMPLES:
        wire = json.loads(_read("guide", "examples", f"{name}.json"))
        examples.append(
            GuideExample(
                section=section,
                name=name,
                html=_read("guide", "examples", f"{name}.html"),
                spec=template_input(wire["spec"]),
                params=param_pairs(wire["instance"]["params"]),
                prompt_text=wire["instance"]["prompt_text"],
                wire=wire,
            )
        )
    return tuple(examples)


@cache
def bridge(channel: Channel) -> str:
    """The server-owned bridge script for a channel; `app_frame` comes with slice C."""
    if channel not in BRIDGES:
        raise LookupError(channel)
    return _read(BRIDGES[channel])


__all__ = [
    "GuideExample",
    "bridge",
    "example_files",
    "guide_digest",
    "guide_examples",
    "guide_section",
    "guide_sha256",
    "inline_guide",
    "param_pairs",
    "template_input",
]
