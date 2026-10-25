# Demo runbook: resolver, validation, evals, publish (WF2)

Rehearsed through the real server on port 8000, 2026-10-25. Every quoted line
below is what actually printed. Run from the repo root.

## Setup (demo machine)

```
python -m uvicorn runtime.api.runs:app --port 8000      # WF1's app; serves WF2's routes too
export REAGENT_API=http://localhost:8000 REAGENT_USER=alice
```

| Variable | Demo value | Why |
|---|---|---|
| `REAGENT_LAYERS` | `db` | Layers re-read from Tiger Cloud every run, so a policy edit shows on the next run |
| `REAGENT_PUBLISH_STORE` | `db` | Pipelines and published versions persist across restarts |
| `REAGENT_MODEL` | `live` for the real run, `fake` to rehearse | A live publish spends credits (about $0.20) |
| `REAGENT_INHERITED_MOCKS` | leave unset | Default `inert` is the team's decision on the D4 conflict |
| `REAGENT_PIPELINE_RUNS_PER_CASE` | `1` | **Demo only.** Each eval case runs once instead of five times, so a live publish takes about a minute instead of six |

Database mode needs a working `DATABASE_URL`. **The runtime cannot use Postgres
on Windows** (WF1's async event-loop bug), so run the live demo on the Mac.

## Step 4: tighten-only refusals

Edit pr-reviewer's `budget.timeout_seconds` to 2300, then `python -m cli.reagent validate <file>`:

```
E_LIMIT_ABOVE_PARENT  spec.budget.timeout_seconds is 2300, above the limit of 900 set by enterprise. A lower layer may set a smaller value, never a larger one.
```

Add `pulls.merge` to its github `actions`, then validate:

```
E_ACTION_NOT_GRANTED  github:pulls.merge is not granted to this team. team:platform-eng grants github:pulls.read, github:pulls.comment, github:issues.*, github:contents.read.
E_DENIED_ACTION  spec.tools[0].actions[2] matches a deny rule from agent. Deny always beats allow.
```

Both exit 1. The dashboard editor shows the same text from `POST /v0/agents/{name}/validate`.

## Resolved view: members versus admins

`GET /v0/agents/pr-reviewer/resolved`:

- as **alice** (member): enterprise values arrive as `{"value": null, "source": "enterprise", "redacted": true}`; `effective.prompt` is null.
- as **frank** (admin): real values, e.g. `max_call_depth` 3 from enterprise and the full enterprise prompt.
- alice gets **404** for `people-ops-bot` (another team) and `secret-scanner` (enterprise config).

## Publish pipeline

```
python -m cli.reagent publish pr-reviewer
pipeline 1 started for pr-reviewer: stage review, running
review every new or changed case here: http://localhost:3000/publish?run=1
```

- `continue` before review is refused: `4 case(s) are unreviewed. Nothing publishes with unreviewed tests.`
- Dry run of `agent/never-merges` passes, with `blocked: ["github:pulls.merge"]`: the runtime stopped the merge.
- Inherited enterprise cases arrive already approved; members see names and results only.
- After review, `continue` runs evals, then the gate, then stage 7.

**Timing.** With `REAGENT_PIPELINE_RUNS_PER_CASE=1` a live publish runs each
of the 6 cases once: roughly a minute and a few cents. The report shows
`runs_per_case: 1`. Without it, 6 × 5 runs take about **6 minutes** and $0.20.

**What to say on stage:** "For the demo each test runs once. In production every
case runs five times, and the pass rate comes with a 95% confidence interval, so
one lucky or unlucky run can't decide a publish."

**The trade-off:** at one run per case the gate needs all 6 to pass (5 of 6 is
0.83, below 0.9), so one flaky run blocks the publish. From the last live run,
expect that roughly one time in three. If it happens, it is an honest
demonstration of the gate doing its job. Under `REAGENT_MODEL=fake` the gate blocks at
`pass_rate 0.83 < 0.9`, because WF1's fake model reads a top-level `diff` and
misses the S3 issue in the real wrapper's `files[].diff` shape.

## Live eval numbers (qwen-coder, 5 runs per case)

| | First run | After fixes | After the s3 check was relaxed |
|---|---|---|---|
| Pass rate | 0.47 | 0.83 | **0.93**: gate passes |
| 95% interval | 0.30–0.64 | 0.66–0.93 | 0.79–0.98 |
| Safety | 0.53 | 1.00 | 1.00 |
| p95 latency | 137 s | 23 s | 7 s |
| Cost (30 runs) | $0.15 | $0.20 | $0.20 |

**The margin is thin.** The gate compares the point estimate with 0.9, and 0.93
is 28 of 30 runs. Two more failures on stage would block the publish. Rehearse
once on the demo machine; if it blocks, the gate catching a weak run is itself
an honest demo of the claim.

Fixes: memory mocks in every case, an ordered prompt in which posting the review
is a required step, and a realistic GitHub patch in the s3 case. The s3 check
was relaxed from `infra/s3.tf` to `S3` and `public-read` by team decision: the
model names the resource, not the file path.

## Known gaps

- Runtime plus Postgres fails on Windows (WF1).
- The runtime's `langchain-mcp-adapters 0.3.2` cannot import alongside the
  wrappers' `mcp 2.3`: run them in separate environments (WF1/WF4).
- The judge is uncalibrated (0 labels), so judge rubrics are reported and never gate.
