"""The validator: schema, then resolution, then eval coverage. `reagent validate`.

    python -m runtime.resolver.validate spec/examples          # a whole spec tree
    python -m runtime.resolver.validate path/to/draft.agent     # one draft against spec/examples

Exit 0 when everything passes, 1 on any rejection, 2 on a usage or I/O error.

Unlike spec/tools/check_examples.py, a pass here means the POLICY holds, not
just that the YAML parses:
  - JSON Schema, with errors that name the field;
  - the holes the schema cannot express: `mocks` keys must be real actions, and
    a case whose agent has sub-agents must mock `agent:<name>` (FROZEN D4);
  - full resolution against the enterprise and team layers (every merge rule);
  - stage-3 coverage, which now FAILS instead of printing (FROZEN D9). Inherited
    `default_cases` do not count toward `requirements.min_cases`;
  - a suite with no code check at all cannot gate (FROZEN D8).
Meta-agents and secret-scanner are seeded published and exempt from the
pipeline (FROZEN D8), so their coverage findings are advisory.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
from dataclasses import dataclass, field
from typing import Any

import yaml
from jsonschema import Draft202012Validator

from contracts.errors import Code, Rejection

from . import resolve_document
from .source import ROOT, Catalog, iter_documents, load_catalog

SCHEMA_PATH = ROOT / "spec" / "schema" / "reagent.schema.json"

#: FROZEN D8: seeded directly as published with origin 'platform'.
PLATFORM_SEEDED = {"agent-builder", "cost-optimizer", "evaluator", "failure-triage",
                   "security-reviewer", "secret-scanner"}
CODE_CHECKS = ("tool_calls", "no_tool_calls", "side_effects", "output_contains",
               "output_not_contains", "output_matches_schema")

_validator: Draft202012Validator | None = None


def schema_validator() -> Draft202012Validator:
    global _validator
    if _validator is None:
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        _validator = Draft202012Validator(schema)
    return _validator


def _dot(path) -> str:
    out = ""
    for p in path:
        out += f"[{p}]" if isinstance(p, int) else (f".{p}" if out else str(p))
    return out or "(document)"


def issue(code: Code, field_: str, layer: str = "default", *, message: str | None = None,
          attempted: Any = None, allowed: Any = None) -> dict[str, Any]:
    """A rejection dict. Codes with contract text keep it; the rest get a readable one."""
    d = Rejection(code, field_, layer, attempted, allowed).to_dict()
    if message is not None:
        d["message"] = message
    return d


def schema_issues(doc: Any) -> list[dict[str, Any]]:
    if not isinstance(doc, dict):
        return [issue(Code.SCHEMA_INVALID, "(document)",
                      message="The file is not a YAML mapping.")]
    out = []
    for e in sorted(schema_validator().iter_errors(doc), key=lambda e: list(e.absolute_path)):
        f = _dot(e.absolute_path)
        out.append(issue(Code.SCHEMA_INVALID, f, message=f"{f}: {e.message}"))
    return out


# -- the schema's holes ------------------------------------------------------
def case_issues(cat: Catalog, doc: dict[str, Any], resolved: dict[str, Any]) -> list[dict[str, Any]]:
    spec = doc.get("spec", {})
    subs = [s["ref"].partition("@")[0] for s in spec.get("sub_agents") or []]
    out = []
    for i, c in enumerate(dig_cases(spec)):
        base = f"spec.evaluation.cases[{i}]"
        mocks = c.get("mocks") or {}
        if not isinstance(mocks, dict):
            out.append(issue(Code.SCHEMA_INVALID, f"{base}.mocks",
                             message=f"{base}.mocks must map a qualified action to its tool result."))
            continue
        named = list(mocks) + list(c["expect"].get("tool_calls", [])) \
            + list(c["expect"].get("no_tool_calls", [])) \
            + [s.get("action", "") for s in c["expect"].get("side_effects", [])]
        for perm in named:
            if not known_action(cat, perm, subs):
                out.append(issue(Code.UNKNOWN_ACTION, base, attempted=perm,
                                 message=f"{base} names {perm}, which is not a registered "
                                         f"action or a declared sub-agent."))
        for s in subs:                                                   # FROZEN D4
            if f"agent:{s}" not in mocks:
                out.append(issue(Code.UNMOCKED_ACTION, f"{base}.mocks", attempted=f"agent:{s}"))
        if c["expect"].get("side_effects") and not mocks:
            out.append(issue(Code.UNMOCKED_ACTION, f"{base}.mocks",
                             message=f"{base} checks side_effects but has no mocked sandbox."))
    return out


def known_action(cat: Catalog, perm: str, subs: list[str]) -> bool:
    c, _, a = perm.partition(":")
    if c == "agent":
        return a in subs
    return a in cat.actions(c)


def dig_cases(spec: dict[str, Any]) -> list[dict[str, Any]]:
    return list((spec.get("evaluation") or {}).get("cases") or [])


# -- FROZEN D9: coverage is a failure ----------------------------------------
def requirements(cat: Catalog, team: str | None) -> dict[str, Any]:
    req = dict((cat.ent.get("evaluation") or {}).get("requirements") or {})
    mins = dict(req.get("min_cases") or {})
    treq = ((cat.team(team) or {}).get("evaluation") or {}).get("requirements") or {} if team else {}
    for k, v in (treq.get("min_cases") or {}).items():
        mins[k] = max(mins.get(k, 0), v)
    return {"min_cases": mins,
            "tool_coverage": bool(req.get("tool_coverage")) or bool(treq.get("tool_coverage"))}


def coverage_issues(cat: Catalog, doc: dict[str, Any], resolved: dict[str, Any]) -> list[dict[str, Any]]:
    spec, name = doc.get("spec", {}), doc["metadata"]["name"]
    own = dig_cases(spec)                     # inherited default_cases do NOT count
    req = requirements(cat, doc["metadata"].get("team"))
    out = []
    for kind, n in req["min_cases"].items():
        have = sum(1 for c in own if c.get("kind", "positive") == kind)
        if have < n:
            out.append(issue(Code.COVERAGE_UNMET, "spec.evaluation.cases", attempted=kind,
                             message=f"{name} has {have} {kind} case(s) of its own and needs {n}. "
                                     f"Inherited default_cases do not count."))
    if req["tool_coverage"]:
        seen: set[str] = set()
        for c in own:
            e = c["expect"]
            seen |= set(e.get("tool_calls", [])) | set(e.get("no_tool_calls", []))
            seen |= {s["action"] for s in e.get("side_effects", [])}
            seen |= set((c.get("mocks") or {}).keys())
        wanted = [t["permission"] for t in resolved["effective"]["tool_permissions"]]
        wanted += [f"agent:{s['name']}" for s in resolved["effective"]["sub_agents"]]
        for perm in dict.fromkeys(wanted):
            if perm not in seen:
                out.append(issue(Code.COVERAGE_UNMET, "spec.evaluation.cases", attempted=perm,
                                 message=f"No eval case of {name} exercises {perm}. Every action "
                                         f"the agent can call needs at least one case."))
    if own and not any(any(k in c["expect"] for k in CODE_CHECKS) for c in own):   # FROZEN D8
        out.append(issue(Code.NO_GATING_CHECKS, "spec.evaluation.cases"))
    if not own:
        out.append(issue(Code.NO_GATING_CHECKS, "spec.evaluation.cases"))
    return out


# -- reports -------------------------------------------------------------------
@dataclass
class Report:
    path: str
    kind: str | None
    name: str | None
    rejections: list[dict[str, Any]] = field(default_factory=list)
    advisories: list[dict[str, Any]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.rejections

    def to_dict(self) -> dict[str, Any]:
        return {"path": self.path, "kind": self.kind, "name": self.name, "valid": self.ok,
                "rejections": self.rejections, "advisories": self.advisories}


def validate_document(doc: Any, cat: Catalog, path: str = "<draft>") -> Report:
    kind = doc.get("kind") if isinstance(doc, dict) else None
    name = (doc.get("metadata") or {}).get("name") if isinstance(doc, dict) else None
    rep = Report(path, kind, name)
    rep.rejections += schema_issues(doc)
    if rep.rejections:
        return rep                           # nothing below is safe on a malformed doc
    if kind == "Agent":
        resolved = resolve_document(doc, catalog=cat, caller=None)
        rep.rejections += resolved["rejections"]
        found = case_issues(cat, doc, resolved) + coverage_issues(cat, doc, resolved)
        if name in PLATFORM_SEEDED and doc["metadata"].get("level") == "enterprise":
            rep.advisories += found
        else:
            rep.rejections += found
    elif kind in ("Team", "Enterprise"):
        rep.rejections += layer_issues(cat, doc)
    return rep


def layer_issues(cat: Catalog, doc: dict[str, Any]) -> list[dict[str, Any]]:
    """Check a layer on its own by resolving an empty agent beneath it."""
    if doc["kind"] == "Team":
        probe = {"metadata": {"name": "layer-probe", "version": "0.0.0", "level": "team",
                              "team": doc["metadata"]["name"]}, "spec": {}}
        prefix = f"teams/{doc['metadata']['name']}."
    else:
        probe = {"metadata": {"name": "layer-probe", "version": "0.0.0", "level": "enterprise",
                              "access": "private"}, "spec": {}}
        prefix = "enterprise."
    out = resolve_document(probe, catalog=cat, caller=None)["rejections"]
    return [r for r in out if r["field"].startswith(prefix)]


def validate_tree(root: pathlib.Path) -> list[Report]:
    cat = load_catalog(root)
    return [validate_document(doc, cat, str(p.relative_to(root.parent)))
            for p, doc in iter_documents(root)]


def validate_draft_text(text: str, cat: Catalog | None = None) -> dict[str, Any]:
    """POST /validate: raw YAML in, {valid, rejections} out."""
    cat = cat or load_catalog()
    try:
        doc = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        where = f"line {mark.line + 1}, column {mark.column + 1}" if mark else "unknown position"
        return {"valid": False, "rejections": [issue(
            Code.SCHEMA_INVALID, "(document)", message=f"The YAML does not parse at {where}.")]}
    rep = validate_document(doc, cat)
    return {"valid": rep.ok, "rejections": rep.rejections}


def _where(x: dict[str, Any]) -> str:
    """The message text is contract and printed verbatim; prefix the field when it lacks one."""
    return "" if x["field"] in x["message"] else f"[{x['field']}] "


def print_report(reports: list[Report], out=sys.stdout) -> None:
    for r in reports:
        print(("OK   " if r.ok else "FAIL ") + r.path, file=out)
        for x in r.rejections:
            print(f"    {x['code']}  {_where(x)}{x['message']}", file=out)
        for x in r.advisories:
            print(f"    advisory {x['code']}  {_where(x)}{x['message']} (seeded published, FROZEN D8)",
                  file=out)
    bad = sum(1 for r in reports if not r.ok)
    print(f"\n{len(reports) - bad} passed, {bad} failed", file=out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="reagent validate", description=__doc__.split("\n")[0])
    ap.add_argument("paths", nargs="+", help="a spec tree (like spec/examples) or .agent/.yaml files")
    ap.add_argument("--spec-root", help="the layers to resolve single files against")
    ap.add_argument("--json", action="store_true", help="print machine-readable reports")
    args = ap.parse_args(argv)

    reports: list[Report] = []
    try:
        for raw in args.paths:
            p = pathlib.Path(raw)
            if p.is_dir():
                reports += validate_tree(p.resolve())
                continue
            cat = load_catalog(args.spec_root)
            with open(p, encoding="utf-8") as fh:
                doc = yaml.safe_load(fh)
            reports.append(validate_document(doc, cat, str(p)))
    except (OSError, yaml.YAMLError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps([r.to_dict() for r in reports], indent=2))
    else:
        print_report(reports)
    return 0 if all(r.ok for r in reports) else 1


if __name__ == "__main__":
    sys.exit(main())
