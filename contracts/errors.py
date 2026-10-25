"""ReAgent error codes. FROZEN CONTRACT.

SPEC.md §1: a config that tries to loosen a rule "fails with an error naming the
field and the layer that blocked it. It never silently clamps." Demo step 4 puts
that error on screen, so the message text is part of the contract, not an
implementation detail.

Owner: nobody. Shared. Do not add codes on a workflow branch; report instead.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any


class Code(str, enum.Enum):
    """Every way resolution or a run can be refused."""

    # --- Resolution: tighten-only violations (SPEC.md §1) --------------------
    WIDEN_ALLOWLIST      = "E_WIDEN_ALLOWLIST"       # child allows something the parent does not
    LIMIT_ABOVE_PARENT   = "E_LIMIT_ABOVE_PARENT"    # child raises a numeric limit
    SAFETY_FLAG_DISABLED = "E_SAFETY_FLAG_DISABLED"  # child turns off a flag that is on above
    LOCKED_PATH          = "E_LOCKED_PATH"           # child sets a path locked above (prefix match)
    RESOURCE_OUT_OF_SCOPE= "E_RESOURCE_OUT_OF_SCOPE" # child resource glob escapes the parent scope
    DENIED_ACTION        = "E_DENIED_ACTION"         # action matches the effective denylist

    # --- Resolution: grants and registry ------------------------------------
    CONNECTOR_NOT_GRANTED = "E_CONNECTOR_NOT_GRANTED"  # team was never granted this connector
    ACTION_NOT_GRANTED    = "E_ACTION_NOT_GRANTED"     # connector granted, action not
    UNKNOWN_CONNECTOR     = "E_UNKNOWN_CONNECTOR"
    UNKNOWN_ACTION        = "E_UNKNOWN_ACTION"
    UNKNOWN_MODEL         = "E_UNKNOWN_MODEL"
    MODEL_NOT_ALLOWED     = "E_MODEL_NOT_ALLOWED"
    TRIGGER_NOT_ALLOWED   = "E_TRIGGER_NOT_ALLOWED"
    AUTH_MODE_UNSUPPORTED = "E_AUTH_MODE_UNSUPPORTED"  # connector has no such auth mode
    PLATFORM_CONNECTOR_DENIED = "E_PLATFORM_CONNECTOR_DENIED"  # team agent asked for `platform`

    # --- Resolution: structure ----------------------------------------------
    SCHEMA_INVALID       = "E_SCHEMA_INVALID"
    UNKNOWN_TEAM         = "E_UNKNOWN_TEAM"
    UNKNOWN_BUCKET       = "E_UNKNOWN_BUCKET"
    BUCKET_REDEFINED     = "E_BUCKET_REDEFINED"      # audit finding 6: team redeclares an enterprise bucket
    BUCKET_ACCESS_DENIED = "E_BUCKET_ACCESS_DENIED"
    EPISODIC_WRITE_DENIED= "E_EPISODIC_WRITE_DENIED" # SPEC.md §9: runtime only
    SUBAGENT_NOT_CALLABLE= "E_SUBAGENT_NOT_CALLABLE" # SPEC.md §8.4
    SUBAGENT_CYCLE       = "E_SUBAGENT_CYCLE"
    FORK_OUTSIDE_TEAM    = "E_FORK_OUTSIDE_TEAM"

    # --- Run time -----------------------------------------------------------
    BUDGET_EXCEEDED      = "E_BUDGET_EXCEEDED"       # per-run usd cap hit at a turn boundary
    AGGREGATE_BUDGET_EXCEEDED = "E_AGGREGATE_BUDGET_EXCEEDED"  # a layer's monthly cap
    CONCURRENCY_EXCEEDED = "E_CONCURRENCY_EXCEEDED"
    STEP_LIMIT_EXCEEDED  = "E_STEP_LIMIT_EXCEEDED"
    TIMEOUT              = "E_TIMEOUT"
    CALL_DEPTH_EXCEEDED  = "E_CALL_DEPTH_EXCEEDED"
    APPROVAL_EXPIRED     = "E_APPROVAL_EXPIRED"      # audit finding 16: expiry DENIES
    APPROVAL_REJECTED    = "E_APPROVAL_REJECTED"
    TOOL_CALL_DENIED     = "E_TOOL_CALL_DENIED"      # wrap_tool_call blocked an ungranted call
    CONNECTOR_DRIFT      = "E_CONNECTOR_DRIFT"       # a granted action is missing from the MCP server
    ON_BEHALF_OF_NO_USER = "E_ON_BEHALF_OF_NO_USER"  # audit finding 2: no principal for a user-scoped tool
    MODEL_ERROR          = "E_MODEL_ERROR"
    CONNECTOR_ERROR      = "E_CONNECTOR_ERROR"

    # --- Evaluation and publish ---------------------------------------------
    UNMOCKED_ACTION      = "E_UNMOCKED_ACTION"       # audit finding 4: eval hit a tool with no mock
    NO_GATING_CHECKS     = "E_NO_GATING_CHECKS"      # audit finding 8: judge-only suite cannot gate
    COVERAGE_UNMET       = "E_COVERAGE_UNMET"        # stage 3 minimums or tool coverage
    UNREVIEWED_CASES     = "E_UNREVIEWED_CASES"      # SPEC.md §11: nothing publishes unreviewed
    CASE_HASH_MISMATCH   = "E_CASE_HASH_MISMATCH"    # case edited after approval
    THRESHOLD_NOT_MET    = "E_THRESHOLD_NOT_MET"
    PIPELINE_ALREADY_RUNNING = "E_PIPELINE_ALREADY_RUNNING"  # audit finding 17
    LAYERS_CHANGED_MID_PIPELINE = "E_LAYERS_CHANGED_MID_PIPELINE"  # stage 7 revalidates


#: How each code renders. `{field}`, `{layer}`, `{attempted}`, `{allowed}` are
#: filled from the Rejection. Keep these sentences: they appear on the demo
#: screen, and "names the field and the layer that blocked it" is the spec's own
#: acceptance criterion.
MESSAGES: dict[Code, str] = {
    Code.WIDEN_ALLOWLIST:
        "{field} is not allowed by {layer}. {layer} permits {allowed}; this asked for {attempted}. "
        "A lower layer can only narrow what a layer above it allows.",
    Code.LIMIT_ABOVE_PARENT:
        "{field} is {attempted}, above the limit of {allowed} set by {layer}. "
        "A lower layer may set a smaller value, never a larger one.",
    Code.SAFETY_FLAG_DISABLED:
        "{field} is switched on by {layer} and cannot be switched off below it.",
    Code.LOCKED_PATH:
        "{field} is locked by {layer} and cannot be set at a lower layer.",
    Code.RESOURCE_OUT_OF_SCOPE:
        "{field} includes {attempted}, which is outside the scope {allowed} granted by {layer}.",
    Code.DENIED_ACTION:
        "{field} matches a deny rule from {layer}. Deny always beats allow.",
    Code.CONNECTOR_NOT_GRANTED:
        "The {attempted} connector was never granted to this team. Ask an enterprise admin, "
        "or request it from the dashboard.",
    Code.ACTION_NOT_GRANTED:
        "{attempted} is not granted to this team. {layer} grants {allowed}.",
    Code.MODEL_NOT_ALLOWED:
        "Model {attempted} is not in the set allowed by {layer}: {allowed}.",
    Code.TRIGGER_NOT_ALLOWED:
        "Trigger type {attempted} is not allowed by {layer}.",
    Code.PLATFORM_CONNECTOR_DENIED:
        "The platform connector can only be granted at the enterprise level.",
    Code.BUCKET_REDEFINED:
        "Memory bucket {attempted} is already declared by {layer}. Bucket names are global "
        "and cannot be redeclared at a lower layer.",
    Code.EPISODIC_WRITE_DENIED:
        "Bucket {attempted} is episodic. Only the runtime writes episodic memory.",
    Code.SUBAGENT_NOT_CALLABLE:
        "Agent {attempted} cannot be called. Callable agents are those in your own team, "
        "or enterprise agents with access: service.",
    Code.BUDGET_EXCEEDED:
        "Run halted: spend reached ${attempted}, over the per-run cap of ${allowed} from {layer}.",
    Code.AGGREGATE_BUDGET_EXCEEDED:
        "Run refused: {layer} has spent ${attempted} of its ${allowed} monthly budget.",
    Code.TIMEOUT:
        "Run halted after {attempted}s, over the {allowed}s timeout from {layer}.",
    Code.APPROVAL_EXPIRED:
        "Approval for {attempted} was not answered within {allowed} minutes, so the action "
        "was denied and the run failed.",
    Code.TOOL_CALL_DENIED:
        "The agent tried to call {attempted}, which is not in its resolved permissions. "
        "The call was blocked and recorded.",
    Code.CONNECTOR_DRIFT:
        "The {layer} connector does not expose {attempted}, which this agent is granted. "
        "The wrapper and the registry have drifted.",
    Code.ON_BEHALF_OF_NO_USER:
        "{field} uses auth: on_behalf_of, but this run has no acting user. "
        "Schedule, webhook and agent triggers cannot act on behalf of a user.",
    Code.UNMOCKED_ACTION:
        "Eval case called {attempted} with no mock. Every action an eval case reaches must be "
        "mocked, including agent:<name> sub-agent calls.",
    Code.NO_GATING_CHECKS:
        "No case in this suite carries a code check, so there is nothing to gate on. "
        "A judge-only suite cannot publish.",
    Code.UNREVIEWED_CASES:
        "{attempted} case(s) are unreviewed. Nothing publishes with unreviewed tests.",
    Code.CASE_HASH_MISMATCH:
        "Case {attempted} changed after it was approved. Its approval is void and it needs "
        "reviewing again.",
    Code.PIPELINE_ALREADY_RUNNING:
        "A publish pipeline is already running for this agent.",
    Code.LAYERS_CHANGED_MID_PIPELINE:
        "The enterprise or team layer changed while this pipeline was running, and the draft "
        "no longer resolves. Start again.",
}


@dataclass(frozen=True, slots=True)
class Rejection:
    """One refusal. Serialises straight into `rejections[]` of the resolved spec."""

    code: Code
    field: str
    blocked_by: str                       # "enterprise" | "team:<name>" | "agent" | "default"
    attempted: Any = None
    allowed: Any = None
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def message(self) -> str:
        tmpl = MESSAGES.get(
            self.code, "{field} was rejected by {layer} ({code})."
        )
        return tmpl.format(
            field=self.field,
            layer=self.blocked_by,
            attempted=_fmt(self.attempted),
            allowed=_fmt(self.allowed),
            code=self.code.value,
        )

    def to_dict(self) -> dict[str, Any]:
        d = {
            "code": self.code.value,
            "field": self.field,
            "blocked_by": self.blocked_by,
            "message": self.message,
        }
        if self.attempted is not None:
            d["attempted"] = self.attempted
        if self.allowed is not None:
            d["allowed"] = self.allowed
        return d | self.extra


class ReAgentError(Exception):
    """Raised when a run cannot proceed. Carries the rejections for the API."""

    def __init__(self, *rejections: Rejection) -> None:
        self.rejections = list(rejections)
        super().__init__("; ".join(r.message for r in self.rejections))

    def to_dict(self) -> dict[str, Any]:
        return {"rejections": [r.to_dict() for r in self.rejections]}


def _fmt(v: Any) -> str:
    if v is None:
        return "nothing"
    if isinstance(v, (list, tuple, set)):
        return ", ".join(str(x) for x in v) or "nothing"
    return str(v)
