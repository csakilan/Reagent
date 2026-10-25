"""FROZEN D3: members never see enterprise values.

When the caller is not an enterprise admin, every leaf whose source is
`enterprise` keeps its source tag and loses its value. `narrowed_from` blocks
that point at the enterprise lose their value the same way. The assembled
prompt contains the enterprise prompt verbatim, so it goes too.
"""
from __future__ import annotations

from typing import Any


def _is_leaf(node: dict[str, Any]) -> bool:
    return "source" in node and "value" in node


def _redact_leaf(node: dict[str, Any]) -> None:
    nf = node.get("narrowed_from")
    if isinstance(nf, dict) and nf.get("from_layer") == "enterprise":
        node["narrowed_from"] = {"from_layer": "enterprise", "redacted": True}
    if node["source"] != "enterprise":
        return
    value = node["value"]
    if isinstance(value, dict) and "connector" in value and "actions" in value:
        # A tool leaf's value must stay an object under the contract, so keep
        # only the connector name. Enterprise agents are never served to members
        # by the API anyway (SPEC.md §2); this is the belt to that brace.
        node["value"] = {"connector": value["connector"], "actions": []}
    else:
        node["value"] = None
    node["redacted"] = True


def _walk(node: Any) -> None:
    if isinstance(node, dict):
        if _is_leaf(node):
            _redact_leaf(node)
            return
        for v in node.values():
            _walk(v)
    elif isinstance(node, list):
        for v in node:
            _walk(v)


def redact_for_member(doc: dict[str, Any]) -> dict[str, Any]:
    _walk(doc["provenance"])
    eff = doc["effective"]
    eff["prompt"] = None
    ent = eff.get("aggregate_limits", {}).get("enterprise")
    if ent:
        eff["aggregate_limits"]["enterprise"] = {k: None for k in ent}
    return doc
