"""Writing the layers above agents: enterprise, team, registry. SPEC.md §2 rows 8 and 9.

Only enterprise admins change these. Every change is checked first (the layer must
pass the schema and its own rules) and reports its IMPACT: which agents start or
stop resolving once it lands. Agents are not edited: SPEC.md §12 says a layer change
is not opt-in and applies on each agent's next run, so the impact list is how an
admin sees what a change will break before saving it.

Storage follows REAGENT_LAYERS: `db` writes the enterprise, teams and
registry_items rows; otherwise changes live in an in-process overlay on top of
the spec files, read by every load_catalog() call.
"""
from __future__ import annotations

import copy
import os
from typing import Any

from contracts.errors import Code, Rejection

from . import resolve_document
from .source import Catalog, load_catalog, overlay

KINDS = {"Enterprise", "Team", "Connector", "Model"}


def _db() -> bool:
    return os.environ.get("REAGENT_LAYERS", "files").lower() == "db"


def check_layer(doc: Any, kind: str, cat: Catalog) -> list[dict[str, Any]]:
    """Schema, then the layer's own tighten-only rules against everything above it."""
    from .validate import layer_issues, schema_issues
    if not isinstance(doc, dict) or doc.get("kind") != kind:
        return [Rejection(Code.SCHEMA_INVALID, "kind", "default", attempted=(doc or {}).get("kind") if isinstance(doc, dict) else None,
                          extra={"message": f"Expected a {kind} document."}).to_dict()]
    found = schema_issues(doc)
    if found or kind not in ("Team", "Enterprise"):
        return found
    trial = with_change(cat, doc)
    return layer_issues(trial, doc)


def with_change(cat: Catalog, doc: dict[str, Any]) -> Catalog:
    """A copy of the catalog with one layer document replaced or added."""
    new = copy.deepcopy(cat)
    name, kind = doc["metadata"]["name"], doc["kind"]
    if kind == "Enterprise":
        new.enterprise = copy.deepcopy(doc)
    elif kind == "Team":
        new.teams[name] = copy.deepcopy(doc)
    elif kind == "Connector":
        new.connectors[name] = copy.deepcopy(doc)
    elif kind == "Model":
        new.models[name] = copy.deepcopy(doc)
    return new


def impact(before: Catalog, after: Catalog, team: str | None = None) -> list[dict[str, Any]]:
    """Every affected agent, resolved under the old and the new layers."""
    out = []
    for name in sorted(after.agents):
        doc = after.latest(name)
        if doc is None or (team is not None and doc["metadata"].get("team") != team):
            continue
        old = resolve_document(doc, catalog=before, caller=None)["rejections"]
        new = resolve_document(doc, catalog=after, caller=None)["rejections"]
        out.append({"agent": name, "team": doc["metadata"].get("team"),
                    "before": "rejected" if old else "resolves",
                    "after": "rejected" if new else "resolves",
                    "changed": bool(old) != bool(new),
                    "rejections": new[:3]})
    # The ones that change status first: those are what the admin must see.
    return sorted(out, key=lambda r: (not r["changed"], r["after"] == "resolves", r["agent"]))


def save(doc: dict[str, Any]) -> None:
    kind, name = doc["kind"], doc["metadata"]["name"]
    if _db():
        import psycopg
        from psycopg.types.json import Jsonb
        with psycopg.connect(os.environ.get("DATABASE_URL") or "", connect_timeout=15) as c:
            if kind == "Enterprise":
                c.execute("UPDATE enterprise SET spec = %s, updated_at = now() WHERE name = %s", (Jsonb(doc), name))
            elif kind == "Team":
                done = c.execute("UPDATE teams SET spec = %s, updated_at = now() WHERE name = %s RETURNING id",
                                 (Jsonb(doc), name)).fetchone()
                if done is None:
                    c.execute("INSERT INTO teams (name, spec) VALUES (%s, %s)", (name, Jsonb(doc)))
            else:
                c.execute("INSERT INTO registry_items (kind, name, version, spec) VALUES (%s, %s, %s, %s) "
                          "ON CONFLICT (kind, name, version) DO UPDATE SET spec = EXCLUDED.spec",
                          (kind.lower(), name, str(doc["metadata"].get("version", "1.0.0")), Jsonb(doc)))
        return
    overlay(doc)


def apply(doc: dict[str, Any], *, dry_run: bool = False) -> dict[str, Any]:
    """Check a layer document, report its impact, and save it unless dry_run."""
    cat = load_catalog()
    kind = doc.get("kind") if isinstance(doc, dict) else None
    if kind not in KINDS:
        return {"saved": False, "rejections": [Rejection(Code.SCHEMA_INVALID, "kind", "default", attempted=kind,
                extra={"message": "Expected an Enterprise, Team, Connector or Model document."}).to_dict()], "impact": []}
    found = check_layer(doc, kind, cat)
    if found:
        return {"saved": False, "rejections": found, "impact": []}
    after = with_change(cat, doc)
    scope = doc["metadata"]["name"] if kind == "Team" else None
    report = {"saved": False, "dry_run": dry_run, "rejections": [], "impact": impact(cat, after, scope)}
    if not dry_run:
        save(doc)
        report["saved"] = True
    return report
