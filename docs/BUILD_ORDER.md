# Build order

Final submission **11:30**. Code freeze **10:30**. Everything below is CDT,
Sunday 2026-10-25.

## Schedule

| Time | What has to be true | Who |
|---|---|---|
| 03:00 | contracts, fakes, fixtures, CI on `main` | done |
| **03:30** | **H4** database live, migrated, seeded | D |
| 03:45 | validator works, examples pass | B |
| 04:30 | an agent runs against fakes · GitHub app and planted-key repo | A · D |
| **05:00** | **H1** resolver emits a real resolved spec · resolved view renders | B · C |
| 05:30 | redaction and permission denial work · seed loads | A · D |
| 06:00 | all three limits enforce · trace viewer | A · C |
| **07:00** | **H6** approval pause and resume · **H3** live GitHub wrapper | A · D |
| 07:30 | sub-agent nested span · **H5** eval report · pipeline screens | A · B · C |
| **08:00** | **H2** a run executes end to end and is recorded | A |
| 08:00–09:00 | `integ/all`: everything merged, seams green | all |
| 09:00 | CLI · admin, analytics, privacy · track features | B · C · D |
| 09:00–10:00 | **demo rehearsal, three full runs** | all |
| 10:00–10:30 | pitch video recorded, submission text written | all |
| **10:30** | **CODE FREEZE** | |
| 11:30 | submitted | |

Rehearsal is not optional and not the thing you cut. A demo nobody has run end
to end will fail on stage, and the feasibility report's own top risk is
integration time at the end.

## Cut order, already decided

Cut from the top of this list when you are behind. Do not spend time at 08:00
arguing about it.

1. Memory buckets and pgvector search
2. `failure-triage` and `cost-optimizer` meta-agents
3. Slack connector and the Slackbot agent
4. `security-reviewer` meta-agent, folded into the validator's output
5. Mutation testing, keep the slide
6. Analytics page with live numbers, seeded is acceptable
7. `agent-builder`, with demo step 3 falling back to forking by hand

## Never cut. These six are the demo.

1. The resolver and its tighten-only rejection messages (demo step 4)
2. One agent running end to end (step 6)
3. Secret redaction (step 6)
4. The approval pause (step 6)
5. The publish pipeline with its human review gate (step 5)
6. The resolved view with provenance (step 3)

## Demo corrections

Two steps in the feasibility report's demo script do not work as written.

**Step 9, "Claude Desktop through MCP".** Claude Desktop dials custom connectors
from Anthropic's cloud, so it cannot reach `localhost`. Use
`claude mcp add --transport http reagent http://localhost:8000/mcp` in Claude
Code instead, or bridge with `mcp-remote`. Rehearse whichever you pick.

**Step 5, the cost-optimizer sweep.** Running it live spends real credits across
8 variants. Pre-run it and show cached results, triggering one live case.

## Pitch corrections

Three claims in the competitive section are wrong or stale and a judge may
catch them. Full detail in `docs/research/competition.md`.

- **cagent is now `docker/docker-agent`** at ~4.6k stars, and **it has eval
  tooling** (`docker agent eval`). Do not say "no evals".
- **Microsoft shipped an Agent Governance Toolkit** in April 2026, MIT, with a
  real policy engine. Do not say open-source agent governance does not exist.
- **Obot already does connector registry and audit logs.** Those are table
  stakes, not differentiators.

Lead instead with the two things nobody has: **a three-layer tighten-only merge
resolved on every run, and a publish pipeline where generated tests must be
human-reviewed before they run.**
