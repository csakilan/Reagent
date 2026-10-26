# ReAgent

Build enterprise AI agents by extending, not rewriting.

## What this is

ReAgent is a self-hosted platform for building, testing, governing and running
AI agents inside a company. An agent here is a single declarative YAML file
with a `.agent` extension. It names the job the agent does, the tools it may
call, the model it runs on, its budget, what triggers it, and the tests it has
to pass before anyone can ship it. It contains no code and no secrets, so it
can be read, diffed, reviewed in a pull request and copied between teams
safely.

The agents in `spec/examples/` are the kind of thing people build with it: a
reviewer that reads every pull request to the infrastructure repositories and
flags bugs, Terraform mistakes and leaked credentials, a bot that answers HR
and handbook questions in a Slack channel, and a scanner that other agents call
to check a diff for secrets.

What makes it a platform rather than a folder of scripts is that the rules an
agent runs under do not live in the agent. They live in two layers above it,
owned by whoever is accountable for them, and the runtime applies them to every
run. An agent cannot grant itself a tool, raise its own spending cap, turn off
secret redaction or skip a human approval, because none of those are its
decision to make.

## What it is for

The brief came out of the NMC2 workshop, and it set three constraints at once:
every team was building its agents from scratch, anyone who needed a new tool
connector had to wait on the central AI team, and software as a service was
ruled out because the data could not leave their own infrastructure.

Those pull against each other. Let teams move on their own and you lose central
control of what agents may touch. Keep control central and you become the
bottleneck everyone waits on. ReAgent separates the two concerns instead of
trading one off against the other: the central AI team owns the policy layers
and the connector registry, and teams build whatever they like inside whatever
those layers leave them. Nobody files a ticket to write an agent, and nobody
can give themselves a permission their team was never granted. The third
constraint is why all of it is self-hosted, on any Postgres and any model
provider.

## Who uses it

Two roles, with a deliberate split of authority between them.

| Role | Owns | Can do |
|---|---|---|
| **Enterprise admin** (the central AI team) | The enterprise layer, every team layer, and the registry of connectors and models | Decide what each team may touch, and change it for every agent at once |
| **Team member** | Their own team's agents | Create, fork, edit, evaluate, publish and run agents, within what their team was given |

Approvers are not a third role. They are team members an admin marks as able to
approve the actions their team's agents pause on.

Members never see the enterprise layer, enterprise-level agents, or the teams
they are not in. The API filters every query by the caller, so another team's
configuration is not merely hidden in the browser, it is never sent.

## What you do with it

1. An admin registers each connector and model once, then writes the enterprise
   layer and one layer per team. A connector is a thin MCP wrapper around an
   internal tool that exposes exactly the actions the registry declares, which
   is how credentials stay in the wrapper process and never reach a model.
2. A team member describes the agent they want in plain English. The
   `agent-builder` meta-agent drafts a valid `.agent` file and its test cases
   using only what that team is allowed, and prefers forking an existing team
   agent over starting from a blank file.
3. They edit the draft. Asking for a tool, a model, a budget or a trigger the
   team was never given is refused as they work, naming the field and the layer
   that blocked it.
4. Publishing starts a seven-stage pipeline. It validates the draft, reviews it
   for least privilege, generates whatever test cases the suite is missing, and
   then stops and waits for a person to approve, edit or reject every new or
   changed test. Only after that does it run the evaluations, check them against
   the thresholds and the previous version, and create an immutable version.
5. The published agent runs from HTTP, MCP, a schedule, a GitHub or Slack
   event, or a call from another agent. Every run re-resolves all three layers,
   checks each tool call against the result, redacts secrets in both directions,
   caps cost, steps and wall-clock time, and pauses for a human before a write
   or destructive action.
6. All of it is recorded. Nested traces per run, every tool call with its allow
   or deny outcome, approvals and who decided them, cost against budget, and
   evaluation results per version.

## The rule that holds it together

What an agent *does* lives in its file. What it is *allowed* to do lives in two
layers above it, an **enterprise** layer and a **team** layer. On every run the
runtime re-reads all three from the database and merges them, and a lower layer
may only ever **tighten** what the layers above allow. Allowlists intersect,
denylists union, the lowest numeric limit wins, safety flags cannot be switched
off, and prompts append in order. A file that tries to widen a rule is rejected
with an error naming the field and the layer that blocked it.

```
Enterprise level  ── holds everything: all connectors, models, policies
   └─ Team         ── a subset of the enterprise (one level, no sub-teams)
       └─ Agent (.agent) ── picks from what its team allows
Registry: connectors and models, added once by enterprise admins
Roles: enterprise admin, team member
```

Because the layers are read on every run and never copied into the agent file,
a policy an admin changes this morning applies to every agent on its next run,
with no redeploy and nothing for a team to opt into.

**Third place, NMC2 track, HackTX 26.** Built over one weekend by four people
against a Sunday-morning code freeze. It was also entered for Best Use of Tiger
Data, which is why the schema leans on hypertables and pgvector rather than
treating Postgres as a plain row store.

---

## Resolution: the tighten-only merge

This is the centre of the project, so it is worth stating precisely. Each field
type merges by its own rule, and the rule depends on what the field means:

| Field type | Rule |
|---|---|
| Allowlists (connector actions, resources, models, trigger types) | Intersection: the child must be a subset of the parent |
| Denylists | Union, and deny beats allow |
| Numeric limits (budget, steps, timeout, call depth, concurrency) | Minimum: a child may go lower, never higher |
| Safety flags (redaction, audit log, trace payloads) | Stricter wins; a flag that is on cannot be turned off below |
| Approval requirements | Union |
| Prompts | Appended, enterprise then team then agent |
| Default eval cases | Appended, so inherited cases always run |
| Free parameters (temperature, max_tokens) | Override, within any bounds above |
| `locked: [paths]` | A locked path may not appear in any lower layer |

Nothing is ever silently clamped. A layer that asks for more than it may have
is refused, and the refusal names the field, the offending document and the
layer that blocked it:

```
E_LIMIT_ABOVE_PARENT   spec.budget.timeout_seconds is 2300, above the limit
                       of 900 set by enterprise.
E_ACTION_NOT_GRANTED   github:pulls.merge is not granted to this team.
                       team:platform-eng grants github:pulls.*, contents.read.
```

The resolver emits two views of one result and they have to agree. The
**provenance** tree tags every leaf with the layer it came from, which is what
the dashboard renders. The **effective** spec is the same result flattened for
enforcement, which is what the runtime reads. `runtime/resolver/merge.py` has
one method per row of the table above, and `tests/unit/test_merge_rules.py`
has 62 tests, one per rule.

---

## Runtime enforcement

A resolved spec is only a document. Four things turn it into a boundary, and
the design of each came out of checking an assumption that turned out to be
wrong. `docs/research/` records those checks against live docs and real
servers.

**Tool permissions are enforced per call, not by filtering the tool list.**
`langchain-mcp-adapters` can narrow tool loading to a server but not to a
subset of that server's actions, so a filtered tool list only keeps ungranted
tools out of the model's context. It is advisory. The real boundary is
`PermissionMiddleware`, which wraps every tool call, checks it against the
resolved grant set, the denylist and the grant's resource globs, and returns a
typed error to the model when it fails. Both layers exist, and the middleware
order matters: permission, then approval, then execution, so a denied call is
refused before an approval is ever queued and a call only counts as executed
once it has really run.

**Secrets are redacted on four paths**, not one: model output, tool results,
trace payloads and logs. Regex rules cover private keys, AWS key pairs, GitHub
and Slack tokens, JWTs, connection strings and password assignments, with PII
rules behind an enterprise flag. Two details matter in practice. The bare
40-character AWS secret shape also matches hashes and paths, so a heuristic
checks the character mix before masking, and card numbers are Luhn-checked so
PR numbers and run ids survive. Hits are reported by rule name and count, never
by the matched text. Secrets redaction cannot be switched off at run time,
because a resolver bug should not be the thing that puts a key on screen.

**The USD budget is enforced at turn boundaries.** OpenRouter returns cost only
in the final chunk of a call, so there is no mid-generation signal to cut on.
`BudgetMiddleware` accumulates cost per completed model call and halts the run
at that boundary. Reading the cost is fussier than the documentation suggests:
the code reads `response_metadata["cost"]` and
`response_metadata["token_usage"]["cost"]`, because the non-streaming calls
`create_agent` makes put it in the second place.

**Step caps, wall-clock timeouts and aggregate caps** complete the set. Steps
use LangChain's own checkpointed counter, so the limit survives an approval
pause. The timeout runs a watchdog that drains the graph, plus a deadline on
each individual tool call, because a step-boundary check alone would let one
hung MCP call sail past the deadline. Monthly spend and concurrent-run caps are
aggregates rather than per-run minimums, so they are checked before the run by
summing the runs table per layer.

---

## Human-in-the-loop approvals

An action's `risk` in the connector registry drives approvals. A rule like
`require_for_risk: [write, destructive]` pauses every matching action for a
human, and an agent or team can add specific actions on top. The pause is real
rather than cosmetic: the run graph interrupts, the pending approval is written
to Postgres, and the HTTP call returns with `status: awaiting_approval`.

Resume works across processes because the run is checkpointed with
`AsyncPostgresSaver`, `durability="sync"` and `interrupt_mode="per_call"`. An
approver can decide from the dashboard minutes later, in a different process,
and the run continues from where it stopped. Rejecting denies the action and
fails the run, and so does an expired approval timeout. Runs that contain no
approval-gated tool cannot pause at all, so they get a throwaway in-memory
checkpointer instead, which saves about eighty database round trips per run.

---

## Agents calling agents

A `sub_agents` entry becomes a tool named `agent:<name>`. The callee runs with
its own resolved permissions intersected with the **root** run's grants, which
is a deliberate correction to the specification: an enterprise service agent
pulled into a team's run must not be able to reach past what that team may
touch. Budget, steps and time flow down as the minimum of the callee's own
limit and the caller's remaining allowance. Depth is capped, cycles are
rejected when the agent resolves, and a cycle that somehow slips through still
dies on the depth cap.

Each child is its own row in `runs` with a `parent_run_id`, and appears as a
nested span in the parent's trace. Cost rolls up at read time rather than being
stored twice, so a sum over the runs table never counts a sub-agent's spend
against its parent as well.

---

## Evaluation

The principle is to grade with code wherever possible and to use a language
model only for what code cannot check, and only after that judge has been
measured against human labels.

Checks run in a fixed order and each is deterministic: the trajectory
(`tool_calls`, `no_tool_calls`), the end state of the mocked sandbox
(`side_effects`, checked against an answer key), leak checks
(`output_not_contains`), and structure (`output_matches_schema`). Cases are
labelled `positive`, `negative` or `safety`, which turns a suite into a
benchmark with a known right answer and yields recall, false-alarm rate and a
safety pass rate that has to be 100%.

Non-determinism is handled rather than ignored. Every case runs k times
(default 5) and the report carries the pass rate with a 95% Wilson confidence
interval, plus pass^k, the share of cases that passed on *all* k runs. Version
comparison only flags a regression when the two intervals do not overlap, so
the platform never reports an improvement that is really noise.

The judge is held to the same standard as the agents. Enterprise admins keep a
calibration set of hand-labelled outputs, and agreement is measured as accuracy
and Cohen's κ. A rubric the current judge is not calibrated for still gets
reported, but it cannot gate a publish and it is excluded from the pass rate. A
case with no code check cannot gate either, and a suite with no gating case at
all is refused with `E_NO_GATING_CHECKS` rather than reporting a pass rate over
an empty set.

Cases run in a mocked sandbox where `mocks[<action>]` is the verbatim tool
result for every call of that action. The real runtime drives the run, so
permissions, budget, step limit and timeout are all still in force; only the
connector tools, the sub-agent tools and the spec under test are swapped. An
unmocked call fails the case rather than silently reaching a real service.

---

## Publish pipeline

Publishing is a gated pipeline with exactly one human gate. An agent cannot be
published until its tests have been generated and a person has reviewed every
new or changed test.

```
1 Validate → 2 Security review → 3 Generate tests → 4 Human review
          → 5 Run evals → 6 Gate → 7 Publish
```

| # | Stage | Passes when |
|---|---|---|
| 1 | Validate | The draft matches the schema and resolves against the current layers |
| 2 | Security review | No blocking findings; advisory findings acknowledged by the author |
| 3 | Generate tests | Coverage is met: every kind of case, and every callable action exercised |
| 4 | Human review | Every new or changed case is approved, edited then approved, or rejected |
| 5 | Run evals | Approved cases plus inherited ones run k times each |
| 6 | Gate | Thresholds met, safety at 100%, no significant regression |
| 7 | Publish | An immutable version is created with the eval report attached |

The review gate is enforced with hashes rather than trust. Each approval is
recorded against a SHA-256 hash of the case's canonical content, so editing a
case after approving it voids the approval and the pipeline fails with
`E_CASE_HASH_MISMATCH`. Continuing while anything is undecided is refused with
`E_UNREVIEWED_CASES`. Rejections need a reason, which is remembered so the
generator does not propose the same case again. Already-approved cases carry
over between pipeline runs, so review effort is spent only on what changed.

Stage 7 re-validates against the layers as they are at that moment. If an
admin tightened a policy while the pipeline was in review and the draft no
longer fits, it fails with `E_LAYERS_CHANGED_MID_PIPELINE` instead of
publishing something that would be refused on its first run. Sub-agent version
ranges are pinned to exact versions at publish, so `secret-scanner@^1` becomes
a fixed `name@x.y.z` in the stored version.

The security review checks four things: destructive actions without approval
(blocking), writes without approval, permissions that neither the prompt nor
any case uses, broad resource globs, and `on_behalf_of` where the service
account would do.

---

## Memory

Memory is a built-in connector with `read`, `search` and `write` actions over
buckets declared in the enterprise and team layers. Bucket names are globally
unique and immutable, and read and write lists are checked against the caller's
team in Postgres rather than from anything the caller claims. An agent's
`memory: [{bucket, mode}]` entry expands into connector permissions before any
check runs, so memory is governed by exactly the same machinery as every other
tool.

The three bucket types behave differently on purpose.

| Type | Holds | Written by |
|---|---|---|
| `semantic` | Facts and knowledge, vector searched | Agents or people with write access |
| `episodic` | What happened on past runs | The runtime only |
| `procedural` | How to do things, so it changes behaviour | Proposed, then promoted only if evals still pass |

Shared memory is a prompt-injection path, so it is defended on both sides. On
the way in, the curator that proposes memories after a run has **no tools at
all**: it reads the already-redacted trace and proposes, and the module itself
validates and performs every write. That makes "episodic is runtime-only" true
by construction rather than by policy, and it means a prompt-injected trace
cannot cause a write. Proposals are kept only when grounded in real trace
events, above a confidence floor, free of directives and secrets, and not a
near-duplicate. Procedural lessons are always written pending, and are promoted
only if every agent reading that bucket still passes its evals with the lesson
in context.

On the way out, recall happens before the graph runs, reads only buckets in the
run's grant set, drops anything matching the injection patterns or carrying a
secret, clips each item and the whole block, and arrives as its own user
message so it is checkpointed and never re-recalled on an approval resume.
Every outcome, including what was dropped and why, lands as a trace event on
the source run.

---

## Connectors

A connector is a permission to an internal tool, and each one is a thin MCP
wrapper the project writes rather than a direct API binding. The wrapper
exposes exactly the actions the registry declares, and credentials are injected
into the **wrapper process**, never into the model context or the agent's
environment. The repository ships wrappers for GitHub, Slack and memory, plus
a `platform` connector served by the runtime itself.

Identity is the subtle part. The wrappers trust the runtime, not the model, so
`tools/call` requires a shared-secret bearer token and the identity headers are
computed once at the root run and inherited by every child. The memory wrapper
checks team membership and admin status in the database rather than believing a
caller-supplied admin claim. GitHub renews its App installation token hourly
and keeps the private key on the wrapper host.

Two details that cost real time and are worth stating plainly. MCP tool names
must match `^[a-zA-Z0-9_-]{1,64}$`, so a dotted action name like `pulls.comment`
is not a legal tool name, and Claude Code silently rewrites the dot rather than
erroring. The mapping between the three spellings is therefore read from
`contracts/connector_actions.json` and never recomputed anywhere:

| Layer | Value |
|---|---|
| Permission string in YAML, resolver and database | `github:pulls.comment` |
| MCP tool name on the wrapper | `pulls_comment` |
| What an MCP client sees | `mcp__github__pulls_comment` |

Eighteen of the thirty action names differ from their wire name. The runtime
also refuses to start a run whose granted action is missing from its server,
with `E_CONNECTOR_DRIFT`, so a wrapper and the registry cannot quietly diverge.

---

## Meta-agents and the platform connector

Five agents ship with the platform at enterprise level with `access: run_only`,
so any user can run them while only admins can see their configuration. Each
run is scoped to the caller's team and charged to it.

| Meta-agent | Does |
|---|---|
| `agent-builder` | Drafts a valid `.agent` file and its eval cases from a plain-English request, preferring to fork an existing team agent |
| `evaluator` | Generates eval cases, acts as the judge, and grades sampled production runs |
| `cost-optimizer` | Reruns a suite across a grid of allowed models and recommends the cheapest setup that still passes |
| `security-reviewer` | Checks a spec for least privilege before it can be published |
| `failure-triage` | Groups failed and denied runs by cause, explains each, and proposes a fix |

`secret-scanner` is a sixth, with `access: service`, callable as a sub-agent by
any agent. Meta-agents reach the platform through the built-in `platform`
connector, which can only be granted at enterprise level and whose every action
is filtered by the caller exactly as the HTTP routes are.

---

## Finding an agent by what it achieves

`GET /v0/agents?q=` ranks by outcome rather than by implementation. The query
is scored with BM25 against an outcome card and never against the prompt, model
or budget, weighting `desired_outcome` highest, then the description, when to
use it, what its evals assert, and its tool actions written as plain effects. A
small concept map means "leaked keys" finds "credentials". Measured results
only break ties, moving a score by at most ten percent, so a well-tested agent
never outranks the right one. Weak matches are dropped rather than padded.

---

## Dashboard

Nine screens, all reading the same API the CLI uses.

| Screen | Shows |
|---|---|
| Getting started | The guided path through the demo |
| Hierarchy | The configuration a caller is allowed to see, filtered server-side, with the resolved view beside it: every effective value tagged with the layer it came from |
| Your agents | The team's collection, with fork and open |
| Agent editor | Editing a draft, with rejections shown inline as you widen a rule |
| Publish pipeline | The seven stages as a CI run, with the case review gate and a dry-run button |
| Trace viewer | Runs, nested spans, each tool call allowed or denied, redaction hits |
| Approvals | The pending queue, with approve and reject |
| Enterprise admin | Team layers, the registry, connector requests, impact checking |
| Privacy | What the platform logs, what it never stores, and the retention window |

The visual rules are tight on purpose, because this had to look like an
internal tool rather than a generated template. Colour carries exactly two
meanings, which layer a value came from and whether an action was permitted,
and there is no decorative colour anywhere. Tailwind's default palette is
deleted from the configuration so it cannot be reached for by accident. Type is
IBM Plex Sans with IBM Plex Mono for YAML, action names and run ids. The icons
are five hand-drawn inline SVGs with no icon library. Tables are preferred over
cards because the data is dense and tabular. Every screen that fetches has a
skeleton loader, and there is a real privacy page stating what is logged and
for how long, alongside a real licence and security policy.

---

## Observability

Every run records its own and its parent's id, agent and version, team,
trigger, acting user, model, token counts, cost from OpenRouter, latency, step
count, and every tool call with its action, resource, allow-or-deny outcome and
duration, plus approvals and their decisions, retries, errors, redaction hits
and policy violations. Everything passes through redaction before storage, and
a tool call keeps a short summary rather than a full response. Traces are
retained for 30 days.

---

## Data model

The schema is written for Tiger Data and tested rather than sketched. `runs`
and `run_events` are hypertables partitioned on time, which is what makes the
analytics queries over two weeks of history cheap. `agent_versions` and
`memory_items` carry pgvector columns with HNSW indexes for semantic search.

Searchable columns are generated from the YAML, which is the single source of
truth, so they cannot drift from it. Two corrections in there are worth
knowing if you touch it. The `search` tsvector has to be built with an explicit
regconfig, because `to_tsvector(text)` is only STABLE while
`to_tsvector(regconfig, text)` is IMMUTABLE and a generated column requires
immutability. The `embedding` column deliberately is **not** generated, since a
generated vector column would need an embedding call inside the database; a
stored hash detects a stale vector after a spec edit instead.

One deployment note: the Docker image has to be a Timescale image with vector
added, never the pgvector image with Timescale added. The latter has an empty
`shared_preload_libraries` and `create_hypertable` fails.

---

## Setup

Requires Python 3.11+ and, for the dashboard, Node 20+.

```bash
git clone https://github.com/csakilan/Reagent.git
cd Reagent
cp .env.example .env          # fill in DATABASE_URL and OPENROUTER_API_KEY
python scripts/envs.py
```

### Why that creates two virtualenvs

This is deliberate and it cannot be collapsed into one. The runtime is an MCP
**client** and the connectors are MCP **servers**, and they need incompatible
versions of the `mcp` package:

| | needs | because |
|---|---|---|
| `runtime/` | `mcp` 1.x | `langchain-mcp-adapters==0.3.2` pins `mcp<2.0.0` |
| `connectors/` | `mcp` 2.x | uses `from mcp.server import MCPServer`, which is the v2 API |

`pip install -r` of both files fails with `ResolutionImpossible`, and no pin
fixes it: `langchain-mcp-adapters` 0.3.1 drops the upper bound but then fails to
import against `mcp` 2.x. They are separate processes that talk over HTTP, and
the v2 server serves both protocol eras routed by the `MCP-Protocol-Version`
header, so the split is harmless. `tests/contract/test_two_environments.py`
guards it, and `python scripts/envs.py --verify` checks it.

```
.venv           connectors.  mcp 2.x, no langchain.    Runs the wrappers.
.venv-runtime   runtime.     mcp 1.x + langchain.      Runs the API, CLI, tests.
```

## Running it

Apply the schema, then start the wrappers and the runtime from their own
environments. On Windows substitute `.venv\Scripts\python.exe`.

```bash
# 1. schema (once)
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f contracts/db/001_init.sql

# 2. the three MCP wrappers, from the connectors environment
python scripts/wrappers.py                     # 7100 memory, 7101 slack, 7102 github

# 3. the runtime, from the runtime environment, in another terminal
.venv-runtime/bin/python -m uvicorn runtime.api.runs:app --loop asyncio:SelectorEventLoop --port 8000

# 4. the dashboard
cd dashboard && npm ci && npm run dev          # 3000
```

`--loop asyncio:SelectorEventLoop` is what lets the runtime use Postgres on
Windows. `python scripts/doctor.py --fix` checks a machine's toolchain, and
`python scripts/demo_reset.py` rebuilds a self-contained `demo` schema with
three teams, nine agents and two weeks of run history.

## The CLI

The CLI mirrors the API and holds no logic of its own. The acting principal
comes from `REAGENT_USER`, never from a request body, and any rejection is
printed verbatim with a non-zero exit.

```bash
python -m cli.reagent validate spec/examples/agents/platform-eng/pr-reviewer.agent
python -m cli.reagent run pr-reviewer --input '{"repo":"nmc2/infra","pr_number":42}'
python -m cli.reagent eval pr-reviewer --runs 5
python -m cli.reagent publish pr-reviewer      # starts the pipeline, prints the review link
python -m cli.reagent fork code-reviewer --as my-reviewer
```

`validate --local` runs in process with no server, for CI.

## Tests

```bash
.venv-runtime/bin/python -m pytest tests -q
```

`tests/` holds 273 test functions, 220 unit and 53 contract, across 24 files,
with 21 more in `connectors/tests/`. The connector contract tests need the
wrappers running (`python scripts/wrappers.py`). They skip cleanly rather than
fail when a wrapper is down, so a skipped seam means "not up yet" while a failed
one means "both sides exist and disagree".

| Suite | Covers |
|---|---|
| `tests/unit/test_merge_rules.py` | 62 tests, one per row of the tighten-only merge table |
| `tests/unit/test_runtime_guardrails.py` | Redaction, permission denial, budget, steps, timeout |
| `tests/unit/test_evals.py` | Code checks, the judge, Wilson intervals |
| `tests/unit/test_publish.py` | The seven-stage pipeline and its human gate |
| `tests/unit/test_memory_*.py` | Recall, curation, the injection filters, the procedural gate |
| `tests/contract/` | The seams between the four workstreams |

Four GitHub Actions workflows cover it: `test.yml` runs the spec validator, the
schema and the unit tests, `integration.yml` runs the contract seams,
`contracts-guard.yml` fails any change to `contracts/` from a workflow branch,
and `lock-check.yml` enforces the integration locks described below.

## Layout

```
contracts/      frozen shapes shared across the codebase: resolved spec, DDL,
                OpenAPI, run events, connector action names, 45 error codes
runtime/        resolver, engine, guardrails, evals, publish pipeline, memory, db
connectors/     MCP wrappers for github, slack and memory
dashboard/      Next.js app
cli/            reagent validate | run | eval | publish | fork
spec/           SPEC.md, JSON Schema, 20 example documents, validator
docs/research/  external facts verified against live docs and real servers
docs/decisions/ FROZEN.md: 25 spec ambiguities, each with one answer
```

`spec/SPEC.md` is the full specification. `docs/decisions/FROZEN.md` overrides
it where they conflict, and says why in each case. An audit found 25 places
where the spec was ambiguous enough that four people would each resolve it
differently, so each one was given a single answer and a home in a test.

## How it was built

Four people and four Claude agents worked in parallel on four branches against
a hard deadline, on one macOS and three Windows laptops. Three things made that
work and are visible throughout the repository.

`contracts/` is frozen and CI-enforced, because a contract change silently
breaks everyone else. Every boundary has a fake, so no workstream was ever
blocked waiting on another, and the fake and the real thing satisfy the same
contract test. A handoff board (`.handoffs.yaml`, driven by
`scripts/handoff.py`) made "this is ready, here is who is unblocked" an
explicit step with a verification command attached, rather than something
shouted across a table.

The mixed fleet also left marks worth knowing before you edit anything: every
script is Python because Windows cannot run a shell script in the build path,
the word is always `python` and never `python3`, line endings are pinned in
`.gitattributes`, and paths go through `pathlib`.

## Honest limits

Some things are deliberately partial, and the code says so where they are.

Mutation testing (`reagent eval --mutants`) is cut; it reports no score and
says why. Stages 2 and 3 of the publish pipeline run deterministically in
`runtime/publish/review.py` rather than through the `security-reviewer` and
`evaluator` meta-agents, with the same contract on both sides. Two platform
connector actions, `get_runs` and `record_grade`, answer `E_NOT_AVAILABLE`
while still being listed, because an unlisted granted action would fail a run
with `E_CONNECTOR_DRIFT`. Webhook triggers are declared in the schema and
resolved, but no receiver is wired up, so runs start over HTTP or MCP.

## Not in this version

Data classification and model routing by sensitivity, signed `.agent` files,
SSO, Postgres row-level security, sandboxed execution, and self-hosted
inference. `SPEC.md` §16 has the reasoning for each.
