# Starting the four agents

## Per laptop, once

```
git clone https://github.com/hanhg/Reagent.git
cd Reagent
python scripts/doctor.py --fix
```

On Windows, first:
```
git config --global core.longpaths true
git config --global core.autocrlf input
```

Fix anything `doctor.py` marks **BAD**. Warnings are fine. **Do not install
Docker on the Windows machines** unless the cloud database fails; it wants WSL2
and possibly a reboot, and the primary database is remote.

Copy `.env.example` to `.env`. One person fills in the real values and shares
them over a private channel, never a commit.

## Then, on each laptop

```
git checkout -b wf/runtime origin/wf/runtime      # or wf/resolver, wf/dashboard, wf/data
claude
```

`CLAUDE.md` loads automatically, so the agent already has the whole picture.
Paste the matching prompt below.

---

### Laptop A — runtime

> You are the WF1 runtime agent on branch `wf/runtime`. Read
> `docs/agents/WF1-runtime.md` and follow it exactly. Before writing any
> middleware, read `docs/research/langchain.md`: two assumptions in the spec were
> refuted and the correct hooks are named there. Run
> `python scripts/handoff.py status` now, and whenever a task has a STOP, run
> `python scripts/handoff.py wait <ID>` and build against the fake it names
> rather than waiting. Release your handoffs the moment their verify command
> passes. Start with task 1.

### Laptop B — resolver, evals, publish

> You are the WF2 resolver agent on branch `wf/resolver`. Read
> `docs/agents/WF2-resolver.md` and follow it exactly, then read all 25 entries
> in `docs/decisions/FROZEN.md` because most of them are yours. Your resolver is
> the critical path: `wf/runtime` and `wf/dashboard` are both blocked on H1 until
> you release it, so build the resolver before the eval engine. Note that
> `spec/tools/check_examples.py` prints ALL PASS on a config with
> enterprise-locked redaction switched off, so use it as a sketch and not as an
> oracle. Start with task 1.

### Laptop C — dashboard

> You are the WF3 dashboard agent on branch `wf/dashboard`. Read
> `docs/agents/WF3-dashboard.md` and follow it exactly. `CLAUDE.md` §10 is a
> binding design spec, not a suggestion: colour encodes provenance and verdict
> and nothing else, and the banned list is absolute. You are never blocked:
> every screen has a fixture in `fixtures/`, so build against those and swap to
> live calls as `python scripts/handoff.py status` turns each handoff ready.
> Generate your API client from `contracts/openapi.yaml`. Build screens 1 to 4
> before anything else. Start with the resolved view.

### Laptop D — data and integrations

> You are the WF4 data agent on branch `wf/data`. Read
> `docs/agents/WF4-data.md` and follow it exactly. Task 1 is the most urgent
> thing on the team: all four workflows are blocked on H4, due 03:30. Read
> `docs/research/tigerdata.md` first, and use the MLH $1,000 credits to create a
> STANDARD service, not a free one. `contracts/db/001_init.sql` is already
> written and tested against real Postgres, so apply it rather than rewriting
> it; if it errors, report that rather than patching it. Before writing an MCP
> wrapper, read `docs/research/mcp.md`: the SDK renamed its main class and your
> training data is wrong. Start with task 1 and release H4 as soon as it passes.

---

## Running an integration test

When two workflows are ready to be tested together, a **human** does this, not
an agent:

```
python scripts/handoff.py lock integ/resolver+runtime --owner B
git checkout -b integ/resolver+runtime origin/main
git merge origin/wf/resolver origin/wf/runtime
git push -u origin integ/resolver+runtime
```

CI runs the seam tests and prints the status table. The frozen workflows keep
committing to their own branches and get a red check if they try to merge to
`main`. When it is done:

```
python scripts/handoff.py unlock integ/resolver+runtime
```

## If an agent gets stuck

Check the board first: `python scripts/handoff.py status`. Most stalls are a
handoff that is ready and nobody told the waiting agent, or one that is overdue
and the waiting agent has not taken its fallback.
