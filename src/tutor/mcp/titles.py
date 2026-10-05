"""Restore field titles that FastMCP strips from published tool schemas.

FastMCP 4.0.11 removes every `title` from input and output schemas (it re-adds only top-level
input titles that differ from the parameter name). The contract wants a title on every field,
nested ones included, so this middleware copies them back from the Pydantic models.
"""

from __future__ import annotations

import copy
from collections.abc import Mapping, Sequence
from typing import Any

from fastmcp.server.middleware import Middleware, MiddlewareContext
from pydantic import BaseModel

_MAX_DEPTH = 12


def _resolve(node: Any, defs: Mapping[str, Any]) -> Any:
    seen = 0
    while isinstance(node, dict) and "$ref" in node and seen < _MAX_DEPTH:
        name = str(node["$ref"]).rsplit("/", 1)[-1]
        target = defs.get(name)
        if not isinstance(target, dict):
            break
        node = target
        seen += 1
    return node


def _walk(
    published: Any,
    reference: Any,
    pub_defs: Mapping[str, Any],
    ref_defs: Mapping[str, Any],
    depth: int = 0,
) -> None:
    if depth > _MAX_DEPTH or not isinstance(published, dict) or not isinstance(reference, dict):
        return
    published = _resolve(published, pub_defs)
    reference = _resolve(reference, ref_defs)
    if not isinstance(published, dict) or not isinstance(reference, dict):
        return
    ref_props = reference.get("properties", {})
    for name, prop in published.get("properties", {}).items():
        ref_prop = ref_props.get(name)
        if not isinstance(prop, dict) or not isinstance(ref_prop, dict):
            continue
        title = ref_prop.get("title")
        if isinstance(title, str):
            prop["title"] = title
        _walk(prop, ref_prop, pub_defs, ref_defs, depth + 1)
    pub_items, ref_items = published.get("items"), reference.get("items")
    if isinstance(pub_items, dict) and isinstance(ref_items, dict):
        _walk(pub_items, ref_items, pub_defs, ref_defs, depth + 1)
    for key in ("anyOf", "allOf", "oneOf"):
        pub_branches, ref_branches = published.get(key), reference.get(key)
        if (
            isinstance(pub_branches, list)
            and isinstance(ref_branches, list)
            and len(pub_branches) == len(ref_branches)
        ):
            for pub_branch, ref_branch in zip(pub_branches, ref_branches, strict=True):
                _walk(pub_branch, ref_branch, pub_defs, ref_defs, depth + 1)


def restore_titles(
    published: Mapping[str, Any], model: type[BaseModel], *, mode: str
) -> dict[str, Any]:
    """A copy of `published` with every property title the model defines put back."""
    result: dict[str, Any] = copy.deepcopy(dict(published))
    reference = model.model_json_schema(mode="validation" if mode == "input" else "serialization")
    pub_defs = result.get("$defs", {})
    ref_defs = reference.get("$defs", {})
    _walk(result, reference, pub_defs, ref_defs)
    for name, definition in pub_defs.items():
        _walk(definition, ref_defs.get(name, {}), pub_defs, ref_defs)
    return result


class TitleRestoreMiddleware(Middleware):
    """tools/list: put titles back on the input and output schemas of the known tools."""

    def __init__(
        self,
        inputs: Mapping[str, type[BaseModel]],
        outputs: Mapping[str, type[BaseModel]] | None = None,
    ) -> None:
        self._inputs = dict(inputs)
        self._outputs = dict(outputs or {})

    async def on_list_tools(self, context: MiddlewareContext[Any], call_next: Any) -> Any:
        tools: Sequence[Any] = await call_next(context)
        return [self._restored(tool) for tool in tools]

    def _restored(self, tool: Any) -> Any:
        update: dict[str, Any] = {}
        input_model = self._inputs.get(tool.name)
        if input_model is not None and tool.parameters:
            update["parameters"] = restore_titles(tool.parameters, input_model, mode="input")
        output_model = self._outputs.get(tool.name)
        if output_model is not None and tool.output_schema:
            update["output_schema"] = restore_titles(
                tool.output_schema, output_model, mode="output"
            )
        return tool.model_copy(update=update) if update else tool
