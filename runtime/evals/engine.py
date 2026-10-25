"""The eval engine. SPEC.md §7 as overridden by FROZEN D4, D8, D9, D15, D19, D22.

    run_evals("pr-reviewer", runs_per_case=5, fake_model=True) -> EvalReport

Every approved case plus inherited `default_cases` runs `runs_per_case` times in
a mocked sandbox through the real runtime. Code checks grade each run; the LLM
judge runs only when calibrated (D19), and an uncalibrated judge never gates.

A case with no code check cannot gate (D8): it is reported, but excluded from
the pass rate. A suite with no gating case at all is blocked with
E_NO_GATING_CHECKS rather than reporting a pass rate over an empty set.
"""
from __future__ import annotations

import contextlib
import copy
import hashlib
import json
import os
import time
from typing import Any, Iterator

from contracts.errors import Code, Rejection

from runtime.resolver import load_catalog, resolve_document

from .checks import TRAJECTORY, Trace, has_code_check, reasons, run_checks
from .sandbox import sandboxed
from .stats import percentile, rate, wilson

DEFAULT_RUNS = 5


# -- case identity, FROZEN D15 -------------------------------------------------
def case_hash(case: dict[str, Any], origin: str) -> str:
    """SHA-256 of the canonical case, `kind` and `origin` defaults materialised."""
    c = dict(case)
    c.setdefault("kind", "positive")
    c.setdefault("origin", origin)
    return hashlib.sha256(json.dumps(c, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def collect_cases(cat: Any, doc: dict[str, Any], include_inherited: bool = True) -> list[dict[str, Any]]:
    out = []
    for c in (doc.get("spec", {}).get("evaluation") or {}).get("cases") or []:
        out.append({"case_id": f"agent/{c['name']}", "origin": "authored", "case": c})
    if include_inherited:
        for c in (cat.ent.get("evaluation") or {}).get("default_cases") or []:
            out.append({"case_id": f"enterprise/{c['name']}", "origin": "inherited", "case": c})
        team = doc["metadata"].get("team")
        for c in ((cat.team(team) or {}).get("evaluation") or {}).get("default_cases") or [] if team else []:
            out.append({"case_id": f"team:{team}/{c['name']}", "origin": "inherited", "case": c})
    for item in out:
        item["case_hash"] = case_hash(item["case"], item["origin"])
        item["kind"] = item["case"].get("kind", "positive")
    return out


# -- judge, FROZEN D19 -----------------------------------------------------------
def judge_calibration(cat: Any, agent_openrouter_id: str | None) -> dict[str, Any]:
    """Which rubrics the current judge is calibrated on (D19). With no database
    there are no labels, so every rubric is uncalibrated by construction (D8)."""
    from .judge import calibration_state, pick_model, settings
    model = pick_model(cat, agent_openrouter_id)
    try:
        rubrics = calibration_state(cat, model)
    except Exception as e:  # noqa: BLE001 - a missing database must not fail an eval
        rubrics = {}
        reason = f"calibration unavailable: {type(e).__name__}"
    else:
        reason = None if rubrics else "no calibration labels"
    return {"model": model, "rubrics": rubrics, "reason": reason,
            "min_labels": settings(cat)["calibration_min_labels"],
            "min_agreement": settings(cat)["min_agreement"],
            "calibrated": any(r["calibrated"] for r in rubrics.values())}


# -- running ---------------------------------------------------------------------
@contextlib.contextmanager
def _model_mode(fake_model: bool | None) -> Iterator[None]:
    if fake_model is None:
        yield
        return
    old = os.environ.get("REAGENT_MODEL")
    os.environ["REAGENT_MODEL"] = "fake" if fake_model else "live"
    try:
        yield
    finally:
        if old is None:
            os.environ.pop("REAGENT_MODEL", None)
        else:
            os.environ["REAGENT_MODEL"] = old


def inherited_mock_mode() -> str:
    """FROZEN D4 fails any unmocked call, but the enterprise's default_cases carry
    no mocks: they are written once for every agent and cannot know its tools.
    Decided by the team (option b): an INHERITED case's unmocked calls get an
    empty result, and the report lists each one in `inert_mocks`. An agent's own
    cases stay strict. REAGENT_INHERITED_MOCKS=strict restores the literal rule."""
    return os.environ.get("REAGENT_INHERITED_MOCKS", "inert").lower()


def inert_mocks(resolved: dict[str, Any]) -> dict[str, Any]:
    out = {t["permission"]: {} for t in resolved["effective"]["tool_permissions"]}
    out.update({f"agent:{s['name']}": {} for s in resolved["effective"]["sub_agents"]})
    return out


def run_once(resolved: dict[str, Any], case: dict[str, Any], user: str | None,
             strip_mocks: list[str], origin: str = "authored") -> Trace:
    from runtime.engine import run_agent

    mocks = {k: v for k, v in (case.get("mocks") or {}).items() if k not in strip_mocks}
    if origin == "inherited" and inherited_mock_mode() == "inert":
        mocks = {**inert_mocks(resolved), **mocks}
    with sandboxed(resolved, mocks) as box:
        t0 = time.monotonic()
        r = run_agent(resolved["agent"], case.get("input"), user=user, trigger="system")
        latency = int((time.monotonic() - t0) * 1000)
    err = r.get("error")
    if box.unmocked:
        err = Rejection(Code.UNMOCKED_ACTION, field="mocks", blocked_by="default",
                        attempted=box.unmocked[0]).message
        err = f"{Code.UNMOCKED_ACTION.value}: {err}"
    return Trace(status=r.get("status", "failed"), calls=box.calls, denied=box.denied,
                 unmocked=box.unmocked, text=r.get("output") or "",
                 cost_usd=float(r.get("cost_usd") or 0.0), latency_ms=latency, error=err)


def grade(case: dict[str, Any], trace: Trace, output_schema: dict[str, Any] | None,
          calib: dict[str, Any], cat: Any = None) -> dict[str, Any]:
    """Code checks first. The judge runs only on a rubric it is calibrated for, and
    only then does its verdict count; otherwise it is reported as uncalibrated."""
    from .checks import output_string
    from .judge import judge, rubric_hash
    checks = run_checks(case.get("expect", {}), trace, output_schema)
    exp = case.get("expect", {})
    if "judge" in exp:
        state = calib["rubrics"].get(rubric_hash(exp["judge"]))
        if state and state["calibrated"] and cat is not None and trace.status == "succeeded":
            v = judge(cat, calib["model"], exp["judge"], exp.get("reference", ""),
                      output_string(trace, output_schema))
            trace.cost_usd += v["cost_usd"]
            checks["judge"] = {"passed": v["passed"], "gating": True, "votes": v["votes"],
                               "model": v["model"], "agreement": state["agreement"]}
        else:
            checks["judge"] = {"passed": None, "gating": False,
                               "detail": "uncalibrated: reported, never gates",
                               "n_labels": state["n_labels"] if state else 0}
    code_ok = all(v["passed"] for k, v in checks.items()
                  if k != "judge" or v.get("gating"))
    ran_ok = trace.status == "succeeded" and not trace.unmocked
    return {"passed": bool(code_ok and ran_ok), "checks": checks,
            "trajectory_ok": all(checks[k]["passed"] for k in TRAJECTORY if k in checks),
            "has_trajectory": any(k in checks for k in TRAJECTORY)}


# Case runs kept for reuse, keyed by (spec key, case hash). In-process only: it
# lets a pipeline reuse what ran during review; a restart simply runs them again.
_CACHE: dict[tuple[str | None, str], list[tuple[dict[str, Any], Any]]] = {}


def _spec_key(doc: dict[str, Any], k: int) -> str:
    """Everything that changes how a case runs: the spec minus its case list, k, model mode."""
    d = copy.deepcopy(doc)
    (d.get("spec", {}).get("evaluation") or {}).pop("cases", None)
    blob = json.dumps([d, k, os.environ.get("REAGENT_MODEL")], sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()


def run_evals(agent: str, runs_per_case: int | None = None, *, fake_model: bool | None = None,
              strip_mocks: list[str] | None = None, caller: str | None = None,
              overrides: dict[str, Any] | None = None, draft: dict[str, Any] | None = None,
              include_inherited: bool = True, case_ids: list[str] | None = None,
              catalog: Any = None, mutants: bool = False,
              on_case: Any = None, cache: bool = False) -> dict[str, Any]:
    """Run an agent's suite and return an EvalReport (contracts/openapi.yaml).

    on_case(entry, done, total) is called as each case finishes, so a caller can show
    results live. cache=True reuses a case's runs from an earlier call on the same
    agent spec, case hash, runs-per-case and model mode (the publish pipeline pre-runs
    cases while they are reviewed); an edited case has a new hash and runs again."""
    cat = catalog or load_catalog()
    doc = draft or cat.latest(agent)
    if doc is None:
        raise LookupError(f"unknown agent {agent!r}")
    resolved = resolve_document(doc, catalog=cat, caller=None, overrides=overrides)
    ev = doc.get("spec", {}).get("evaluation") or {}
    k = runs_per_case or ev.get("runs_per_case") or DEFAULT_RUNS
    report: dict[str, Any] = {
        "agent": agent, "version": str(doc["metadata"].get("version")), "kind": "ci",
        "runs_per_case": k, "pass_rate": 0.0, "pass_rate_ci_low": 0.0, "pass_rate_ci_high": 1.0,
        "pass_k": 0.0, "recall": None, "false_alarm_rate": None, "safety_pass_rate": None,
        "mutation_score": None, "tool_call_accuracy": None, "human_intervention_rate": None,
        "avg_cost": 0.0, "p95_latency": 0, "judge_calibrated": False, "gate_blocked": True,
        "gate_reasons": [], "error": None, "cases": [],
    }
    if resolved["rejections"]:
        report["error"] = "; ".join(f"{r['code']}: {r['message']}" for r in resolved["rejections"])
        report["gate_reasons"] = [r["code"] for r in resolved["rejections"]]
        return report

    calib = judge_calibration(cat, resolved["effective"]["model"].get("openrouter_model"))
    report["judge_calibrated"] = calib["calibrated"]
    report["judge"] = calib
    output_schema = doc.get("spec", {}).get("output")
    owners = (doc.get("spec", {}).get("ownership") or {}).get("owners") or [None]
    user = caller or owners[0]
    cases = collect_cases(cat, doc, include_inherited)
    if case_ids is not None:
        cases = [c for c in cases if c["case_id"] in case_ids]

    def counts(case: dict[str, Any]) -> bool:
        """A case counts toward the pass rate if it has a code check, or a judge
        rubric that is calibrated (SPEC.md §7). An uncalibrated judge never counts."""
        from .judge import rubric_hash
        rubric = case.get("expect", {}).get("judge")
        state = calib["rubrics"].get(rubric_hash(rubric)) if rubric else None
        return has_code_check(case) or bool(state and state["calibrated"])

    runs: list[tuple[dict[str, Any], dict[str, Any], Trace]] = []
    use_cache = cache and not strip_mocks and not overrides
    with _model_mode(fake_model):
        spec_key = _spec_key(doc, k) if use_cache else None
        for item in cases:
            gating = counts(item["case"])
            graded = []
            hit = _CACHE.get((spec_key, item["case_hash"])) if use_cache else None
            for idx in range(k):
                if hit is not None:
                    g, tr = hit[idx]
                else:
                    tr = run_once(resolved, item["case"], user, list(strip_mocks or []), item["origin"])
                    g = grade(item["case"], tr, output_schema, calib, cat)
                graded.append(g)
                runs.append((item, g, tr))
            if use_cache and hit is None:
                _CACHE[(spec_key, item["case_hash"])] = [(g, t) for i, g, t in runs if i is item]
            mine = [t for i, _, t in runs if i is item]
            errors = [t.error for t in mine if t.error]
            passes = sum(1 for g in graded if g["passed"])
            report["cases"].append({
                "case_id": item["case_id"], "origin": item["origin"], "kind": item["kind"],
                "case_hash": item["case_hash"], "gating": gating, "runs": k, "passes": passes,
                "passed": gating and passes == k, "error": errors[0] if errors else None,
                "checks": graded[-1]["checks"] if graded else {},
                "tool_calls": [p for p, _ in mine[-1].calls] if mine else [],
                "blocked_calls": list(mine[-1].denied) if mine else [],
                # CI-style detail: what went in, what came out, and why it failed.
                # Inherited cases keep their contents hidden (SPEC.md §11).
                "input": item["case"].get("input") if item["origin"] != "inherited" else None,
                "output": (mine[-1].text or "")[:4000] if mine else "",
                "reasons": reasons(graded[-1]["checks"], errors[0] if errors else None)
                           if graded and passes < k else [],
                "inert_mocks": (sorted(set(inert_mocks(resolved)) - set(item["case"].get("mocks") or {}))
                                if item["origin"] == "inherited" and inherited_mock_mode() == "inert"
                                else []),
            })
            if on_case is not None:
                on_case(report["cases"][-1], len(report["cases"]), len(cases))

    gate_runs = [(i, g, t) for i, g, t in runs if counts(i["case"])]
    n, p = len(gate_runs), sum(1 for _, g, _ in gate_runs if g["passed"])
    lo, hi = wilson(p, n)
    by_kind = lambda kind: [(g, t) for i, g, t in gate_runs if i["kind"] == kind]
    pos, neg, saf = by_kind("positive"), by_kind("negative"), by_kind("safety")
    traj = [g for _, g, _ in gate_runs if g["has_trajectory"]]
    gating_cases = [c for c in report["cases"] if c["gating"]]
    report.update({
        "pass_rate": rate(p, n) or 0.0, "pass_rate_ci_low": lo, "pass_rate_ci_high": hi,
        "pass_k": rate(sum(1 for c in gating_cases if c["passes"] == c["runs"]), len(gating_cases)) or 0.0,
        "recall": rate(sum(1 for g, _ in pos if g["passed"]), len(pos)),
        "false_alarm_rate": rate(sum(1 for g, _ in neg if not g["passed"]), len(neg)),
        "safety_pass_rate": rate(sum(1 for g, _ in saf if g["passed"]), len(saf)),
        "tool_call_accuracy": rate(sum(1 for g in traj if g["trajectory_ok"]), len(traj)),
        "avg_cost": round(sum(t.cost_usd for _, _, t in runs) / len(runs), 6) if runs else 0.0,
        "p95_latency": int(percentile([t.latency_ms for _, _, t in runs], 95)),
        "p95_latency_unit": "ms",
        "total_cost": round(sum(t.cost_usd for _, _, t in runs), 6),
    })
    # D8 + D9: only the agent's OWN cases can make a suite gateable. Inherited
    # default_cases always carry code checks, so counting them would let a
    # judge-only suite publish on the enterprise's tests alone.
    own_gating = sum(1 for i, _, _ in gate_runs if i["origin"] != "inherited")
    report.update(gate(report, ev.get("thresholds") or {}, own_gating))
    if mutants:
        report["mutation_score"] = None
        report["gate_reasons"].append("mutation testing is cut (CLAUDE.md §12, item 5)")
    return report


def gate(report: dict[str, Any], thresholds: dict[str, Any], gating_runs: int) -> dict[str, Any]:
    """Stage 6. Code checks only; an uncalibrated judge never blocks or approves."""
    reasons: list[str] = []
    error = report.get("error")
    if gating_runs == 0:
        error = f"{Code.NO_GATING_CHECKS.value}: " + Rejection(
            Code.NO_GATING_CHECKS, "spec.evaluation.cases", "default").message
        reasons.append(Code.NO_GATING_CHECKS.value)
    else:
        if "pass_rate" in thresholds and report["pass_rate"] < thresholds["pass_rate"]:
            reasons.append(f"pass_rate {report['pass_rate']:.2f} < {thresholds['pass_rate']}")
        if report["safety_pass_rate"] is not None and report["safety_pass_rate"] < 1.0:
            reasons.append(f"safety_pass_rate {report['safety_pass_rate']:.2f} < 1.0")
        if "max_usd_per_run" in thresholds and report["avg_cost"] > thresholds["max_usd_per_run"]:
            reasons.append(f"avg_cost {report['avg_cost']} > {thresholds['max_usd_per_run']}")
        if "max_p95_seconds" in thresholds and report["p95_latency"] / 1000 > thresholds["max_p95_seconds"]:
            reasons.append(f"p95 {report['p95_latency']}ms > {thresholds['max_p95_seconds']}s")
    return {"gate_blocked": bool(reasons), "gate_reasons": reasons, "error": error}
