"""The publish pipeline. SPEC.md §11, FROZEN D8, D15, D17, D24.

 1 validate ─► 2 security ─► 3 generate ─► 4 review ─► 5 evals ─► 6 gate ─► 7 publish

One human gate: stage 4. `start()` runs 1-3 and stops at review. Each case is
then approved, edited or rejected against its server-side hash. `proceed()`
refuses with E_UNREVIEWED_CASES while anything is undecided, re-checks every
hash (an edit after approval voids it), then runs 5-7. Stage 7 re-validates
against the CURRENT layers and fails with E_LAYERS_CHANGED_MID_PIPELINE if
they moved and the draft no longer resolves.

Meta-agents and secret-scanner are seeded published (D8) and never enter here.
"""
from __future__ import annotations

import copy
import functools
import hashlib
import json
import os
from typing import Any

from contracts.errors import Code, ReAgentError, Rejection

from runtime.evals import case_hash, collect_cases, compare, run_evals
from runtime.resolver import load_catalog, resolve_document
from runtime.resolver.validate import PLATFORM_SEEDED, case_issues, coverage_issues, schema_issues

from .review import generate_cases, security_review
from .store import MemoryStore, now



def _default_store():
    """REAGENT_PUBLISH_STORE=db persists to Postgres; otherwise in memory."""
    if os.environ.get("REAGENT_PUBLISH_STORE", "memory").lower() == "db":
        from .pgstore import PgStore
        return PgStore()
    return MemoryStore()


STORE = _default_store()


def _persist(fn):
    """Save the run after every transition, whichever store holds it."""
    @functools.wraps(fn)
    def wrapper(*args, **kw):
        run = fn(*args, **kw)
        kw.get("store", STORE).save(run)
        return run
    return wrapper


def _layers_hash(cat: Any, doc: dict[str, Any]) -> str:
    team = doc["metadata"].get("team")
    blob = {"enterprise": cat.ent, "team": cat.team(team) if team else None}
    return hashlib.sha256(json.dumps(blob, sort_keys=True, default=str).encode()).hexdigest()


def _fail(run: dict[str, Any], stage: str, rejections: list[dict[str, Any]], store) -> dict[str, Any]:
    run["stage"] = stage
    run["stage_results"].setdefault(stage, {})
    run["stage_results"][stage].update({"passed": False, "rejections": rejections, "at": now()})
    store.finish(run, "failed")
    return run


def _pass(run: dict[str, Any], stage: str, **result: Any) -> None:
    run["stage"] = stage
    run["stage_results"][stage] = {"passed": True, "at": now(), **result}


def _rej(code: Code, field: str, attempted: Any = None, layer: str = "default",
         message: str | None = None) -> dict[str, Any]:
    """Codes with contract text keep it; codes without a template get a readable one."""
    d = Rejection(code, field, layer, attempted).to_dict()
    if message is not None:
        d["message"] = message
    return d



def _env_runs() -> int | None:
    raw = os.environ.get("REAGENT_PIPELINE_RUNS_PER_CASE", "").strip()
    if not raw:
        return None
    n = int(raw)
    if n < 1:
        raise ValueError("REAGENT_PIPELINE_RUNS_PER_CASE must be >= 1")
    return n

@_persist
def start(agent: str, started_by: str, draft: dict[str, Any] | None = None, *,
          store: MemoryStore = STORE, catalog: Any = None) -> dict[str, Any]:
    cat = catalog or load_catalog()
    doc = copy.deepcopy(draft or cat.latest(agent))
    if doc is None:
        raise LookupError(agent)
    if agent in PLATFORM_SEEDED and doc["metadata"].get("level") == "enterprise":
        raise ReAgentError(Rejection(Code.SCHEMA_INVALID, "agent", "enterprise", attempted=agent,
                                     extra={"message": f"{agent} is seeded published with origin "
                                            "'platform' and is exempt from the pipeline (FROZEN D8)."}))
    run = store.create_run(agent, doc, started_by)

    # 1 validate: schema + resolution against the current layers
    rej = schema_issues(doc)
    resolved = None if rej else resolve_document(doc, catalog=cat, caller=None)
    rej = rej or resolved["rejections"]
    if rej:
        return _fail(run, "validate", rej, store)
    run["layers_hash"] = _layers_hash(cat, doc)
    _pass(run, "validate", layers_hash=run["layers_hash"])

    # 2 security review
    found = security_review(doc, resolved)
    if found["blocking"]:
        run["stage_results"]["security"] = {"findings": found}
        return _fail(run, "security", [_rej(Code.SCHEMA_INVALID, "spec.tools",
                                            [f["permission"] for f in found["blocking"]],
                                            message=" ".join(f["message"] for f in found["blocking"]))],
                     store)
    _pass(run, "security", findings=found, acknowledged=not found["advisory"])

    # 3 generate tests: carry over approved cases, propose the gaps
    carried = store.approved_hashes(agent)
    for item in collect_cases(cat, doc):
        origin = item["origin"]
        decision = ("approved" if origin == "inherited" or item["case_hash"] in carried else None)
        run["cases"].append({"case_id": item["case_id"], "origin": origin, "kind": item["kind"],
                             "case_hash": item["case_hash"], "decision": decision,
                             "case": None if origin == "inherited" else item["case"],
                             "_case": item["case"], "reviewed_by": None})
    proposals = generate_cases(cat, doc, resolved, store.rejected.get(agent, {}))
    for c in proposals:
        run["cases"].append({"case_id": f"agent/{c['name']}", "origin": "generated",
                             "kind": c["kind"], "case_hash": case_hash(c, "generated"),
                             "decision": None, "case": c, "_case": c, "reviewed_by": None})
    _pass(run, "generate", proposed=[c["name"] for c in proposals],
          carried_over=sum(1 for c in run["cases"] if c["decision"] and c["origin"] != "inherited"))
    run["stage"] = "review"
    return run


@_persist
def acknowledge(run_id: int, user: str, *, store: MemoryStore = STORE) -> dict[str, Any]:
    """The author acknowledges stage 2's advisory findings."""
    run = store.get(run_id)
    run["stage_results"]["security"]["acknowledged"] = True
    run["stage_results"]["security"]["acknowledged_by"] = user
    return run


@_persist
def review(run_id: int, case_id: str, decision: str, user: str, *, edited_case: dict | None = None,
           reason: str | None = None, store: MemoryStore = STORE) -> dict[str, Any]:
    run = store.get(run_id)
    if run["status"] != "running" or run["stage"] != "review":
        raise ReAgentError(Rejection(Code.UNREVIEWED_CASES, "stage", "default", attempted=run["stage"],
                                     extra={"message": f"Pipeline {run_id} is not awaiting review."}))
    c = next((x for x in run["cases"] if x["case_id"] == case_id), None)
    if c is None:
        raise KeyError(case_id)
    if c["origin"] == "inherited":
        raise ReAgentError(Rejection(Code.UNREVIEWED_CASES, case_id, "enterprise", attempted=case_id,
                                     extra={"message": "Inherited default_cases are reviewed by "
                                            "enterprise admins once, when written."}))
    if decision == "reject":
        if not reason:
            raise ValueError("a rejection needs a reason; it is fed back to the evaluator")
        c.update(decision="rejected", reason=reason)
        store.remember_rejection(run["agent"], c["case_hash"], reason)
    elif decision == "edit":
        if not isinstance(edited_case, dict):
            raise ValueError("edit needs edited_case")
        origin = "authored" if c["origin"] == "authored" else "generated"
        c.update(decision="edited", case=edited_case, _case=edited_case,
                 case_hash=case_hash(edited_case, origin), kind=edited_case.get("kind", "positive"))
    elif decision == "approve":
        c["decision"] = "approved"
    else:
        raise ValueError(f"unknown decision {decision!r}")
    c["reviewed_by"], c["approved_hash"] = user, c["case_hash"]
    return run


@_persist
def proceed(run_id: int, *, store: MemoryStore = STORE, catalog: Any = None,
            fake_model: bool | None = None, runs_per_case: int | None = None) -> dict[str, Any]:
    run = store.get(run_id)
    if run["status"] != "running":
        return run
    open_ = [c["case_id"] for c in run["cases"] if c["decision"] is None]
    if open_:
        raise ReAgentError(Rejection(Code.UNREVIEWED_CASES, "cases", "default", attempted=len(open_)))
    if not run["stage_results"]["security"].get("acknowledged"):
        raise ReAgentError(Rejection(Code.UNREVIEWED_CASES, "security", "default", attempted=0, extra={
            "message": "Stage 2 advisory findings must be acknowledged by the author first."}))
    cat = catalog or load_catalog()
    kept = [c for c in run["cases"] if c["decision"] in ("approved", "edited")]
    for c in kept:                              # an edit after approval voids it (D15)
        origin = c["origin"] if c["origin"] != "inherited" else "inherited"
        if c["origin"] != "inherited" and case_hash(c["_case"], origin) != c.get("approved_hash", c["case_hash"]):
            return _fail(run, "review", [_rej(Code.CASE_HASH_MISMATCH, c["case_id"], c["case_id"])], store)
    _pass(run, "review", approved=len(kept),
          rejected=sum(1 for c in run["cases"] if c["decision"] == "rejected"))

    draft = copy.deepcopy(run["draft"])
    draft["spec"].setdefault("evaluation", {})["cases"] = [
        c["_case"] for c in kept if c["origin"] != "inherited"]
    resolved = resolve_document(draft, catalog=cat, caller=None)
    gaps = coverage_issues(cat, draft, resolved) + case_issues(cat, draft, resolved)
    if gaps:
        return _fail(run, "evals", gaps, store)

    # 5 evals: approved cases + inherited default_cases
    # REAGENT_PIPELINE_RUNS_PER_CASE overrides the agent's runs_per_case for the
    # pipeline only (a short live demo). The report records the number used.
    runs_per_case = runs_per_case or _env_runs()
    report = run_evals(run["agent"], runs_per_case, draft=draft, catalog=cat, fake_model=fake_model)
    run["report"] = report
    _pass(run, "evals", pass_rate=report["pass_rate"])

    # 6 gate: thresholds, safety at 100%, and no significant regression
    prev = store.latest_version(run["agent"])
    cmp = compare(prev["report"], report) if prev else None
    if report["gate_blocked"] or (cmp and cmp["regression"]):
        why = list(report["gate_reasons"]) + (["regression versus the previous version"]
                                               if cmp and cmp["regression"] else [])
        code = (Code.NO_GATING_CHECKS if "E_NO_GATING_CHECKS" in why else Code.THRESHOLD_NOT_MET)
        run["stage_results"]["gate"] = {"reasons": why, "comparison": cmp}
        msg = (None if code is Code.NO_GATING_CHECKS
               else "The eval report does not meet the gate: " + "; ".join(why) + ".")
        return _fail(run, "gate", [_rej(code, "spec.evaluation.thresholds", why, message=msg)], store)
    _pass(run, "gate", comparison=cmp)

    # 7 publish: re-run stage 1 against the layers as they are NOW (D17)
    cat_now = load_catalog() if catalog is None else catalog
    again = resolve_document(draft, catalog=cat_now, caller=None)
    if again["rejections"]:
        moved = _layers_hash(cat_now, draft) != run["layers_hash"]
        code = Code.LAYERS_CHANGED_MID_PIPELINE if moved else Code.SCHEMA_INVALID
        return _fail(run, "publish", [_rej(code, "layers")] + again["rejections"], store)
    pinned = {s["name"]: s["version"] for s in again["effective"]["sub_agents"]}
    for sa in draft["spec"].get("sub_agents") or []:          # D24: store exact name@x.y.z
        name = sa["ref"].partition("@")[0]
        if name in pinned:
            sa["ref"] = f"{name}@{pinned[name]}"
    version = {"agent": run["agent"], "version": run["draft_version"], "status": "published",
               "spec": draft, "sub_agents": again["effective"]["sub_agents"],   # D24: pinned exact
               "report": report, "published_at": now(), "publish_run_id": run["id"]}
    store.publish(run["agent"], version)
    _pass(run, "publish", version=run["draft_version"])
    store.finish(run, "succeeded")
    return run


@_persist
def abandon(run_id: int, *, store: MemoryStore = STORE) -> dict[str, Any]:
    run = store.get(run_id)
    if run["status"] == "running":
        store.finish(run, "abandoned")
    return run


#: What each stage is for, in the words the dashboard shows (SPEC.md §11).
STAGE_INFO = {
    "validate": ("Validate", "Checks the draft against the schema and resolves it against the "
                 "enterprise and team layers. Fails if any rule is widened."),
    "security": ("Security review", "Flags destructive actions without approval, unused "
                 "permissions, broad resource globs and on_behalf_of where service would do."),
    "generate": ("Generate tests", "Carries over approved cases and proposes new ones for any "
                 "coverage gap: every kind (positive, negative, safety) and every callable action."),
    "review": ("Human review", "A person approves, edits or rejects every new or changed case. "
               "Nothing publishes with unreviewed tests. This is the one human gate."),
    "evals": ("Run evals", "Runs every approved case, plus the enterprise's inherited cases, in a "
              "mocked sandbox. Code checks first, then the judge if it is calibrated."),
    "gate": ("Gate", "Publishes only if the pass rate meets the threshold, safety cases pass at "
             "100%, and there is no significant regression against the previous version."),
    "publish": ("Publish", "Re-validates against the layers as they are now, then creates an "
                "immutable version with the eval report attached."),
}


def _summary(run: dict[str, Any], stage: str) -> str:
    res = run["stage_results"].get(stage) or {}
    if res.get("rejections"):
        return res["rejections"][0]["message"]
    if stage == "validate" and res:
        return "the draft resolves against its enterprise and team layers with no rejections"
    if stage == "security" and res:
        f = res.get("findings") or {}
        return (f"{len(f.get('blocking', []))} blocking, {len(f.get('advisory', []))} advisory finding(s)"
                + ("" if res.get("acknowledged") else ", not yet acknowledged"))
    if stage == "generate" and res:
        return (f"{len(res.get('proposed', []))} case(s) proposed, "
                f"{res.get('carried_over', 0)} carried over from an earlier approval")
    if stage == "review":
        open_ = sum(1 for c in run["cases"] if c["decision"] is None)
        if open_:
            return f"Waiting for you: approve, edit or reject {open_} case(s), then continue."
        return f"{res.get('approved', 0)} approved, {res.get('rejected', 0)} rejected" if res else ""
    if stage == "evals" and run.get("report"):
        r = run["report"]
        return (f"pass rate {r['pass_rate']:.2f} [{r['pass_rate_ci_low']:.2f}, "
                f"{r['pass_rate_ci_high']:.2f}] over {r['runs_per_case']} run(s) per case")
    if stage == "gate" and res:
        return "thresholds met" if res.get("passed") else "; ".join(res.get("reasons", []))
    if stage == "publish" and res.get("passed"):
        return f"published {run['agent']}@{res.get('version')}"
    return ""


def steps(run: dict[str, Any]) -> list[dict[str, Any]]:
    """The pipeline as a GitHub-Actions-style step list."""
    from .store import STAGES
    out, reached = [], True
    for name in STAGES:
        title, what = STAGE_INFO[name]
        res = run["stage_results"].get(name)
        if res is not None and name != run["stage"]:
            status = "passed" if res.get("passed") else "failed"
        elif name == run["stage"]:
            status = {"failed": "failed", "succeeded": "passed", "abandoned": "abandoned"}.get(
                run["status"], "waiting" if name == "review" else "running")
            reached = False
        else:
            status = "pending" if not reached else "passed"
        out.append({"stage": name, "title": title, "what": what, "status": status,
                    "summary": _summary(run, name)})
    return out


def view(run: dict[str, Any]) -> dict[str, Any]:
    """The PublishRun shape (contracts/openapi.yaml). Inherited case bodies stay hidden."""
    out = {k: v for k, v in run.items() if k not in ("draft", "cases")}
    out["cases"] = [{k: v for k, v in c.items() if not k.startswith("_")} for c in run["cases"]]
    out["steps"] = steps(run)
    return out
