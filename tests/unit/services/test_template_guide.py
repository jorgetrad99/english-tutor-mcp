"""The template guide, its worked examples and the bridges (spec 15.6, plan ruling 21)."""

import re
from html.parser import HTMLParser
from uuid import UUID

import pytest

from tutor.domain.html_guard import MAX_TEMPLATE_BYTES, TEMPLATE_MARKER, check_template_html
from tutor.domain.templates import (
    ANSWER_KINDS,
    GRADING_KINDS,
    GUIDE_SECTIONS,
    INLINE_GUIDE_MAX_CHARS,
    PARAM_ROLES,
    PARAM_TYPES,
    POOL_DRAWS,
    TEMPLATE_CONTRACT_SHA256,
    TEMPLATE_GUIDE_VERSION,
    TEXT_MATCHES,
    TemplateSpec,
    compose_document,
    draw,
    encode_payload,
    public_payload,
    render_text,
    seed_for,
    validate_params,
    validate_template,
)
from tutor.services.template_files import (
    bridge,
    example_files,
    guide_digest,
    guide_examples,
    guide_section,
    guide_sha256,
    inline_guide,
    param_pairs,
)

pytestmark = pytest.mark.unit

INSTANCE = UUID("00000000-0000-0000-0000-0000000000ab")
FIELD_WORDS = re.compile(r"\b(pitch|hz|octave|interval)s?\b", re.IGNORECASE)
EXAMPLE_KINDS = {
    "example_pool_recall": "exact_text",
    "example_numeric": "numeric",
    "example_ordering": "exact_sequence",
    "example_pool_choice_media": "choice_index",
}


def test_guide_hash_covers_constants_sections_and_examples() -> None:
    # Plan rulings 21 and 31: any change to a contract constant, a rendered section or an
    # example file fails here until the version is bumped and the hash updated together.
    assert guide_sha256() == TEMPLATE_CONTRACT_SHA256, (
        "bump TEMPLATE_GUIDE_VERSION and the hash together"
    )


def test_guide_digest_changes_with_each_part() -> None:
    constants = {"a": 1}
    sections = {"contract": "text"}
    examples = {"x.html": "<p>"}
    base = guide_digest(constants, sections, examples)
    assert guide_digest({"a": 2}, sections, examples) != base
    assert guide_digest(constants, {"contract": "text."}, examples) != base
    assert guide_digest(constants, sections, {"x.html": "<p>."}) != base
    assert guide_digest(constants, sections, {"y.html": "<p>"}) != base


def test_example_files_are_every_file_of_the_examples_folder() -> None:
    names = set(example_files())
    for example in guide_examples():
        assert {f"{example.name}.html", f"{example.name}.json"} <= names
    assert len(names) == 2 * len(guide_examples())
    for section in GUIDE_SECTIONS:  # every section takes part through guide_sha256
        assert guide_section(section)


def test_every_section_loads() -> None:
    for section in GUIDE_SECTIONS:
        text = guide_section(section)
        assert text.startswith("# ")
        assert "{{" not in text and "}}" not in text  # every placeholder rendered


def test_section_names_name_no_field_of_study() -> None:
    # Contract review: the enum Claude sees is generic; the fields live in the example text.
    for section in GUIDE_SECTIONS:
        for word in ("language", "math", "history", "music", "flashcard", "timeline", "ear"):
            assert word not in section.split("_"), section


def test_unknown_section_is_refused() -> None:
    with pytest.raises(LookupError):
        guide_section("nope")  # type: ignore[arg-type]


def test_contract_names_everything_from_the_constants() -> None:
    contract = guide_section("contract")
    for name in (*PARAM_TYPES, *PARAM_ROLES, *GRADING_KINDS, *TEXT_MATCHES, *POOL_DRAWS):
        assert f"`{name}`" in contract, name
    for kind in ANSWER_KINDS:
        assert f'"answer_kind": "{kind}"' in contract, kind
    assert TEMPLATE_MARKER in contract
    assert f"{MAX_TEMPLATE_BYTES:,}" == "40,000" and "40,000" in contract
    assert "tutorSubmit" in contract and "tutorOnResult" in contract
    assert f"guide version {TEMPLATE_GUIDE_VERSION}" in contract


def test_choosing_exercises_carries_the_decision_path() -> None:
    text = guide_section("choosing_exercises")
    for step in range(1, 8):
        assert f"\n{step}. **" in text
    for kind in GRADING_KINDS:  # step 3 maps every kind once; the contract holds the table
        assert f"`{kind}`" in text
        assert f"- `{kind}`" not in text
    assert "Across subjects" not in text
    assert "get_template_guide('contract')" in text and "yes" in text


def test_the_guide_says_its_examples_are_illustrations_and_the_contract_is_neutral() -> None:
    # Plan ruling 40: the worked examples keep their fields on purpose; the guide says so
    # where it introduces them, and that the contract itself names no field.
    text = guide_section("choosing_exercises")
    assert "illustrations from different fields" in text
    assert "the contract itself is field-neutral" in text
    assert TEMPLATE_GUIDE_VERSION == 3


def test_inline_guide_is_the_decision_path_only_and_fits_the_cap() -> None:
    # Contract review: the contract is read after the learner's yes, before writing HTML.
    text = inline_guide()
    assert text == guide_section("choosing_exercises")
    assert guide_section("contract") not in text and "# Template contract" not in text
    assert len(text) <= INLINE_GUIDE_MAX_CHARS


def test_only_the_music_example_talks_about_field_specific_sound() -> None:
    # The generic-contract guard (spec 15.12).
    for section in GUIDE_SECTIONS:
        found = FIELD_WORDS.search(guide_section(section))
        if section == "example_pool_choice_media":
            assert found is not None
        else:
            assert found is None, (section, found)
    for example in guide_examples():
        if example.name != "pool_choice_media":
            assert FIELD_WORDS.search(example.html) is None, example.name


def test_examples_cover_the_four_fields_and_a_display() -> None:
    sections = [e.section for e in guide_examples()]
    assert sections == [*EXAMPLE_KINDS, "display_templates"]
    for example in guide_examples():
        if example.section in EXAMPLE_KINDS:
            kind = EXAMPLE_KINDS[example.section]
            assert example.spec.grading_kind == kind
            assert kind in guide_section(example.section)
            assert kind in example.html
        assert f"{example.name}.html" in guide_section(example.section)


class _Ids(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.ids: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.ids += [v for n, v in attrs if n == "id" and v]


@pytest.mark.parametrize("example", guide_examples(), ids=lambda e: e.name)
def test_example_validates_and_composes(example: object) -> None:
    from tutor.services.template_files import GuideExample

    assert isinstance(example, GuideExample)
    spec = validate_template(example.spec, TEMPLATE_GUIDE_VERSION)
    assert isinstance(spec, TemplateSpec), spec
    assert check_template_html(example.html) == ()
    assert example.html.startswith("<!doctype html>")
    assert len(example.html.encode("utf-8")) <= MAX_TEMPLATE_BYTES
    params = validate_params(spec, example.params, path="")
    assert isinstance(params, dict), params
    shown = draw(spec, params, seed_for(INSTANCE))
    payload = encode_payload(public_payload(INSTANCE, spec, params, shown))
    document = compose_document(example.html, payload, bridge("artifact"))
    found = _Ids()
    found.feed(document)
    assert found.ids.count("tutor-params") == 1 and found.ids.count("tutor-bridge") == 1
    text = render_text(spec, params, shown, example.prompt_text)
    assert text.startswith(example.prompt_text)
    if spec.purpose == "display":
        assert "tutorSubmit" not in example.html
    else:
        assert "tutorSubmit(" in example.html
    assert "innerHTML" not in example.html


def test_example_wire_shape_round_trips() -> None:
    for example in guide_examples():
        assert set(example.wire) == {"spec", "instance"}
        assert param_pairs(example.wire["instance"]["params"]) == example.params
        assert example.prompt_text == example.wire["instance"]["prompt_text"]


def test_param_pairs_need_exactly_one_value_field() -> None:
    with pytest.raises(ValueError, match="exactly one"):
        param_pairs([{"name": "x", "text": "a", "integer": 1}])


@pytest.mark.parametrize("channel", ["artifact", "web"])
def test_bridges_record_nothing_and_cannot_end_their_element(channel: str) -> None:
    script = bridge(channel)  # type: ignore[arg-type]
    assert "window.tutorSubmit" in script
    for forbidden in ("fetch", "XMLHttpRequest", "WebSocket", "postMessage", "sendBeacon"):
        assert forbidden not in script
    assert "</script" not in script.lower()
    assert "innerHTML" not in script
    assert "Tu respuesta" in script


def test_bridge_texts_per_channel() -> None:
    assert "Díselo a Claude." in bridge("artifact")
    assert "Claude" not in bridge("web")


def test_app_frame_bridge_comes_with_slice_c() -> None:
    with pytest.raises(LookupError):
        bridge("app_frame")
