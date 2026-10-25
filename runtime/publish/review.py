"""Stages 2 and 3 without their meta-agents.

SPEC.md assigns stage 2 to `security-reviewer` and stage 3 to `evaluator`, both
invoked as a `system` principal (FROZEN D11). Both reach the platform through
the `platform` connector, which has no server yet, and CLAUDE.md's cut order
says to fold security-reviewer into the validator's output. So both stages run
deterministically here, with the same contract: stage 2 returns blocking and
advisory findings; stage 3 proposes cases only for what the suite does not yet
cover, and every proposal still goes to human review in stage 4.
"""
from __future__ import annotations

import re
from typing import Any

from runtime.resolver.validate import coverage_issues

BROAD = re.compile(r"^\*$|/\*$|^[^/]*\*$")


def security_review(doc: dict[str, Any], resolved: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """SPEC.md §10, security-reviewer's four checks."""
    spec = doc.get("spec", {})
    used = (spec.get("prompt") or {}).get("task", "")
    for c in (spec.get("evaluation") or {}).get("cases") or []:
        used += " " + " ".join(list(c.get("mocks") or {}) + list(c["expect"].get("tool_calls", []))
                               + [s["action"] for s in c["expect"].get("side_effects", [])])
    blocking, advisory = [], []
    for t in resolved["effective"]["tool_permissions"]:
        p = t["permission"]
        if t["risk"] == "destructive" and not t["requires_approval"]:
            blocking.append({"finding": "destructive_without_approval", "permission": p,
                             "message": f"{p} is destructive and runs without human approval."})
        elif t["risk"] == "write" and not t["requires_approval"]:
            advisory.append({"finding": "write_without_approval", "permission": p,
                             "message": f"{p} writes without human approval."})
        if t["connector"] != "memory" and p not in used and t["action"] not in used:
            advisory.append({"finding": "unused_permission", "permission": p,
                             "message": f"Neither the prompt nor any case uses {p}. Remove it?"})
        for k, pats in (t.get("resources") or {}).items():
            for pat in pats:
                if BROAD.search(pat):
                    advisory.append({"finding": "broad_resource_glob", "permission": p,
                                     "message": f"{p} is scoped to {k} {pat!r}, which is broad."})
        if t["auth"] == "on_behalf_of" and t["risk"] == "read" and t["connector"] != "platform":
            advisory.append({"finding": "on_behalf_of_where_service_would_do", "permission": p,
                             "message": f"{p} acts on behalf of the user. Would the service account do?"})
    return {"blocking": blocking, "advisory": advisory}


def generate_cases(cat: Any, doc: dict[str, Any], resolved: dict[str, Any],
                   rejected: dict[str, str]) -> list[dict[str, Any]]:
    """Propose one template case per coverage gap. A human fills in the answer key."""
    out: list[dict[str, Any]] = []
    subs = {f"agent:{s['name']}": {"findings": []} for s in resolved["effective"]["sub_agents"]}
    for gap in coverage_issues(cat, doc, resolved):
        want = gap.get("attempted")
        if gap["code"] != "E_COVERAGE_UNMET" or not want:
            continue
        if ":" in want:
            name = "exercises-" + re.sub(r"[^a-z0-9]+", "-", want.lower()).strip("-")
            case = {"name": name, "kind": "positive", "input": {},
                    "mocks": {want: {"ok": True}, **subs},
                    "expect": {"tool_calls": [want]}}
        else:
            name = f"generated-{want}-case"
            case = {"name": name, "kind": want, "input": {}, "mocks": dict(subs),
                    "expect": {"output_not_contains": ["REPLACE-ME: a string that must never appear"]}}
        out.append(case)
    from runtime.evals import case_hash
    return [c for c in out if case_hash(c, "generated") not in rejected]
