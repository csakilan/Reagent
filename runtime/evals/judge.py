"""The LLM judge and its calibration. SPEC.md §7 layers 3 and "Evaluating the evaluator",
as overridden by FROZEN D19.

The judge asks ONE binary rubric question per case, compares the output against
the case's `reference` answer key, and takes a majority of `votes` calls. It runs
on a model from a different provider than the agent's, to limit self-preference.

Calibration lives only in `calibration_labels` (D19). For the current judge model
and each rubric (rubric_hash = SHA-256 of the rubric string) the judge's verdicts
on the labelled outputs are compared with the human verdicts, as accuracy and
Cohen's kappa, and recorded in `judge_calibration`. A rubric whose latest row has
fewer than `calibration_min_labels` labels or agreement below `min_agreement` is
uncalibrated: its judge results are reported but never block or approve.

With REAGENT_MODEL anything but `live`, a deterministic fake judge stands in, so
calibration and gating can be exercised without spending credits.
"""
from __future__ import annotations

import hashlib
import os
import re
from collections import Counter
from typing import Any

DEFAULTS = {"model": "claude-sonnet", "votes": 3, "min_agreement": 0.85, "calibration_min_labels": 30}

PROMPT = """You are grading one output of an AI agent. Answer the question about the output
with PASS or FAIL as the first word, then one sentence of reason.

Question: {rubric}
Answer key (what a correct output should say): {reference}

Output to grade:
<<<
{output}
>>>"""


def rubric_hash(rubric: str) -> str:
    return hashlib.sha256(rubric.encode("utf-8")).hexdigest()


def settings(cat: Any) -> dict[str, Any]:
    return {**DEFAULTS, **((cat.ent.get("evaluation") or {}).get("judge") or {})}


def _provider(openrouter_id: str | None) -> str:
    return (openrouter_id or "").split("/")[0]


def pick_model(cat: Any, agent_openrouter_id: str | None) -> str:
    """The configured judge, unless it shares the agent's provider; then another model."""
    want = settings(cat)["model"]
    models = cat.models
    if want in models and _provider(models[want]["spec"]["model"]) != _provider(agent_openrouter_id):
        return want
    for name in sorted(models):
        if _provider(models[name]["spec"]["model"]) != _provider(agent_openrouter_id):
            return name
    return want


# -- verdicts ------------------------------------------------------------------
def _words(s: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", (s or "").lower()) if len(w) > 3}


def fake_vote(rubric: str, reference: str, output: str) -> bool:
    """Deterministic stand-in: pass when the output shares enough of the answer key's words."""
    key = _words(reference) or _words(rubric)
    if not output or not key:
        return False
    return len(key & _words(output)) / len(key) >= 0.3


def _live_vote(model_id: str, cat: Any, rubric: str, reference: str, output: str) -> tuple[bool, float]:
    from langchain_openai import ChatOpenAI
    llm = ChatOpenAI(model=cat.models[model_id]["spec"]["model"], temperature=0, max_tokens=60,
                     base_url="https://openrouter.ai/api/v1", api_key=os.environ.get("OPENROUTER_API_KEY"),
                     timeout=60)
    msg = llm.invoke(PROMPT.format(rubric=rubric, reference=reference or "(none)", output=output[:6000]))
    text = str(msg.content).strip().upper()
    cost = float((msg.response_metadata or {}).get("cost") or 0.0)   # docs/research/openrouter.md
    return text.startswith("PASS"), cost


def judge(cat: Any, model_id: str, rubric: str, reference: str, output: str) -> dict[str, Any]:
    votes = int(settings(cat)["votes"])
    live = os.environ.get("REAGENT_MODEL", "fake").lower() == "live"
    results, cost = [], 0.0
    for _ in range(votes):
        if live:
            v, c = _live_vote(model_id, cat, rubric, reference, output)
            cost += c
        else:
            v = fake_vote(rubric, reference, output)
        results.append(v)
    passed = Counter(results).most_common(1)[0][0]
    return {"passed": passed, "votes": results, "model": model_id, "cost_usd": round(cost, 6),
            "fake": not live}


# -- calibration ------------------------------------------------------------------
def agreement_and_kappa(judge_says: list[bool], human_says: list[bool]) -> tuple[float, float | None]:
    n = len(human_says)
    if n == 0:
        return 0.0, None
    po = sum(j == h for j, h in zip(judge_says, human_says)) / n
    pj, ph = sum(judge_says) / n, sum(human_says) / n
    pe = pj * ph + (1 - pj) * (1 - ph)
    return round(po, 4), (None if pe >= 1 else round((po - pe) / (1 - pe), 4))


def _rubrics_by_case(c) -> dict[tuple[int | None, str], tuple[str, str]]:
    """(agent_version_id, case_name) -> (rubric, reference), plus enterprise defaults under None."""
    out: dict[tuple[int | None, str], tuple[str, str]] = {}
    for (spec,) in c.execute("SELECT spec FROM enterprise"):
        for case in ((spec.get("spec", {}).get("evaluation") or {}).get("default_cases") or []):
            if "judge" in case.get("expect", {}):
                out[(None, case["name"])] = (case["expect"]["judge"], case["expect"].get("reference", ""))
    for vid, spec in c.execute("SELECT id, spec FROM agent_versions"):
        for case in ((spec.get("spec", {}).get("evaluation") or {}).get("cases") or []):
            if "judge" in case.get("expect", {}):
                out[(vid, case["name"])] = (case["expect"]["judge"], case["expect"].get("reference", ""))
    return out


def recalibrate(cat: Any, url: str | None = None, agent_openrouter_id: str | None = None) -> dict[str, Any]:
    """Re-grade every label with the current judge and record one row per rubric."""
    import psycopg

    conf = settings(cat)
    model_id = pick_model(cat, agent_openrouter_id)
    by_rubric: dict[str, dict[str, Any]] = {}
    with psycopg.connect(url or os.environ.get("DATABASE_URL") or "", connect_timeout=15) as c:
        rubrics = _rubrics_by_case(c)
        for vid, case, output, human in c.execute(
                "SELECT agent_version_id, case_name, output_redacted, human_verdict FROM calibration_labels"):
            found = rubrics.get((vid, case)) or rubrics.get((None, case))
            if found is None:
                continue                         # a label for a case with no judge rubric
            rubric, reference = found
            v = judge(cat, model_id, rubric, reference, output)["passed"]
            slot = by_rubric.setdefault(rubric_hash(rubric), {"judge": [], "human": []})
            slot["judge"].append(v)
            slot["human"].append(bool(human))
        for h, s in by_rubric.items():
            agree, kappa = agreement_and_kappa(s["judge"], s["human"])
            c.execute("INSERT INTO judge_calibration (judge_model, rubric_hash, agreement, kappa, n_labels) "
                      "VALUES (%s, %s, %s, %s, %s)", (model_id, h, agree, kappa, len(s["human"])))
    all_j = [v for s in by_rubric.values() for v in s["judge"]]
    all_h = [v for s in by_rubric.values() for v in s["human"]]
    agree, kappa = agreement_and_kappa(all_j, all_h)
    return {"agreement": agree, "kappa": kappa, "n_labels": len(all_h), "judge_model": model_id,
            "calibrated": len(all_h) >= conf["calibration_min_labels"] and agree >= conf["min_agreement"],
            "rubrics": {h: {"n_labels": len(s["human"]),
                            "agreement": agreement_and_kappa(s["judge"], s["human"])[0]}
                        for h, s in by_rubric.items()}}


def calibration_state(cat: Any, model_id: str, url: str | None = None) -> dict[str, dict[str, Any]]:
    """rubric_hash -> latest calibration row for this judge model. Empty without a database."""
    url = url or os.environ.get("DATABASE_URL")
    if not url:
        return {}
    import psycopg
    conf = settings(cat)
    out: dict[str, dict[str, Any]] = {}
    with psycopg.connect(url, connect_timeout=15) as c:
        for h, agree, kappa, n in c.execute(
                "SELECT DISTINCT ON (rubric_hash) rubric_hash, agreement, kappa, n_labels "
                "FROM judge_calibration WHERE judge_model = %s ORDER BY rubric_hash, created_at DESC",
                (model_id,)):
            out[h] = {"agreement": float(agree), "kappa": None if kappa is None else float(kappa),
                      "n_labels": n, "calibrated": n >= conf["calibration_min_labels"]
                      and float(agree) >= conf["min_agreement"]}
    return out
