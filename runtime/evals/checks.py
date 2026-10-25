"""Deterministic code checks. SPEC.md §7 layer 1, run in this order:

    tool_calls  no_tool_calls  side_effects  output_not_contains  output_matches_schema

plus `output_contains` (FROZEN D22). Same trace, same verdict, every time.

"Output" (D22) is `json.dumps(structured_output, sort_keys=True)` when the agent
declares `output`, else the final assistant text. `output_contains` needs EVERY
substring, `output_not_contains` forbids ANY, both case-sensitive, and
`args_contains` applies the same rule to the canonical JSON of call arguments.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from jsonschema import Draft202012Validator

ORDER = ("tool_calls", "no_tool_calls", "side_effects", "output_contains",
         "output_not_contains", "output_matches_schema")
TRAJECTORY = ("tool_calls", "no_tool_calls", "side_effects")


@dataclass
class Trace:
    """What one run did, as far as grading is concerned."""
    status: str
    calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)   # executed, in order
    denied: list[str] = field(default_factory=list)                          # attempted, blocked
    unmocked: list[str] = field(default_factory=list)
    text: str = ""
    cost_usd: float = 0.0
    latency_ms: int = 0
    error: str | None = None


def canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def structured(trace: Trace) -> Any:
    try:
        return json.loads(trace.text)
    except (TypeError, ValueError):
        return None


def output_string(trace: Trace, output_schema: dict[str, Any] | None) -> str:
    if output_schema is not None:
        data = structured(trace)
        if data is not None:
            return json.dumps(data, sort_keys=True)
    return trace.text or ""


def _subsequence(want: list[str], got: list[str]) -> bool:
    it = iter(got)
    return all(any(g == w for g in it) for w in want)


def run_checks(expect: dict[str, Any], trace: Trace,
               output_schema: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    called = [p for p, _ in trace.calls]
    text = output_string(trace, output_schema)
    for name in ORDER:
        if name not in expect:
            continue
        want = expect[name]
        if name == "tool_calls":
            ok = _subsequence(list(want), called)
            out[name] = {"passed": ok, "expected": want, "actual": called}
        elif name == "no_tool_calls":
            hit = [p for p in want if p in called]
            out[name] = {"passed": not hit, "called": hit, "blocked_attempts": trace.denied}
        elif name == "side_effects":
            results = []
            for se in want:
                args = [a for p, a in trace.calls if p == se["action"]]
                match = [a for a in args
                         if all(s in canonical(a) for s in se.get("args_contains", []))]
                need = se.get("count")
                ok = len(match) == need if need is not None else len(match) >= 1
                results.append({"action": se["action"], "passed": ok, "matched": len(match),
                                "expected_count": need})
            out[name] = {"passed": all(r["passed"] for r in results), "effects": results}
        elif name == "output_contains":
            missing = [s for s in want if s not in text]
            out[name] = {"passed": not missing, "missing": missing}
        elif name == "output_not_contains":
            # Never echo the forbidden string back into the report: it may be a secret.
            leaked = [i for i, s in enumerate(want) if s in text]
            out[name] = {"passed": not leaked, "leaked_indexes": leaked}
        elif name == "output_matches_schema":
            data = structured(trace)
            if output_schema is None:
                out[name] = {"passed": not want, "detail": "agent declares no output schema"}
            elif data is None:
                out[name] = {"passed": not want, "detail": "final output is not JSON"}
            else:
                errs = [e.message[:120] for e in Draft202012Validator(output_schema).iter_errors(data)]
                out[name] = {"passed": (not errs) == bool(want), "errors": errs[:3]}
    return out


def has_code_check(case: dict[str, Any]) -> bool:
    return any(k in case.get("expect", {}) for k in ORDER)


def reasons(checks: dict[str, dict[str, Any]], error: str | None = None) -> list[str]:
    """One plain sentence per failed check, for a CI-style 'why did this fail' line."""
    out: list[str] = []
    if error:
        out.append(error.split(": ", 1)[-1] if error.startswith("E_") else error)
    for name, v in checks.items():
        if v.get("passed") is not False:
            continue
        if name == "tool_calls":
            out.append(f"Expected the calls {v['expected']} in this order; the agent made {v['actual']}.")
        elif name == "no_tool_calls":
            out.append(f"The agent called {v['called']}, which this case forbids.")
        elif name == "side_effects":
            for e in v["effects"]:
                if not e["passed"]:
                    want = f"exactly {e['expected_count']}" if e["expected_count"] is not None else "at least one"
                    out.append(f"Expected {want} {e['action']} call containing the required text; "
                               f"{e['matched']} matched.")
        elif name == "output_contains":
            out.append(f"The output is missing {v['missing']}.")
        elif name == "output_not_contains":
            out.append(f"The output contains forbidden text (item {v['leaked_indexes']} of the list).")
        elif name == "output_matches_schema":
            out.append("The output does not match the agent's output schema: "
                       + (v.get("detail") or "; ".join(v.get("errors", []))) + ".")
        elif name == "judge":
            out.append("The calibrated judge said FAIL.")
    return out
