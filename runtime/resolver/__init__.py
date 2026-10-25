"""The resolver. Owned by wf/resolver. Output contract: contracts/resolved_spec.schema.json.

    from runtime.resolver import resolve_agent
    doc = resolve_agent("pr-reviewer", caller="alice")
    if doc["rejections"]: ...            # the agent cannot run
    doc["effective"]["tool_permissions"]  # the runtime builds tools from this, and nothing else

`caller` is the AUTHENTICATED user from the session (FROZEN D2). Pass None or
"system" when the runtime itself resolves for a run: it sees real values,
including `effective.prompt`. A member gets enterprise leaves redacted (D3).
"""
from __future__ import annotations

import copy
from typing import Any

from .merge import Resolution
from .source import Catalog, load_catalog
from .visibility import redact_for_member

__all__ = ["resolve_agent", "resolve_document", "load_catalog", "Catalog"]


def resolve_document(doc: dict[str, Any], *, catalog: Catalog, caller: str | None = None,
                     overrides: dict[str, Any] | None = None, root_team: str | None = None,
                     trigger: str | None = None) -> dict[str, Any]:
    """Resolve an agent document (stored or draft) against the catalog's layers."""
    res = Resolution(catalog, copy.deepcopy(doc), caller=caller, root_team=root_team,
                     overrides=copy.deepcopy(overrides), trigger=trigger)
    out = res.run()
    if not res.is_admin:
        redact_for_member(out)
    return out


def resolve_agent(agent: str, caller: str | None = None, overrides: dict[str, Any] | None = None,
                  *, version: str | None = None, root_team: str | None = None,
                  trigger: str | None = None, catalog: Catalog | None = None,
                  draft: dict[str, Any] | None = None) -> dict[str, Any]:
    """Resolve a stored agent by name. Re-reads every layer on each call (SPEC.md §1).

    `root_team` carries the ROOT run's team down a sub-agent chain (FROZEN D1).
    `trigger` lets the resolver refuse `on_behalf_of` tools on triggers that
    have no acting user (FROZEN D2). `draft` resolves an unsaved document.
    """
    cat = catalog or load_catalog()
    if draft is not None:
        doc = draft
    else:
        versions = cat.agent_versions(agent)
        if not versions:
            raise LookupError(f"unknown agent {agent!r}")
        doc = versions[version] if version else cat.latest(agent)
        if doc is None:
            raise LookupError(f"unknown version {agent}@{version}")
    return resolve_document(doc, catalog=cat, caller=caller, overrides=overrides,
                            root_team=root_team, trigger=trigger)
