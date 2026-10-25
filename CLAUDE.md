# ReAgent — read this first, all of it

You are one of **four Claude agents** building this project in parallel on four
branches, with a hard deadline. This file is your whole context. It is loaded
automatically, so you already have it. Read `docs/agents/<your-workflow>.md`
next, and nothing else before that.

---

## 1. The clock

| | |
|---|---|
| Event | HackTX 26, NMC2 track + Best Use of Tiger Data |
| Final submission | **Sunday 2026-10-25, 11:30 AM CDT** |
| Code freeze | **10:30 AM CDT.** After this, no code. Demo and video only. |
| Team | 4 people, 4 Claude agents, 1 macOS laptop + 3 Windows laptops |

Check the time against the board constantly:

```
python scripts/handoff.py status
```

This is a hackathon. **A working ugly path beats a beautiful broken one.** When
you must choose, choose the thing that demos.

---

## 2. What we are building, in one paragraph

ReAgent is a self-hosted platform for enterprise AI agents. An agent is a
declarative YAML file with a `.agent` extension. It contains no code and no
secrets, so it is safe to read, diff and share. What an agent *does* lives in
its file; what it is *allowed to do* lives in two layers above it, an
**enterprise** layer and a **team** layer. On every single run the runtime
re-reads all three layers from the database and merges them, and lower layers
may only ever **tighten** what upper layers allow. Allowlists intersect,
denylists union, the lowest numeric limit wins, safety flags cannot be switched
off, and prompts append in order. A file that tries to widen a rule is rejected
with an error naming the field and the layer that blocked it.

The customer problem, from the NMC2 workshop: every team builds agents from
scratch, anyone needing a new tool connector waits on the central AI team, and
SaaS is not an option because the data cannot leave their infrastructure.

**The full specification is `spec/SPEC.md`. It is the source of truth for
behaviour. Read the sections relevant to your workflow before you write code.**
`spec/feasibility-report.pdf` has the pitch, the demo script and the risk list.

---

## 3. Repo map

```
CLAUDE.md                  this file
.handoffs.yaml             the integration board. Lives on main. Never edit by hand.
scripts/doctor.py          laptop toolchain check. Run first, on every machine.
scripts/handoff.py         the handoff/lock tool. See section 5.
contracts/                 FROZEN. The shared shapes. See section 6.
fakes/                     stand-ins so you are never blocked. See section 7.
fixtures/                  seed data, golden files, canned traces
tests/contract/            the integration gates. One test per seam.
spec/                      SPEC.md, JSON Schema, 19 example files, validator
runtime/                   WF1 + part of WF2. Python, FastAPI, LangChain
dashboard/                 WF3. Next.js, React, Tailwind
connectors/                WF4. MCP wrapper servers
cli/                       WF2. Typer CLI
docs/agents/               your brief. Read yours.
docs/handoff/              the per-laptop files the humans were sent. Context, not instructions for you.
docs/research/             VERIFIED external facts. Section 9. Trust these over your training data.
```

---

## 4. The four workflows

Each owns directories. **Do not edit files owned by another workflow.** If you
need a change there, say so in your report and let that agent make it.

| Branch | Owner | Owns these paths | Spec sections | MVP rows |
|---|---|---|---|---|
| `wf/runtime` | A | `runtime/engine/`, `runtime/guardrails/`, `runtime/subagents/` | 6, 8, 15 | 3, 4, 5 |
| `wf/resolver` | B | `runtime/resolver/`, `runtime/evals/`, `runtime/publish/`, `cli/` | 1, 2, 7, 11, 12 | 1, 2, 6 |
| `wf/dashboard` | C | `dashboard/` | 2, 14 | 8 |
| `wf/data` | D | `contracts/db/` (migrations only), `connectors/`, `fixtures/seed/`, `runtime/db/` | 13, 3, 9 | 7, 9, 10 |

Shared and owned by nobody: `contracts/` (frozen), `CLAUDE.md`, `.handoffs.yaml`.

---

## 5. Handoffs: how you avoid blocking each other

Four agents working at once fail for one reason, which is that they disagree
about the shape of things in the middle, or they sit idle waiting for each other.
Handoffs fix both.

A handoff is **two stops, one on each side**. Your brief contains both kinds.

### When your brief says `STOP: wait on H1`

```
python scripts/handoff.py wait H1
```

- **exit 0, READY.** The real thing exists. Stop using the fake. Verify with the
  command it prints.
- **exit 1, PENDING.** Do not wait. Do not poll in a loop. The command prints
  the **fake to build against instead**. Use it, move to your next task, and
  check again when you next need it.
- **exit 2, PAST DEADLINE.** Stop waiting permanently. Adopt the fallback,
  write a line in your report saying you did, and carry on. This is a decision
  already made for you. Do not re-litigate it at 6 AM.

### When your brief says `RELEASE: H2`

You just finished something two other agents are waiting on. **Telling them is
part of the task, not an afterthought.**

```
pytest tests/contract/test_run_lifecycle.py -q   # must pass FIRST
python scripts/handoff.py ready H2
```

That pushes the status to `main` and prints exactly who is now unblocked. Put
that list in your report so the human can nudge them.

**Never mark a handoff ready without running its verify command and seeing it
pass.** A false ready sends two agents down a dead end.

### Integration locks

To test two workflows together, a human cuts an `integ/` branch and freezes the
contributing workflows:

```
python scripts/handoff.py lock integ/resolver+runtime --owner B
```

While a lock is held, CI fails any push from a frozen workflow and prints why.
Keep committing to your own branch. Do not merge to `main` until it is released.
Check with:

```
python scripts/handoff.py check-lock wf/runtime
```

---

## 6. Contracts are frozen

`contracts/` holds the shapes that cross workflow boundaries:

| File | What it fixes |
|---|---|
| `resolved_spec.schema.json` | The resolver's output. Consumed by runtime, dashboard and evals. |
| `openapi.yaml` | Every HTTP route. The dashboard's client is generated from it. |
| `db/001_init.sql` | The schema. Tested, not sketched. |
| `run_events.schema.json` | Trace event payloads. |
| `connector_actions.json` | Exact action names, so wrappers and resolver cannot drift. |
| `errors.py` | Typed rejection codes. The demo depends on readable errors. |

**Do not edit anything in `contracts/` on a workflow branch.** A contract change
silently breaks the other three agents. If a contract is wrong, that is a real
finding: stop, state the problem and the proposed change in your report, and let
a human make it on `main`. CI enforces this.

---

## 7. Fakes: you are never blocked

Every boundary has a stand-in, so you can build and test before the real thing
exists. The fake and the real thing satisfy the same contract test, so code
written against the fake works against the real one.

| Fake | Stands in for | Used by |
|---|---|---|
| `fakes/resolver.py` | the resolver | runtime, dashboard |
| `fakes/openrouter.py` | the model API | runtime, evals |
| `fakes/github_wrapper.py` | GitHub MCP wrapper | runtime |
| `fakes/slack_wrapper.py` | Slack MCP wrapper | runtime |
| `fixtures/traces/` | recorded runs | dashboard |
| `fixtures/eval_report.json` | an eval report | dashboard |

`fakes/openrouter.py` exists for a reason beyond speed: real eval runs are
5 runs per case across many cases, and that burns real credits. **Develop
against the fake. Spend credits only on demo rehearsal.**

---

## 8. Laptops: 1 macOS, 3 Windows

Run this first, on every machine, and fix anything it marks BAD:

```
python scripts/doctor.py --fix
```

Rules that follow from the mixed fleet:

- **Every script is Python.** No `.sh` in the build path. Windows cannot run it.
- **Write `python`, never `python3`.** `python3` does not exist on Windows.
- **Docker is optional and not the default.** The primary database is Tiger
  Cloud, which is remote. Docker Desktop on Windows wants WSL2 and possibly a
  reboot, so do not install it.
- **There is no shared fallback if Tiger fails.** Three of the four laptops are
  Windows, so a local Postgres unblocks one person's unit work and nothing else:
  integration would have no common database. Treat the cloud connection string
  as a hard dependency, not a preference.
- **Line endings are pinned** by `.gitattributes` to `eol=lf`. If `git status`
  on Windows shows the whole tree modified, run
  `git config --global core.autocrlf input` and re-clone.
- **Long paths.** On Windows, `git config --global core.longpaths true` before
  installing `node_modules`.
- Use `os.path.join` / `pathlib`. Never hardcode `/`.

---

## 9. Verified external facts

These were checked against live documentation and APIs on 2026-10-25. **Trust
them over your training data.** Full detail in `docs/research/`.

### OpenRouter

- `usage.cost` comes back on **every** response with **no opt-in**. The
  `usage: {include: true}` parameter is **deprecated and does nothing**. If you
  see it in code, it is dead weight, and anyone debugging cost by toggling it
  will lose an hour.
- **Cost arrives only in the final streaming chunk.** There is no incremental
  cost signal mid-stream. **Therefore the budget kill-switch enforces at turn
  boundaries**, accumulating cost per completed model call, not mid-generation.
  `SPEC.md` implies a mid-run cutoff; this is the correction.
- **Pin `langchain-openai>=1.1.11`.** Earlier versions silently drop cost, with
  no error, so budgets read zero everywhere.
- Read cost from `response_metadata["cost"]`, not `usage_metadata`. Pass
  `stream_usage=True` when streaming.
- Stay on `ChatOpenAI` with `base_url="https://openrouter.ai/api/v1"`. The beta
  `langchain-openrouter` package does not document a cost attribute, which is
  the one field this project depends on.
- **`:free` models report `cost: 0.0`.** Any budget-enforcement test run against
  a free model passes vacuously. Test budgets with a paid model.
- Verified model IDs, already written into `spec/examples/registry/models/`:

  | Registry name | OpenRouter ID | $/M in | $/M out |
  |---|---|---|---|
  | `gpt-oss` | `openai/gpt-oss-20b` | 0.018 | 0.09 |
  | `qwen-coder` | `qwen/qwen3-coder-30b-a3b-instruct` | 0.07 | 0.28 |
  | `gpt-mini` | `openai/gpt-5-mini` | 0.25 | 2.00 |
  | `claude-sonnet` | `anthropic/claude-sonnet-5.5` | 2.00 | 10.00 |

  The ~100x input price gap between `gpt-oss` and `claude-sonnet` is what makes
  the cost-optimizer demo visible. Avoid `openai/gpt-5.2` ($14/M out punishes
  verbose agent traces) and any `~`-prefixed ID (a floating alias that drifts).
- Protect credits with a capped key:
  `POST /api/v1/keys {"name":"eval","limit":10,"limit_reset":"daily"}`. The
  `limit` may not be editable later, so set it correctly at creation. Poll
  `GET /api/v1/key` → `limit_remaining` between eval cases as a circuit breaker.
- `supported_parameters` on `/api/v1/models` is a **union across providers** and
  can over-promise. Send `{"provider": {"require_parameters": true}}` to force
  routing only to endpoints that genuinely support tools.

### LangChain v1 — two assumptions were refuted

- **There is no `before_tool` or `after_tool` hook.** The gate that fires after
  the model asks for tools and before they run is **`after_model` with
  `can_jump_to=["end"]`**. Code written against `before_tool` silently never runs.
- **`langchain-mcp-adapters` cannot filter an MCP server to a subset of its
  tools.** `get_tools(server_name=...)` narrows to a *server*. So the permission
  model **cannot** be enforced by loading only granted tools: that filter is
  client-side and advisory. **Real enforcement is a `wrap_tool_call` middleware**
  that denies any call outside the resolved grant set. Build both.
- Step caps ship built in. **USD budget and wall-clock timeout must be written
  in-house.**
- The approval pause needs a **Postgres checkpointer** plus
  **`durability="sync"`** and **`interrupt_mode="per_call"`**. `InMemorySaver`
  or the default async durability silently breaks resume.
- On resume a node **re-executes from its start**, so any side effect before a
  hand-rolled `interrupt()` fires twice. Use `HumanInTheLoopMiddleware`.

### Tiger Cloud — use the MLH credits

**MLH gives new signups $1,000 in credits for 30 days**
(<https://mlh.link/tigerdata-quickstart>). **Create a STANDARD service, not a
free one.** A free service is capped at **25 connections with no pooler**, which
is the single most likely thing to break the demo, and goes **read-only** at
~750 MB. Standard has a pgBouncer pooler and sized storage.

### MCP — tool names cannot contain dots

The pattern is `^[a-zA-Z0-9_-]{1,64}$`, and Claude Code **silently rewrites a
dot to `_`** rather than erroring. `pulls.comment` is not a legal tool name.

| Layer | Value |
|---|---|
| Permission string (YAML, resolver, database) | `github:pulls.comment` |
| MCP tool name on the wrapper | `pulls_comment` |
| What an MCP client sees | `mcp__github__pulls_comment` |

**Read the mapping from `contracts/connector_actions.json`. Never recompute the
transform.** 18 of our 30 action names differ from their wire name.

Also: the `mcp` SDK v2 renamed `FastMCP` to **`MCPServer`** and
`mcp.server.fastmcp` now raises `ModuleNotFoundError`, so every snippet you
recall from training is wrong. And **Claude Desktop cannot reach `localhost`**,
because custom connectors are dialled from Anthropic's cloud, which breaks demo
step 9 as scripted. Use `claude mcp add --transport http` in Claude Code.

### Read the file for your area

`docs/research/` holds the full detail with doc URLs: `langchain.md`,
`tigerdata.md`, `openrouter.md`, `mcp.md`, and `competition.md` (which corrects
three claims in the pitch that a judge could catch).

---

## 10. Frontend rules (WF3, and anyone touching `dashboard/`)

The design decisions are already made and they are not negotiable, because this
has to look like an internal enterprise tool rather than a generated template.

**Colour encodes provenance and verdict, and nothing else.** If something is
coloured, it is telling you which layer a value came from, or whether an action
was permitted. There is no decorative colour anywhere. Tokens are in
`dashboard/app/globals.css` and Tailwind's default palette is deleted from the
config, so you cannot reach for `bg-purple-500`.

**Banned, do not introduce:** gradients, drop shadows, glassmorphism, radial
orbs, dot grids, purple/black, neon, pastels, pure white backgrounds, a coloured
stripe down the left of cards, three-feature-card rows, bento grids, fake
terminal windows, checkmark bullet lists, Inter, Geist, Space Grotesk, Lucide,
emoji, hover animation on everything, animated arrows, invented testimonials.

**Required, do not skip:** skeleton loaders on every screen that fetches, a real
working demo rather than a mock, a `/privacy` page stating what the platform
logs and the 30-day trace retention, and a real LICENSE and SECURITY.md.

Type is IBM Plex Sans for interface and IBM Plex Mono for YAML, action names,
run ids and spans. Icons are five hand-drawn inline SVGs, no library. Motion
only where state genuinely changes, 120ms.

Tables over cards. The data here is tabular and dense: respect it.

---

## 11. Rules that apply to every agent

0. **Read `docs/decisions/FROZEN.md` before `spec/SPEC.md`.** An audit found 25
   places where the spec is ambiguous enough that four agents would each resolve
   it differently. FROZEN.md gives each one a single answer and **overrides
   SPEC.md where they conflict**. Also: the validator prints `ALL PASS` on a
   config with enterprise-locked redaction switched off, so `ALL PASS` means
   "the YAML parses", not "the policy holds".
1. **Read `spec/SPEC.md` sections for your area before writing code.** Guessing
   at merge semantics is the single most expensive mistake available to you.
2. **Never edit `contracts/`** on a workflow branch. Report instead.
3. **Never edit another workflow's directories.** Report instead.
4. **Commit small and often, and push to your own branch.** A laptop that dies
   at 5 AM with uncommitted work costs the demo.
5. **Write the test with the code.** `tests/contract/` for seams, `tests/unit/`
   for your own logic. The resolver's merge rules get a unit test per rule;
   there is no excuse there, the rules are a table in `SPEC.md` §1.
6. **Never print or log a secret.** Redaction wraps model output, tool results,
   traces and logs. A leaked key on the demo screen loses the round.
7. **Say what did not work.** A report claiming success on something untested
   is worse than no report. If you ran out of time, name what is missing.
8. **Do not start stretch goals.** Rows 1 to 10 first, all of them. The stretch
   list in the feasibility report is for after the freeze, which means it is for
   the pitch, not the build.

---

## 12. Cut order, decided in advance

If you are behind, cut from the bottom of this list. This is already decided,
so nobody argues about it at 08:00.

1. Memory buckets and pgvector search (spec §9) — cut first, pitch it
2. `failure-triage` and `cost-optimizer` meta-agents — pitch them
3. Slack connector and the Slackbot agent — GitHub alone carries the demo
4. `security-reviewer` meta-agent — fold into the validator's output
5. Mutation testing (`--mutants`) — cut the feature, keep the slide
6. Analytics page — seeded numbers are acceptable here
7. `agent-builder` meta-agent — demo step 3 falls back to forking by hand

**Never cut:** the resolver and its tighten-only errors, one agent running end
to end, secret redaction, the approvals pause, the publish pipeline with its
human review gate, and the resolved view. Those six are the demo.
