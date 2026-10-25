# WF1 — Runtime

**Branch:** `wf/runtime` · **Owner:** A · **MVP rows:** 3, 4, 5

You build the thing that actually runs an agent. Everything else on this team
either feeds you a spec or displays what you recorded.

## Read, in this order

1. `CLAUDE.md` (already loaded)
2. `docs/decisions/FROZEN.md` — **D1, D2, D4, D7, D13, D21** are yours
3. `docs/research/langchain.md` — **two of your assumptions were refuted; read it before writing any middleware**
4. `docs/research/openrouter.md` — the budget design depends on it
5. `spec/SPEC.md` §6, §8, §15

## You own

```
runtime/engine/        agent factory, model binding, the LangChain graph
runtime/guardrails/    redaction, permission enforcement, limits, approvals
runtime/subagents/     agent-as-tool, depth, budget flow-down
runtime/api/runs.py    POST /v0/agents/{name}/runs, GET /v0/runs/{id}
```

**Do not touch** `runtime/resolver/`, `runtime/evals/`, `runtime/publish/`,
`cli/`, `dashboard/`, `connectors/`, `contracts/`.

## Three things that will cost you an hour if you learn them late

**There is no `before_tool` hook.** It does not exist. The gate that fires after
the model requests tools and before any execute is **`after_model` with
`@hook_config(can_jump_to=["end"])`**. Copy the shape of LangChain's own
`ToolCallLimitMiddleware`.

**Tool filtering is not a security boundary.** `langchain-mcp-adapters` cannot
load a subset of a server's tools, so filtering the returned list only keeps
ungranted tools out of the model's context. **Real enforcement is a
`wrap_tool_call` middleware** that denies anything outside
`effective.tool_permissions`. Build both layers.

**Cost arrives only at the end of a stream.** There is no mid-generation cost
signal, so the budget kill-switch accumulates per completed model call and halts
at a turn boundary. Do not try to abort mid-token.

## Tasks

### 1. Agent factory from a resolved spec · due 04:30

**STOP: wait on H1.** `python scripts/handoff.py wait H1`
While it is pending, build against `fakes/resolver.py`, which serves golden
resolved specs that validate against `contracts/resolved_spec.schema.json`.

Read **only** `effective` from the resolved spec. Never walk `provenance`; that
is the dashboard's view. `effective.tool_permissions` is the complete and only
source of what the agent may call, including synthetic `memory` entries the
resolver already expanded for you (FROZEN D12).

Build tools from `mcp_tool`, never from `action`. The names differ for 18 of our
30 actions because MCP forbids dots. The mapping is in
`contracts/connector_actions.json`.

Pin exactly: `langchain==1.4.7`, `langgraph==1.2.17`,
`langchain-mcp-adapters==0.3.2`, `langchain-openai>=1.1.11`. That last pin is
load-bearing: below 1.1.11 cost is silently dropped and every budget reads zero.

Model via `ChatOpenAI(base_url="https://openrouter.ai/api/v1", stream_usage=True)`.
Cost is at `response_metadata["cost"]`, never `usage_metadata`.

**Done when:** `POST /v0/agents/pr-reviewer/runs` returns output using
`fakes/openrouter.py` and `fakes/github_wrapper.py`.

### 2. Permission enforcement and redaction · due 05:30

`wrap_tool_call` denies any call not in `effective.tool_permissions`, records a
`run_events` row with `allowed: false`, and raises `E_TOOL_CALL_DENIED`. Also
assert at startup that every granted action exists on the server, and fail with
`E_CONNECTOR_DRIFT` if not, so a renamed upstream tool is loud rather than
silently granting nothing.

Redaction wraps **four** paths, not one: model output, tool results, trace
payloads, and logs. Regex patterns plus an exact-match list of the known demo
secrets, because a miss on camera loses the round. Nothing reaches the database
un-redacted; `run_events.payload_redacted` is named that way on purpose.

**Done when:** the planted AWS key appears nowhere in output, trace, or any
database row, and an ungranted call is refused and recorded.

### 3. Limits · due 06:00

Per-run limits come from `effective.limits` and are already resolved to the
minimum across layers, so do not re-derive them.

| Limit | How |
|---|---|
| steps | built in: `ModelCallLimitMiddleware(run_limit=..., exit_behavior="end")` |
| budget | **yours.** Accumulate in `after_model` into a custom `state_schema`, halt with `jump_to: end` |
| timeout | **yours.** `RunControl` passed as `control=`, watchdog thread calls `request_drain("timeout")`, catch `GraphDrained` |

Aggregate limits (`usd_per_month`, `max_concurrent_runs`) are **not** per-run
minimums and are checked **before** the run starts by summing the `runs` table
per layer. See FROZEN D7.

A step-boundary time check is not enough on its own, because one slow MCP call
overruns it. Use `RunControl`.

### 4. Approval pause and resume · due 07:00

**STOP: wait on H4** for a real database. Fall back to the Compose Postgres.

`HumanInTheLoopMiddleware` with `interrupt_mode="per_call"`, an
`AsyncPostgresSaver` checkpointer, and **`durability="sync"`** on the invoke.
Run `await checkpointer.setup()` once. With `InMemorySaver` or the default async
durability, resume-in-another-process silently does not work.

Do not hand-roll an interrupt inside `wrap_tool_call`. On resume a node
re-executes from its start, so any side effect before the interrupt fires twice.

**RELEASE: H6** once a paused `github:pulls.comment` writes an `approvals` row
and resumes to completion after approval.
```
pytest tests/contract/test_approval_pause_resume.py -q
python scripts/handoff.py ready H6
```

### 5. Sub-agent calls · due 07:30

`sub_agents` become tools named `agent:<name>`. The callee runs with its own
resolved permissions, **intersected with the root run's team grants** (FROZEN
D1 — this is the one place §8.1 of the spec is wrong). Limits flow down as
`min(own, caller's remaining)`. Reject cycles at resolve time. Child runs are
`run_events` spans nested under the parent via `runs.parent_run_id`, and cost
rolls up.

### 6. Recording · throughout

Every run writes one `runs` row and ordered `run_events` rows matching
`contracts/run_events.schema.json`. Note `runs` has a **composite primary key
`(id, started_at)`** because it is a hypertable, so **nothing can foreign-key to
`runs(id)`** and `run_events.run_id` carries no FK. Do not try to add one.

**RELEASE: H2** once a run executes end to end and is recorded.
```
pytest tests/contract/test_run_lifecycle.py -q
python scripts/handoff.py ready H2
```
H2 unblocks `wf/resolver` (the eval engine needs to execute runs) and
`wf/dashboard` (real traces). Releasing it promptly is part of the task.

## Checkpoints

| Time | Expected |
|---|---|
| 04:30 | an agent runs against fakes |
| 05:30 | redaction and permission denial work |
| 06:00 | all three limits enforce |
| 07:00 | **H6 released** |
| 07:30 | sub-agent call with a nested span |
| 08:00 | **H2 released**, on live connectors if H3 landed |

## Verify

```
python scripts/doctor.py
python scripts/handoff.py status
pytest tests/contract/test_run_lifecycle.py tests/contract/test_approval_pause_resume.py -q
pytest tests/unit -q
```

Report what you did **not** finish. A claim of success on something untested is
worse than no report.
