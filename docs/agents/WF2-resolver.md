# WF2 — Resolver, evals, publish pipeline, CLI

**Branch:** `wf/resolver` · **Owner:** B · **MVP rows:** 1, 2, 6

You build the part the whole pitch rests on. The defensible claim in this
project is "a three-layer tighten-only merge resolved on every run, plus a
publish pipeline where generated tests must be human-reviewed before they run".
Both halves are yours.

## Read, in this order

1. `CLAUDE.md` (already loaded)
2. `docs/decisions/FROZEN.md` — **read all 25. Most of them are yours.**
3. `contracts/resolved_spec.schema.json` — your output shape, already fixed
4. `contracts/errors.py` — your rejection codes and their exact wording
5. `spec/SPEC.md` §1, §2, §7, §11, §12

## You own

```
runtime/resolver/      the merge, visibility filtering, resolved view
runtime/evals/         eval engine, code checks, judge, statistics
runtime/publish/       the 7-stage pipeline
cli/                   reagent validate | run | eval | publish | fork
runtime/api/agents.py  /resolved, /validate, /evals, /publish, /publish-runs
```

**Do not touch** `runtime/engine/`, `runtime/guardrails/`, `dashboard/`,
`connectors/`, `contracts/`.

## The thing to understand before you start

`spec/tools/check_examples.py` already implements a good share of the resolver
in prototype form, and it prints `ALL PASS` on the examples. **It also prints
`ALL PASS` on a config with enterprise-locked `redaction` switched off, the
`enterprise-kb` bucket redeclared as `write: ["*"]`, and a widened repo scope.**
That was tested. `ALL PASS` means the YAML parses.

So treat that script as a useful starting sketch and a dangerous oracle. Its
`locked` check is literally a `pass` statement.

## Your own internal order matters

Resolver first, evals second. The runtime depends on your resolver, and your
eval engine depends on the runtime. Build in that order and the dependency is a
chain rather than a standoff.

## Tasks

### 1. Validator and CLI validate · due 03:45

Schema validation against `spec/schema/reagent.schema.json`, plus the semantic
checks. **Coverage gaps now exit non-zero** (FROZEN D9), and inherited
`default_cases` do **not** count toward `requirements.min_cases`.

The schema has real holes: `mocks` is a bare `{"type": "object"}` that validates
nothing, `allowList` has no `deny` member so the denylist-union rule is
inexpressible, and the schema **accepts** an agent setting
`guardrails.redaction.secrets: false` against an enterprise lock.

**Done when:** a malformed file fails with a readable error naming the field,
and the 19 examples still pass.

### 2. The resolver · due 05:00 — THIS IS THE CRITICAL PATH

One unit test per row of `SPEC.md` §1's merge table. No exceptions. The rules
are a table; there is no excuse for not testing each one.

Frozen rules you must implement, not invent:

- **D10.** Every allowlist is a true **intersection** across all three layers.
  An omitted allowlist means "inherit the parent set unchanged". A child element
  matching nothing in the parent **fails**, it is not dropped. The prototype
  does last-layer-wins; that is wrong.
- **D5.** `locked` entries are **prefix** locks. Any lower layer setting that
  path or a descendant fails with `E_LOCKED_PATH`. Locks union and never relax.
- **D13.** `connectors.<name>.deny` (bare globs) and `guardrails.deny`
  (qualified) both normalise to qualified globs and union into one
  `effective.denylist`. **Deny beats allow.** The prototype never reads
  `guardrails.deny` at all.
- **D18.** Omitted `resources` inherits the parent scope. Subset testing is
  **one shared function**, glob to glob: a child pattern is accepted only if
  every string it can match is matched by some parent pattern. No `rstrip("*")`.
  `*` never crosses `/`. The prototype has two different algorithms that
  disagree, and the looser one accepts a widening.
- **D7.** Per-run limits resolve by minimum into `effective.limits`.
  `usd_per_month` and `max_concurrent_runs` are **aggregate** and go in
  `effective.aggregate_limits`.
- **D12.** Expand each `memory` entry into a synthetic `tools` entry before any
  coverage or least-privilege check. `tools` in the resolved view is always the
  complete allowlist.
- **D1.** An enterprise agent resolves to `own tools ∩ the ROOT run's team
  grants`, whatever its `access`. `service` gets no exemption.
- **D21.** The prompt is **one** system message, parts joined by `"\n\n"` in the
  order enterprise, team, agent, each stripped. A meta-agent gets the enterprise
  prompt only, never the caller's team prompt.
- **D3.** When the caller is not an enterprise admin, every leaf whose source is
  `enterprise` gets `value: null` and `redacted: true`, keeping the source tag.

Emit both `provenance` and `effective`, and make them agree.

**RELEASE: H1** — this unblocks `wf/runtime` and `wf/dashboard`, both of which
are waiting on it right now.
```
pytest tests/contract/test_resolver_output.py -q
pytest tests/unit/test_merge_rules.py -q
python scripts/handoff.py ready H1
```

### 3. Eval engine · due 07:00

**STOP: wait on H2** for live execution. Until then grade recorded traces from
`fixtures/traces/`.

Code checks first, in this order: `tool_calls` trajectory, `no_tool_calls`,
`side_effects` against the mocked sandbox, `output_not_contains`,
`output_matches_schema`. Then the judge, only if calibrated.

Frozen: `mocks[action]` is the **verbatim** tool result returned to every call
of that action, no argument matching, and a case whose agent has `sub_agents`
**must** mock `agent:<name>`. An unmocked call fails the case with
`E_UNMOCKED_ACTION` (D4). "Output" means
`json.dumps(structured_output, sort_keys=True)` when the agent declares
`output`, else the final assistant text (D22).

Statistics: pass rate with a **Wilson** 95% interval, pass^k, recall,
false-alarm rate, safety at 100%. A regression is flagged only when the two
versions' intervals do not overlap.

**Develop against `fakes/openrouter.py`.** Five runs per case across many cases
burns real credits, and `:free` models report `cost: 0.0` so they would make
your budget assertions pass vacuously.

**RELEASE: H5** once `POST /evals` returns a full report.

### 4. Publish pipeline · due 08:30

Seven stages, one human gate. Frozen rules: `status` is
`running | failed | abandoned | succeeded` and `stage` names only the current
step (D17); the database already enforces **one running pipeline per agent**;
stage 7 re-runs stage 1 and fails with `E_LAYERS_CHANGED_MID_PIPELINE` if the
layers moved. Case identity is `<scope>/<name>` and `case_hash` is SHA-256 of
`json.dumps(case, sort_keys=True, separators=(",",":"))`, computed
**server-side only** (D15). Stage 6 fails with `E_NO_GATING_CHECKS` if no case
carries a code check (D8). Meta-agents are seeded `published` with
`origin: 'platform'` and skip the pipeline entirely, which is what stops stages
2, 3 and 5 from needing themselves.

Stages invoke meta-agents as a `system` principal, not as sub-agents (D11).

### 5. CLI · due 09:00

`reagent validate | run | eval | publish | fork`, Typer, over HTTP. `publish`
starts the pipeline and prints the review link, because review happens in the
dashboard.

## Checkpoints

| Time | Expected |
|---|---|
| 03:45 | validator, examples pass, coverage gaps exit non-zero |
| 05:00 | **H1 released.** Two agents are blocked until you do. |
| 07:00 | **H5 released** |
| 08:30 | pipeline reaches stage 7 |
| 09:00 | CLI works |

## Verify

```
python spec/tools/check_examples.py spec
pytest tests/unit/test_merge_rules.py -q          # one test per merge rule
pytest tests/contract/test_resolver_output.py -q
```
