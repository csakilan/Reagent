# Demo script: ten minutes, every feature, the same start every time

## Reset before each demo (30 seconds)

```
python scripts/demo_reset.py          # rebuilds the `demo` schema; never touches the team's tables
python scripts/demo_reset.py --check  # expect: teams 3, agents 9, runs 32, connector_requests 1
```

Then restart the API against the demo schema. `DEMO_DATABASE_URL` is written to `.env`
on the first reset:

```
DATABASE_URL=$DEMO_DATABASE_URL REAGENT_LAYERS=db REAGENT_PUBLISH_STORE=db \
  REAGENT_MODEL=live REAGENT_PIPELINE_RUNS_PER_CASE=1 \
  python -m uvicorn runtime.api.runs:app --port 8000
# wrappers: python scripts/wrappers.py   dashboard: cd dashboard && npm run start
```

Restart the API after every reset: it holds database connections to the old schema.
Run on the Mac: the runtime cannot use Postgres from Windows (WF1's event-loop issue).

**Starting state.** Three teams (platform-eng, people-ops, customer-support), nine agents,
two weeks of run history, an open connector request from dana, and two pr-reviewer
runs on `csakilan/reagent-demo` #1: one succeeded (secret redacted, comment approved),
one failed (the runtime blocked a merge). pr-reviewer 1.0.0 is a draft, ready to publish.

## The demo

Switch users with the log-in-as menu, top right.

**1. The idea (bob · Getting started, 20 s).** "An agent is one YAML file. What it is
allowed to do lives in two layers above it, enterprise and team, and lower layers can
only tighten."

**2. Who sees what (bob, then frank · Hierarchy, 1 min).** Bob sees platform-eng only,
under a locked enterprise box: people-ops is not hidden, it is never sent. Switch to
frank: every team. Select pr-reviewer: its policy, agent first, then team, then enterprise.

**3. Build an agent (bob · Your agents › New agent, 2 min).** Describe it:
*"Review pull requests to our infra repos and flag leaked secrets."* agent-builder forks
pr-reviewer and says why. Open in the editor and try to loosen it:
- set `timeout_seconds: 2300` → *"…is 2300, above the limit of 900 set by enterprise."*
- add `pulls.merge` → *"github:pulls.merge is not granted to this team. team:platform-eng grants…"*

**4. A real run (alice · Trace viewer, 2 min).** Run pr-reviewer on `csakilan/reagent-demo`,
PR `1`. It reads the PR, calls secret-scanner and pauses: posting a comment is a write.
**Approvals** → approve → the review lands on the real PR. Back in the trace: the
planted AWS key appears only as `[REDACTED]`, everywhere.

**5. When it goes wrong (alice · Trace viewer › run 32, 30 s).** The failed story run.
failure-triage explains it without being asked: permission denial, `github:pulls.merge`
blocked, and what to change.

**6. Publish with a human gate (alice · Publish pipeline, 2–3 min).** Start a pipeline for
pr-reviewer. Validate, security review (security-reviewer) and test generation (evaluator)
run; then it stops: *"Waiting for you."* Dry-run `never-merges` (the merge is blocked),
approve the cases, continue. Evals, the gate, and an immutable published version.
**Compare cheaper models** (cost-optimizer). Say: *"For the demo each test runs once. In
production every case runs five times with a 95% confidence interval."*

**7. Governance (frank · Enterprise admin, 1.5 min).** Dana's open request. Edit the
people-ops layer to add `#ask-people-ops-2` to its Slack channels, **Check impact**
(which agents change), Save, Mark fulfilled. Then set a team's `usd_per_month` above the
enterprise's 1000 → refused, naming the field and the layer.

**Close.** "Every run re-read all three layers from the database, so that policy change
reached every agent on its next run. Reset, and we can show it again."

## Notes

- Step 4 needs the live model, the wrappers and the GitHub App; everything else works
  on the fake model. A live publish (step 6) at one run per case is about a minute.
- At one run per case the gate needs every case to pass; if a flaky run blocks it, that
  is the gate doing its job.
- `python scripts/demo_reset.py` refuses to seed if any table is missing from `demo`,
  so nothing can land in the shared tables.
