"""failure-triage, triggered when someone opens a failed or denied run (SPEC.md §10).

Groups a failure by cause (connector errors, budget cutoffs, permission
denials, model errors) from the run's error code and its trace, explains it in
one sentence, and proposes the spec fix. Deterministic: the evidence is the
recorded trace, so the explanation can be checked against it.
"""
from __future__ import annotations

import re
from typing import Any

#: error code -> (cause, explanation, what to change)
CAUSES: dict[str, tuple[str, str, str]] = {
    "E_TOOL_CALL_DENIED": ("permission denial", "The agent tried an action outside its resolved permissions, and the runtime blocked it.",
                           "If the action is needed, add it to the agent's tools (it must be granted to the team). If not, tighten the prompt so the agent does not attempt it."),
    "E_DENIED_ACTION": ("permission denial", "The action matches a deny rule, and deny always beats allow.",
                        "Remove the action from the agent's tools; a lower layer cannot override a deny rule."),
    "E_ACTION_NOT_GRANTED": ("permission denial", "The agent lists an action its team was never granted.",
                             "Remove it from tools, or request the connector action from enterprise admins."),
    "E_BUDGET_EXCEEDED": ("budget cutoff", "The run spent more than its per-run budget and was halted at a turn boundary.",
                          "Use a cheaper model the team allows, shorten the task, or raise budget.usd_per_run within the team's limit."),
    "E_AGGREGATE_BUDGET_EXCEEDED": ("budget cutoff", "A monthly budget for this agent, its team or the enterprise is used up.",
                                    "Wait for the monthly reset or ask an enterprise admin to raise the layer's usd_per_month."),
    "E_STEP_LIMIT_EXCEEDED": ("step limit", "The agent used every step it was allowed without finishing.",
                              "Make the task prompt more direct (call each tool once), or raise budget.max_steps within the limit above it."),
    "E_TIMEOUT": ("timeout", "The run hit its wall-clock timeout.",
                  "Check for a slow tool in the trace; raise budget.timeout_seconds only within the team's limit."),
    "E_ON_BEHALF_OF_NO_USER": ("trigger and auth mismatch", "A tool acts on behalf of the user, but this trigger has no acting user (FROZEN D2).",
                               "Switch that tool to auth: service, or run the agent from a trigger with a signed-in user."),
    "E_APPROVAL_REJECTED": ("approval rejected", "A person rejected a paused action, so it was not performed.",
                            "Read the rejected action's arguments in the trace; adjust the prompt if the agent asked for the wrong thing."),
    "E_APPROVAL_EXPIRED": ("approval expired", "Nobody answered the approval in time, and an expiry counts as a rejection.",
                           "Make sure an approver is available, or ask for a longer guardrails.approval.timeout_minutes within the limit above."),
    "E_CONNECTOR_ERROR": ("connector error", "A connector (MCP wrapper) failed or was unreachable.",
                          "Check the connector is running and its credentials are set; then rerun."),
    "E_CONNECTOR_DRIFT": ("connector error", "A granted action is missing from the connector's server: the wrapper and the registry drifted.",
                          "Restart the wrapper from the current registry, or regenerate contracts/connector_actions.json."),
    "E_MODEL_ERROR": ("model error", "The model call failed.",
                      "Set a fallback model the team allows, or retry; check the model ID in the registry."),
}


def triage(run: dict[str, Any], trace: dict[str, Any]) -> dict[str, Any]:
    status = run.get("status")
    if status not in ("failed", "denied"):
        return {"run_id": run.get("run_id"), "status": status, "failed": False}
    error = run.get("error") or ""
    codes = re.findall(r"\bE_[A-Z_]+\b", error)
    for r in run.get("rejections") or []:
        codes.append(r.get("code", ""))
    events = trace.get("events") or []
    denied = [e for e in events if e.get("type") == "tool_call" and e.get("allowed") is False]
    violations = [(e.get("payload_redacted") or {}) for e in events if e.get("type") == "policy_violation"]
    codes += [v.get("code", "") for v in violations]
    if denied and "E_TOOL_CALL_DENIED" not in codes:
        codes.append("E_TOOL_CALL_DENIED")
    code = next((c for c in codes if c in CAUSES), None)
    cause, explanation, fix = CAUSES.get(code, ("unclassified", "The run failed without a recognised error code.",
                                                "Read the error and the last events in the trace."))
    evidence = []
    if denied:
        evidence += [f"blocked {e.get('action')}" for e in denied[:3]]
    evidence += [v.get("message", "")[:160] for v in violations[:2] if v.get("message")]
    if error and not evidence:
        evidence.append(error[:200])
    return {"run_id": run.get("run_id"), "status": status, "failed": True, "code": code, "cause": cause,
            "explanation": explanation, "fix": fix, "evidence": evidence, "by": "failure-triage"}
