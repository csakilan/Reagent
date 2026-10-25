# Frozen decisions

An adversarial audit of `spec/SPEC.md`, the JSON Schema, the validator and all
19 examples found 25 places where the spec is ambiguous enough that four agents
would each resolve it differently. Each one has **one answer** below.

**These override `SPEC.md` where they conflict.** `SPEC.md` stays as written so
the pitch and the report still match it; this file is what the code does.

If you think one of these is wrong, say so in your report. Do not quietly
implement something else.

How the audit was run, for the record: the validator was pointed at a mutated
copy of the examples with enterprise-locked `redaction` and `audit_log` switched
off, `approval.require_for_risk` cleared, the enterprise `enterprise-kb` bucket
redeclared as `write: ["*"]`, and a widened repo scope. **It printed `ALL PASS`.**
Treat `ALL PASS` as "the YAML parses", not "the policy holds".

---

## Blockers

**D1. Enterprise `service` and `run_only` agents are scoped to the caller's team.**
§2 says an enterprise agent's effective tools are its own ∩ the caller's team
grants. §8.1 says the opposite for the same agents: "it never inherits the
caller's connectors." Since any team agent may call a `service` agent, §8.1 lets
a People Ops agent reach enterprise-wide GitHub through `secret-scanner`.
**Rule:** every enterprise-level agent, whatever its `access`, resolves to
`own tools ∩ team grants of the team that owns the ROOT run`, computed once from
the root run and carried down the whole sub-agent chain. `service` gets no
exemption.
*Breaks: runtime and resolver independently, in opposite directions.*

**D2. The acting user comes from the session, never the request body.**
§14 defines `POST /runs` as taking `{input, user}`, and `on_behalf_of` intersects
with "the triggering user's own access". A caller-supplied `user` field means any
member can post `{"user": "alice"}` and borrow alice's token scope.
**Rule:** the `user` field is removed from the request body. The principal comes
only from the session token. A run whose resolved spec contains any
`auth: on_behalf_of` tool is refused with `E_ON_BEHALF_OF_NO_USER` when the
trigger is `schedule`, `github`, `slack` or `agent` with no inherited root user.
*Breaks: all four.*

**D3. The resolved view has a fixed shape, and enterprise values are redacted for members.**
§1 defines it in one sentence and §2 separately forbids members from seeing the
enterprise layer, which the merged document contains.
**Rule:** `contracts/resolved_spec.schema.json`. Every leaf is
`{"value", "source"}`, lists are tagged per element, the prompt is an ordered
array of leaves, and when the caller is not an enterprise admin every leaf whose
source is `enterprise` has `value: null` and `redacted: true` with the source tag
kept. The document carries both `provenance` (tree, for the dashboard) and
`effective` (flat, for the runtime), and they must agree.
*Breaks: all four. This is H0 and H1.*

**D4. `mocks` values are verbatim MCP tool results, and sub-agent calls must be mocked.**
The schema types `mocks` as a bare object and the examples use four incompatible
value shapes. `pr-reviewer` expects a call to `agent:secret-scanner` with no mock
for it, so an eval either spends real money on a nondeterministic sub-agent or
fails its own trajectory assertion.
**Rule:** `mocks[<qualified action>]` is the verbatim tool-result payload
returned to **every** call of that action in that case, with no argument
matching. A case whose agent declares `sub_agents` **must** mock `agent:<name>`.
An unmocked call during an eval fails the case with `E_UNMOCKED_ACTION`.
*Breaks: resolver, runtime, data, dashboard.*

**D5. `locked` is a prefix lock, and it is actually enforced.**
§1 gives it one line, the schema lets an agent override a locked path, and the
validator's only locked check is a bare `pass` statement.
**Rule:** `locked` entries are dot paths interpreted as **prefix** locks. Any
lower layer that sets that path or any descendant fails with
`E_LOCKED_PATH(path, locking_layer)`. Locks union across layers and can never be
removed below.
*Breaks: resolver leaves the stub, runtime honestly disables redaction.*

**D6. Memory bucket names are globally unique and immutable.**
The validator does `buckets[name] = (team, bucket)`, so a team redeclaring
`enterprise-kb` with `write: ["*"]` wins and turns an admin-only bucket
world-writable. Confirmed: the schema accepts it and the validator passes it.
**Rule:** names are global and immutable once declared. A team declaring a name
the enterprise already declared fails with `E_BUCKET_REDEFINED`. A
team-declared bucket's `read`/`write` may name only that team, never `"*"` or
another team. Enforced in the database by `UNIQUE (name)`.
*Breaks: resolver, data (unique index or not), runtime, dashboard.*

**D7. `usd_per_month` and `max_concurrent_runs` are aggregate, not per-run minimums.**
§1 says numeric limits resolve by minimum, which is actively wrong for
aggregates: a team capped at 5 concurrent runs with four agents each legally at
5 runs 20 at once.
**Rule:** `usd_per_run`, `max_steps`, `timeout_seconds`, `max_call_depth` are
per-run and follow the minimum rule, and live in `effective.limits`.
`usd_per_month` and `max_concurrent_runs` are **aggregate**, enforced at every
layer independently by summing the `runs` table, and live in
`effective.aggregate_limits`. A run is refused when any layer is exhausted.
*Breaks: runtime counts per agent, resolver validates as a ceiling, dashboard sums per team.*

**D8. Meta-agents are seeded published, and a judge-only suite cannot publish.**
On a fresh install there are zero calibration labels, so the judge is
uncalibrated by construction and cannot gate. `code-reviewer`'s only case is
judge-only, so its pass rate is computed over an empty set. Separately, stages 2,
3 and 5 require the `security-reviewer` and `evaluator` agents, which are agents,
so each would have to pass a stage that needs itself.
**Rule:** meta-agents and `secret-scanner` are seeded directly as `published`
with `origin: 'platform'` and are exempt from the pipeline. Stage 6 fails with
`E_NO_GATING_CHECKS` whenever no case carries at least one code check.
*Breaks: resolver, dashboard, data, runtime.*

---

## High

**D9. Coverage gaps are a non-zero exit.** The validator computes stage-3 gaps and
prints them as information, then exits 0, so `ALL PASS` is printed while 8 of 9
examples would fail stage 3. Coverage gaps now exit non-zero, inherited
`default_cases` do **not** count toward `requirements.min_cases`, and the example
agents need their missing negative and per-action cases.

**D10. Every allowlist is a true intersection.** §1 says intersection; the
validator does last-layer-wins (`if "triggers" in l: allowed = l["triggers"]`),
and team `triggers` are never checked against the enterprise at all. All
allowlists intersect across all three layers, an omitted allowlist means
"inherit the parent set unchanged", and a child element matching nothing in the
parent fails rather than being dropped.

**D11. Pipeline stages invoke meta-agents as a `system` principal.** `evaluator`
and `security-reviewer` are `access: run_only`, and §8.4 makes only team agents
and `service` agents callable, so their declared `agent` triggers are dead and
the pipeline has no invocation path. Stages call them through a `system`
principal, the `agent` trigger is removed from the three `run_only` meta-agents,
and §8.4 stays as written. `runs.trigger` gains `'system'`.

**D12. `memory` expands into `tools` before any check runs.** §9 makes `memory`
shorthand for memory-connector permissions, which bypasses §6's "an agent gets
no tool permissions it doesn't list" and escapes `tool_coverage`. The resolver
expands each entry into a synthetic `tools` entry (`connector: memory`, actions
by mode, `resources: {bucket: [name]}`, `synthetic: true`) in the resolved spec,
before coverage or least-privilege checks. `tools` in the resolved view is
always the complete allowlist.

**D13. One effective denylist, and deny beats allow.** `connectors.<name>.deny`
holds bare globs, `guardrails.deny` holds qualified strings, nothing defines
precedence, and the validator never reads `guardrails.deny` at all. Both
normalise to qualified `<connector>:<action>` globs, union across all three
layers into `effective.denylist`, deny always wins, and the resolver rejects at
publish any `tools` entry matching it.

**D14. Notifications are a platform function, not an agent tool.** Both flagship
examples notify Slack channels they hold no permission for, so either alerts
bypass least privilege or the demo agents silently fail to notify. Delivery is
a platform function; `slack:` notify targets must fall inside the **team's**
resolved `slack.resources.channel` scope and are validated at publish; the agent
needs no `slack` grant.

**D15. Case identity is `<scope>/<name>` and the hash is defined.** A bare name
is unique only within one agent's cases, while inherited `default_cases` land in
the same run. Identity is `agent/<name>`, `enterprise/<name>` or
`team:<team>/<name>`. `case_hash` is SHA-256 of
`json.dumps(case, sort_keys=True, separators=(",",":"))` with `kind` and
`origin` defaults materialised and all other defaults omitted, computed
**server-side only**.

**D16. An approval timeout denies.** `timeout_minutes` is in the schema and the
enterprise example and appears nowhere in `SPEC.md`. `approvals.decision` is
`approved | rejected | expired`; expiry **denies** the action and fails the run
with `E_APPROVAL_EXPIRED`; `timeout_minutes` follows the minimum rule and an
expiry counts as a rejection in the human intervention rate.

**D17. `publish_runs` has a real state machine.** `status` was undefined and
`published` was both a stage and a version status. `status` is
`running | failed | abandoned | succeeded`, `stage` names only the current or
failing step, a unique partial index allows **one** running pipeline per agent,
and stage 7 re-runs stage 1 validation and fails with
`E_LAYERS_CHANGED_MID_PIPELINE` if the layers moved underneath it.

**D18. An omitted `resources` key inherits the parent scope.** Undefined in the
spec, and the validator uses two different subset algorithms: a `rstrip("*")`
fallback at team level accepts `nmc2/platform-api*` against parent
`nmc2/platform-api`, which also matches `nmc2/platform-api-secrets`, while the
agent-level path rejects it. Omission inherits verbatim; subset testing is **one
shared function**, glob to glob, where a child pattern is accepted only if every
string it can match is matched by some parent pattern, with no `rstrip`, and `*`
never crosses a `/`. A scope key naming a resource type the action does not
declare fails validation.

**D19. Calibration lives only in `calibration_labels`.** §7 names
`enterprise.spec.evaluation.judge.calibration`, a path the JSON Schema
**rejects** (`'calibration' was unexpected`). That path is struck. Agreement and
κ are computed enterprise-wide across all labels for the current `judge_model`
plus `rubric_hash` = SHA-256 of the rubric string. The enterprise YAML keeps
only `min_agreement` and `calibration_min_labels`.

**D20. A meta-agent run is charged to the caller's team.** §8.2 defines budget
flow-down only agent-to-agent, and a meta-agent's caller is a user, so
`cost-optimizer` running an 8-variant grid of eval sweeps charges nobody. Worse,
`platform:run_evals` is declared `risk: read`, so no approval rule pauses it and
any member can spend the enterprise's monthly budget from the dashboard.
A meta-agent run is charged to the **caller's team** aggregate and capped by
`min(meta-agent budget, caller's team remaining)`; every run it spawns inherits
the same root budget; and `run_evals`, `propose_agent`, `record_grade` and
`request_connector` become `risk: write`.

**D21. Prompt assembly is one system message.** §1 says "appended", §6 says
"prepended", and neither defines the join, the message role, or whether a
meta-agent sees the caller's team prompt. **One** system message, parts joined
by `"\n\n"` in the order enterprise, team, agent task, each `.strip()`ed. Run
input is always a separate user message. A meta-agent receives the enterprise
prompt only, never the caller's team prompt, because a team-authored prompt
would otherwise be an instruction channel into an enterprise agent.

---

## Medium

**D22. "Output" is defined, and `output_contains` is real.** Three examples use
`output_contains`, which §7 never documents, and "output" is never defined as
text or JSON. The checked string is
`json.dumps(structured_output, sort_keys=True)` when the agent declares
`output`, otherwise the final assistant text. `output_contains` requires
**every** listed substring, `output_not_contains` forbids **any**, both
case-sensitive. `args_contains` applies the same rule to the canonical JSON of
the call arguments.

**D23. `alerts.when` has a grammar, and counters have a home.** `when` was free
text and custom `metrics` had no reporting path or column. v0 supports exactly
`<metric> <op> <number> over <N>(m|h|d)` with metrics limited to
`failure_rate | denial_rate | p95_latency_ms | cost_usd`, evaluated once a
minute. Custom counters are emitted as `run_events` rows of `type: 'metric'` and
aggregated at read time. `runs` gained `steps`, and `eval_runs` gained
`tool_call_accuracy` and `human_intervention_rate`.

**D24. Sub-agent ranges are pinned at publish.** `secret-scanner@^1` resolves at
run time, so an "immutable" version silently changes behaviour when its
dependency publishes and the attached eval report stops describing what runs.
Ranges resolve **once** at stage 7 and the resolved spec stores exact
`name@x.y.z`. `forked_from` requires an exact version. The schema's `agentRef`
pattern drops `~` and `^`.

**D25. Ports come from the registry YAML and nowhere else.**
memory 7100, slack 7101, github 7102, platform 8000. My own `.handoffs.yaml`
had H3 on 7101, which Slack already claims, so whichever wrapper started second
would have failed to bind during the 07:00 integration lock. Fixed.
`transport.url` in the connector YAML is the single source every wrapper reads
its bind address from, and `contracts/connector_actions.json` is generated from
it so the two cannot drift.

---

## Where these are enforced

| Decision | Enforced in |
|---|---|
| D3 | `contracts/resolved_spec.schema.json`, `tests/contract/test_resolver_output.py` |
| D6, D7, D16, D17 | `contracts/db/001_init.sql` (unique indexes and check constraints) |
| D1, D2, D5, D10, D12, D13, D18 | the resolver, with a unit test per rule |
| D4, D8, D9, D15 | the eval engine and the publish pipeline |
| D25 | `scripts/gen_connector_actions.py`, which fails on a port collision |
| all codes | `contracts/errors.py` |
