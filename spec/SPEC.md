# ReAgent spec v0: the `.agent` format

ReAgent separates **what an agent does** (its `.agent` file) from **what it is allowed to do** (the layers above it). The runtime merges both on every run.

```
Enterprise level  ── holds everything: all connectors, models, policies
   └─ Team         ── a subset of the enterprise (one level; no sub-teams)
       └─ Agent (.agent) ── picks from what its team allows
Registry: Connectors + Models, added once by enterprise admins
Roles: enterprise admin, team member
```

## 0. Document kinds and files

| Kind | File | Owned by | Purpose |
|---|---|---|---|
| `Enterprise` | `enterprise.yaml` | Enterprise admins (AI team) | Everything: connector and model grants, enterprise prompt, policies, limits, default evals, memory, observability |
| `Team` | `teams/<name>.team.yaml` | Enterprise admins | A subset of the enterprise, plus team prompt, rules, memory, members |
| `Connector` | `registry/connectors/<name>.connector.yaml` | Enterprise admins | An internal tool (our custom MCP wrapper) and every action it supports |
| `Model` | `registry/models/<id>.model.yaml` | Enterprise admins | A model available through OpenRouter, with its OpenRouter ID |
| `Agent` | `agents/<team>/<name>.agent` | Team members (team agents); enterprise admins (enterprise agents) | What the agent does |

Every document has the same envelope:

```yaml
apiVersion: reagent/v0
kind: Agent              # Enterprise | Team | Connector | Model | Agent
metadata:
  name: pr-reviewer      # lowercase a-z 0-9 -
  version: 1.0.0         # semver; required for Agent, Connector, Model
  description: ...
spec: { ... }
```

`.agent` files are YAML with a `.agent` extension. Add a VS Code file association (`"files.associations": {"*.agent": "yaml"}`) so they get YAML highlighting.

---

## 1. Resolution: tighten-only inheritance

On **every run**, the runtime loads the agent's three layers (enterprise → team → agent) from the database and merges them. It never trusts layer data copied into a file. A policy change therefore reaches every agent on its next run.

Lower layers can **narrow** what upper layers allow, never **widen** it. If they try, the resolver fails with an error naming the field and the layer that blocked it. It never silently clamps.

| Field type | Rule |
|---|---|
| Allowlists (connector actions, resources, models, trigger types) | **Intersection**: the child must be a subset of the parent |
| Denylists | **Union** |
| Numeric limits (budget, steps, timeout, call depth, concurrency) | **Minimum**: the child may set a lower value, never a higher one |
| Safety flags (redaction, audit) | **Stricter wins**: a flag that's on can't be turned off below |
| Approval requirements | **Union** |
| Prompts | **Appended**: enterprise → team → agent task prompt |
| Default evals | **Appended**: inherited eval cases always run alongside the agent's own |
| Free parameters (temperature, max_tokens, retries) | **Override**, within any bounds set above |
| `locked: [paths]` | A locked path can't appear in any lower layer |

**What an omitted field means:**
- **Connectors must be granted explicitly to each team.** A team that doesn't list a connector has no access to it, which is how a team ends up with only a subset of the enterprise.
- **Models, triggers, limits and guardrails** are inherited from the enterprise when a team omits them.
- **An agent gets no tool permissions it doesn't list**, so agents are least-privilege by default.

The **resolved view** is the merged result, with each value tagged by the layer it came from (`enterprise`, `team:<name>`, `agent`, `default`).

---

## 2. Visibility and access

There are two roles:

| Role | Can see | Can change | Can run |
|---|---|---|---|
| **Enterprise admin** | Everything | The enterprise layer, every team layer (members, connector grants, models, guardrails, limits, prompt, memory), the registry, enterprise agents, and any team agent | Everything |
| **Team member** | Own team's agents and the connector actions granted to the team | Create, fork, edit, evaluate and publish agents in their own team | Own team's agents, meta-agents |

- Team layers are set **only by enterprise admins**. Members build agents inside the limits they're given.
- **Approvers** aren't a role. They're a list of team members, set by enterprise admins in the team layer, who can approve paused actions for that team's agents.
- Members never see the enterprise layer, enterprise-level agents, or connectors and actions their team wasn't granted. The API filters every query by the caller's team; the dashboard just renders what comes back.
- **Enterprise-level agents** (`metadata.level: enterprise`) set `metadata.access`:
  - `private`: hidden from everyone but enterprise admins.
  - `run_only`: **meta-agents.** Any user can run them from the dashboard, but their config is hidden.
  - `service`: any agent can call it as a sub-agent (for example `secret-scanner`), but its config is hidden.
- **Enterprise-level agents run on behalf of the caller.** A meta-agent run is scoped to the calling user's team. Its effective tools are its own tools **∩** the caller's team grants, plus the built-in `platform` connector, whose actions are themselves filtered by the caller's team. It sees only what that team can see, and its output never includes enterprise-level configs.
- The built-in `platform` connector can be granted only at the enterprise level, so only enterprise agents can use it.

There are **no templates.** Inheritance already gives a new agent its enterprise and team setup. New agents start from a blank file, fork an agent in the team's collection (`metadata.forked_from`), or are drafted by the agent builder.

---

## 3. Registry

### Connector

A connector is a permission to an internal tool. Each one is a **custom MCP wrapper** we write: a thin server around the tool's API that exposes exactly the actions listed here, with these exact names. Enterprise admins register each connector once, as broadly as possible.

```yaml
apiVersion: reagent/v0
kind: Connector
metadata: { name: slack, version: 1.0.0, description: Company Slack workspace }
spec:
  transport: { type: mcp, url: http://localhost:7101/mcp }   # our wrapper
  auth:
    service:      { env: SLACK_BOT_TOKEN }    # agent acts as the bot account
    on_behalf_of: { env: SLACK_USER_TOKEN }   # runtime fills it per run with the triggering user's login
  actions:
    - { name: messages.read, risk: read,  resources: [channel] }
    - { name: messages.post, risk: write, resources: [channel] }
    - { name: dm.read,       risk: read }
```

- Permission strings are `<connector>:<action>` and allow globs: `slack:*`, `slack:messages.*`. Resource scopes add a restriction: `{ channel: ["#eng-*"] }`.
- Credentials are injected into the **wrapper**, never into the model context or the agent's environment.
- With `on_behalf_of`, the effective permission is the agent's resolved permissions **∩** the triggering user's own access.
- `risk` drives approvals: an enterprise or team rule like `require_for_risk: [write]` pauses every write action for human approval.

### Model

```yaml
apiVersion: reagent/v0
kind: Model
metadata: { name: claude-sonnet, version: 1.0.0 }
spec:
  provider: openrouter
  model: anthropic/claude-sonnet-4.5      # OpenRouter model ID
  context_window: 200000
  supports_tools: true
```

Cost isn't stored here. OpenRouter returns `usage.cost` on every response, and the runtime records it per run. In production, `provider` would point at NMC2's own inference endpoint instead.

---

## 4. Enterprise

```yaml
apiVersion: reagent/v0
kind: Enterprise
metadata: { name: nmc2 }
spec:
  prompt: |
    You are an internal NMC2 agent. Never reveal credentials...
  models:     { allow: ["*"] }
  connectors:
    github: { allow: ["*"], deny: [repos.delete] }
    slack:  { allow: ["*"] }
  triggers:   { allow: [http, mcp, schedule, github, slack, agent] }
  guardrails:
    redaction: { secrets: true, pii: true }
    approval:  { require_for_risk: [destructive] }
    audit_log: true
  limits:
    usd_per_month: 1000
    usd_per_run: 2.00
    max_steps: 100
    timeout_seconds: 900
    max_call_depth: 3
    max_concurrent_runs: 50
  evaluation:
    default_cases: [...]          # appended to every agent's evals (secret leak, prompt injection)
    judge: { model: claude-sonnet, votes: 3 }   # LLM judge; see §7
    requirements:                 # coverage the publish pipeline enforces (§11)
      min_cases: { positive: 1, negative: 1, safety: 1 }
      tool_coverage: true         # every tool action in the spec is exercised by at least one case
  memory:
    buckets:
      - { name: enterprise-kb, type: semantic, read: ["*"], write: [admins] }
  observability:
    log_retention_days: 30
    trace_payloads: redacted      # redacted | metadata_only
  locked: [guardrails.redaction, guardrails.audit_log, observability.trace_payloads]
```

## 5. Team

```yaml
apiVersion: reagent/v0
kind: Team
metadata: { name: platform-eng, description: Platform Engineering }
spec:                             # written by enterprise admins only
  members: [alice, bob]
  approvers: [alice]              # members who can approve paused actions
  prompt: "We write Go and Terraform. Follow docs/style-guide.md."
  models: { allow: [qwen-coder, claude-sonnet] }
  connectors:
    github: { allow: ["pulls.*", contents.read], resources: { repo: ["nmc2/*"] } }
  guardrails:
    approval: { require_for_risk: [write, destructive] }
  limits: { usd_per_month: 200, max_concurrent_runs: 5 }
  memory:
    buckets:
      - { name: platform-eng-notes, type: semantic, read: [platform-eng], write: [platform-eng] }
```

---

## 6. Agent (`.agent`)

The top-level `spec` keys follow the team's field list: **use case, tools, model, budget, ownership, evaluation, triggers, guardrails, prompt, memory, observability**. Sub-agents, failure handling and I/O schemas complete it.

```yaml
apiVersion: reagent/v0
kind: Agent
metadata:
  name: pr-reviewer
  version: 1.0.0
  level: team                      # team | enterprise
  team: platform-eng               # required when level: team
  forked_from: code-reviewer@1.2.0 # optional lineage
  tags: [code-review, github]
spec:
  use_case:
    description: Reviews pull requests for bugs, style issues and leaked secrets.
    when_to_use: On every PR to nmc2/infra and nmc2/platform-api.
    desired_outcome: Every PR gets an accurate review before a human looks at it; leaked secrets are always flagged.
  tools:                           # connector permissions (allowlist)
    - connector: github
      actions: [pulls.read, pulls.comment]
      resources: { repo: [nmc2/infra] }
      auth: service                # service | on_behalf_of
  sub_agents:
    - ref: secret-scanner@^1
  model:
    id: qwen-coder                 # registry Model name
    fallback: claude-sonnet        # optional, used on model errors
    params: { temperature: 0.2, max_tokens: 4096 }
  budget: { usd_per_run: 0.25, usd_per_month: 50, max_steps: 25, timeout_seconds: 240 }
  ownership:
    owners: [alice]
    maintainers: [bob]
  evaluation:
    runs_per_case: 3
    thresholds: { pass_rate: 0.9, max_usd_per_run: 0.10 }
    cases: [...]                   # see §7
    production: { sample_rate: 0.2 }   # evaluation agent grades 20% of real runs
  triggers:
    - { type: github, events: [pull_request.opened], repos: [nmc2/infra] }
    - { type: http }
    - { type: mcp }
  guardrails:
    approval: { actions: ["github:pulls.comment"] }
    deny: ["github:pulls.merge"]
  prompt:
    task: |
      Review the pull request...
  memory:
    - { bucket: platform-eng-notes, mode: read }
  observability:
    metrics: [issues_found, secrets_flagged]     # custom counters the agent reports
    alerts:
      - { when: "failure_rate > 0.2 over 1h", notify: ["slack:#platform-eng-alerts"] }
  failure:
    retries: 2
    retry_on: [timeout, model_error, connector_error]
    on_failure: { notify: ["slack:#platform-eng-alerts"] }
  input:  { type: object, properties: { repo: {type: string}, pr_number: {type: integer} }, required: [repo, pr_number] }
  output: { type: object, properties: { summary: {type: string}, issues: {type: array} } }
```

Notes:
- **Model:** one model per agent. To combine models, use sub-agents.
- **Prompt:** only the task prompt lives in the `.agent`. The enterprise and team prompts are prepended at run time. Reference material belongs in `memory` or the task prompt, not in run input.
- **Input** is per-run data, such as a PR number, validated against `input`.
- **Triggers:** `http`, `mcp`, `schedule` (`cron:`), `github` (webhook `events`, `repos`), `slack` (`events`, `channels`), `agent` (callable only by other agents).
- **Failure:** the builder chooses retries, fallback and notifications. The platform still enforces timeouts, the budget kill-switch, step limits and call depth.

---

## 7. Evaluation

**Principle:** grade with code wherever possible. Use the LLM judge only for what code can't check, and only after the judge has been checked against human labels.

```yaml
evaluation:
  runs_per_case: 5
  thresholds: { pass_rate: 0.9, max_usd_per_run: 0.10, max_p95_seconds: 30 }
  cases:
    - name: flags-leaked-aws-key
      kind: positive                 # positive (a known defect to find) | negative (must not false-alarm) | safety (must never fail)
      input: { repo: nmc2/infra, pr_number: 42 }
      mocks: { "github:pulls.read": { diff: "+aws_secret_key = \"...\"" } }   # the sandbox the agent acts in
      expect:
        tool_calls: ["github:pulls.read", "agent:secret-scanner"]          # trajectory (code)
        no_tool_calls: ["github:pulls.merge"]                              # forbidden actions (code)
        side_effects:                                                      # end state of the sandbox (code)
          - { action: "github:pulls.comment", args_contains: ["infra/main.tf", "secret"] }
        output_not_contains: ["wJalrXUtnFEMI"]                             # leak check (code)
        output_matches_schema: true                                        # structure (code)
        judge: "Does the review recommend rotating the credential?"        # quality (LLM judge)
        reference: "Line 12 of infra/main.tf commits an AWS secret key; it must be rotated."   # answer key the judge grades against
  production: { sample_rate: 0.2 }
```

### Four layers of checking, from most to least objective

1. **Code checks (deterministic).** Trajectory (`tool_calls`, `no_tool_calls`), end state of the sandbox (`side_effects`: what the agent actually *did* to the mock GitHub or Slack, checked against an answer key), leaks (`output_not_contains`) and structure (`output_matches_schema`). Same input, same verdict, every time.
2. **Ground-truth benchmarks.** Each agent's cases form a labeled set: `positive` cases with a planted, known defect and `negative` clean cases. From these the runtime reports **recall** (planted defects found), **false-alarm rate** (clean cases flagged) and **safety** (`safety` cases must pass at 100%). These are objective numbers with a known right answer.
3. **LLM judge, only for open-ended quality.** It asks one binary rubric question per case, compares the output against the `reference` answer key, takes a majority of `votes` (default 3), and runs on a model from a different provider than the agent's, to limit self-preference.
4. **Production grading.** The evaluation agent grades a `sample_rate` share of real runs against `desired_outcome`. That produces the production **task success rate**. **Human intervention rate** is measured independently, from rejected approvals and manual reruns, and acts as a cross-check on the judge.

### Handling non-determinism

- Every case runs `runs_per_case` times (default 5). The runtime reports:
  - **pass rate** with a 95% Wilson confidence interval;
  - **pass^k**, the share of cases that pass on *all* k runs, which measures consistency rather than luck.
- **Comparing versions:** a regression is flagged only when the confidence intervals of the two versions don't overlap. Otherwise the result is "no significant change", so the platform never claims an improvement that's really noise.

### Evaluating the evaluator

- **Judge calibration.** Enterprise admins keep a calibration set: agent outputs hand-labeled pass or fail by humans (`enterprise.spec.evaluation.judge.calibration`). On every judge or rubric change, the runtime measures the judge's **agreement with the human labels**, as accuracy and Cohen's κ. If agreement falls below `min_agreement`, judge results are marked "uncalibrated" and **can't block or approve a publish**. Only code checks gate in that state.
- **Mutation testing.** `reagent eval --mutants` generates deliberately broken versions of an agent: a tool removed, the key instruction deleted, the model swapped for a much weaker one, the prompt scrambled. It then reports the **mutation score**, the share of broken versions the eval suite catches. A low score means the evals are too weak to trust, no matter what the pass rate says.

### Metrics recorded per version

Pass rate (with CI), pass^k, recall, false-alarm rate, safety pass rate, tool-call accuracy, production task success, human intervention rate, judge agreement, mutation score, cost per run, and p95 latency.

---

## 8. Agents calling agents

`sub_agents` exposes each referenced agent to the caller as a tool named `agent:<name>`.

1. **The callee runs with its own resolved permissions.** It never inherits the caller's connectors.
2. **Limits flow down, tighten-only.** The callee gets `min(its own budget, the caller's remaining budget)`, and the same for steps and time.
3. **Depth limit and no cycles.** Calls are capped at `max_call_depth`, and cycles are rejected when the agent is resolved.
4. **Who can be called:** agents in the caller's own team, or enterprise agents with `access: service`.
5. **`on_behalf_of` propagates** only if the callee also declares it for the relevant permission.
6. **Tracing:** child runs are nested spans in the parent's trace, and costs roll up to the parent.

---

## 9. Memory

Memory is the built-in `memory` connector (actions `read`, `search`, `write`). Buckets are declared in the Enterprise and Team layers and have a `type`:

| Type | Holds | Written by |
|---|---|---|
| `semantic` | Facts, FAQs, knowledge (vector search) | Agents or people with `write` |
| `episodic` | What happened on past runs | **The runtime only**, built automatically from run logs |
| `procedural` | How to do things; changes behavior | Agents may propose an entry; it's applied only after the agent's evals pass again |

In a bucket's `read` or `write` list, a team name means that team's members and agents, `"*"` means everyone, and `admins` means enterprise admins. An agent's `memory: [{bucket, mode}]` entry is shorthand for memory connector permissions on that bucket. Bucket `read` and `write` lists are checked as well. Shared memory is a prompt-injection path, so writes are redacted, and `source_run_id` records which run wrote each entry.

---

## 10. Meta-agents (ship with the platform)

All are `level: enterprise, access: run_only`. Every user can run them from the dashboard, only enterprise admins can see their configs, and each run is scoped to the caller's team.

| Meta-agent | Does |
|---|---|
| `agent-builder` | Drafts a valid `.agent` file and its eval cases from a plain-English request, using only what the caller's team allows. Prefers forking an existing team agent. Requests a connector if one is missing. |
| `evaluator` | Generates eval cases (positive, negative and safety, with mocks and answer keys) in stage 3 of every publish (§11), acts as the LLM judge, grades sampled production runs (task success), and reports regressions. It is held to its own calibration set (§7). |
| `cost-optimizer` | Reruns an agent's evals across a grid of models and parameters (from the team's allowed models) and recommends the cheapest setup that meets the thresholds. |
| `security-reviewer` | Runs before every publish. Flags tool permissions the prompt and eval cases never use, write or destructive actions without approval, broad resource globs, and `on_behalf_of` where `service` would do. Proposes a narrower spec. |
| `failure-triage` | Groups failed and denied runs by cause (connector errors, budget cutoffs, permission denials, judge failures), explains each group, and proposes a spec fix or a new eval case. |

`secret-scanner` is an `access: service` enterprise agent that any agent can call.

Meta-agents reach the platform through the built-in **`platform` connector**. Its actions include `resolve_team`, `search_agents`, `validate`, `run_evals`, `get_runs`, `propose_agent` and `request_connector`, and every one of them is filtered by the caller's team.

---

## 11. Publish pipeline (CI/CD for agents)

Publishing is a gated pipeline. **An agent can only be published after its eval tests have been generated and a human has reviewed every new or changed test.**

```
 1 Validate ─► 2 Security review ─► 3 Generate tests ─► 4 Human review ─► 5 Run evals ─► 6 Gate ─► 7 Publish
     │                │                    │                  ▲  │               │            │
     └── fail: fix the draft ◄─────────────┴──────────────────┘  └─ reject/edit ─┘            └─ below threshold: back to draft
```

| # | Stage | Done by | Passes when |
|---|---|---|---|
| 1 | **Validate** | Runtime | The draft matches the schema and resolves against its enterprise and team layers (tighten-only rules, granted connectors and models) |
| 2 | **Security review** | `security-reviewer` meta-agent | No blocking findings. Advisory findings must be acknowledged by the author |
| 3 | **Generate tests** | `evaluator` meta-agent | Coverage requirements are met: at least the enterprise minimum of `positive`, `negative` and `safety` cases, and every tool action in the spec exercised by at least one case. Unchanged, already-approved cases carry over; the evaluator proposes cases only for what's new or changed |
| 4 | **Human review** | A team member | **Every** new or changed case is approved, edited then approved, or rejected. Each case shows its input, mocked sandbox, code checks, judge rubric and answer key, plus a **Dry run** button that runs it once so the reviewer sees real agent output |
| 5 | **Run evals** | Runtime | All approved cases plus inherited `default_cases` run `runs_per_case` times: code checks, then the judge (if calibrated) |
| 6 | **Gate** | Runtime | Thresholds met; results compared with the previous version (pass rate with CI, recall, false-alarm rate, safety at 100%, cost, p95) |
| 7 | **Publish** | Runtime | An immutable version is created, and the eval report is attached to it |

Rules:
- **No unreviewed tests run, and nothing publishes with unreviewed tests.** Approval is recorded per case against a hash of its content. Editing a case afterward invalidates the approval.
- **Cases written by hand** go through the same review screen. The author can approve them in bulk.
- **Rejected cases** are dropped. The rejection reason is fed back to the evaluator, so it doesn't propose the same case next time.
- **Inherited `default_cases`** (enterprise and team) are reviewed by enterprise admins once, when they're written. Members see only their names and results, not their contents.
- **Who reviews:** for the demo, any member of the team, including the author. The production setting `require_second_reviewer: true` would require someone other than the author.
- The same pipeline runs for **forks** and for drafts produced by the **agent builder** or **cost optimizer**. Meta-agents can propose changes but never publish.
- Every pipeline run is stored with its stage results, so the dashboard shows it like a CI run: stages, logs, who reviewed what, and the final report.

## 12. Versioning and forks

- Publishing creates an immutable version. Its lifecycle status (`draft`, `published`, `deprecated`) is stored in the database, not in the file.
- `forked_from: <name>@<version>` records lineage. When the source agent publishes a newer version, the fork shows "Update available" with a field-level diff, and the builder chooses whether to merge it.
- Changes to the Enterprise or Team layers are **not** opt-in. They apply on every agent's next run.

---

## 13. Data model (Tiger Data / Postgres)

```
enterprise       (id, name, spec JSONB)                          -- one row
teams            (id, name, spec JSONB)                          -- one level, no nesting
users            (id, name, email, is_enterprise_admin BOOL)
team_members     (team_id, user_id, is_approver BOOL)
registry_items   (id, kind, name, version, spec JSONB, status, created_by, created_at)   -- connector | model
connector_requests (id, team_id, requested_by, description, status, connector_id NULL, created_at)
agents           (id, name, level, team_id NULL, access, current_version_id, created_at)
agent_versions   (id, agent_id, version, status, spec JSONB, forked_from_version_id NULL,
                  published_by, published_at,
                  description TEXT GENERATED (spec#>>'{spec,use_case,description}'),
                  desired_outcome TEXT GENERATED (spec#>>'{spec,use_case,desired_outcome}'),
                  search TSVECTOR GENERATED, embedding VECTOR)
runs             (id, parent_run_id NULL, agent_version_id, team_id, trigger, triggered_by, model,
                  status, input_tokens, output_tokens, cost_usd, latency_ms, error, started_at, ended_at)
                  -- time-series table for the analytics page
run_events       (id, run_id, seq, type, action, allowed, payload_redacted JSONB, ts)
approvals        (id, run_id, action, requested_at, approver_id, decision, decided_at)
eval_runs        (id, agent_version_id, kind, pass_rate, pass_rate_ci_low, pass_rate_ci_high, pass_k,
                  recall, false_alarm_rate, safety_pass_rate, mutation_score, avg_cost, p95_latency,
                  results JSONB, created_at)                     -- kind: ci | production | mutation
judge_calibration (id, judge_model, rubric_hash, agreement, kappa, n_labels, created_at)
calibration_labels (id, agent_version_id, case_name, output_redacted, human_verdict, labeled_by)
publish_runs     (id, agent_id, draft_version_id, stage, status, started_by, started_at, finished_at,
                  stage_results JSONB)                           -- stage: validate | security | generate | review | evals | gate | published
case_reviews     (id, publish_run_id, case_name, case_hash, origin, decision, edited_case JSONB,
                  reason, reviewed_by, reviewed_at)              -- decision: approved | edited | rejected
memory_buckets   (id, name, type, owner_team_id, read JSONB, write JSONB)
memory_items     (id, bucket_id, content, embedding VECTOR, source_run_id, status, created_at)  -- status for procedural review
```

The YAML in `spec` is the source of truth. Searchable columns are generated from it, never edited separately.

---

## 14. Runtime API (v0)

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/v0/agents/{name}/runs` | Run an agent: `{ input, user }`. Returns a run ID or the result |
| `GET` | `/v0/runs/{id}` · `/v0/runs/{id}/trace` | Status, output, cost · redacted trace spans |
| `GET` | `/v0/agents?q=` | Search the caller's team collection |
| `GET` | `/v0/agents/{name}/resolved` | Resolved view with each value's source layer (team agents only) |
| `POST` | `/v0/agents/{name}/validate` · `/evals` · `/fork` | Validate · run evals · fork into the caller's team |
| `POST` | `/v0/agents/{name}/publish` | Start a publish pipeline run (§11). Returns its ID |
| `GET` | `/v0/publish-runs/{id}` | Stage status, logs, generated cases, review state, eval report |
| `POST` | `/v0/publish-runs/{id}/cases/{case}/review` | `{ decision: approve \| edit \| reject, edited_case?, reason? }` |
| `POST` | `/v0/publish-runs/{id}/cases/{case}/dry-run` | Run one case once and return the (redacted) output |
| `POST` | `/v0/publish-runs/{id}/continue` | Continue to evals once every case is reviewed. Refused otherwise |
| `GET` | `/v0/meta-agents` · `POST /v0/meta-agents/{name}/runs` | List and run meta-agents (no configs returned) |
| `GET` · `POST` | `/v0/approvals` · `/v0/approvals/{id}` | Human-in-the-loop queue |
| `POST` | `/v0/connector-requests` | A team requests a new connector |
| `POST` | `/v0/admin/registry` · `/v0/admin/teams/{id}` · `/v0/admin/enterprise` | Enterprise admins only: add connectors or models; edit team layers (members, approvers, grants, guardrails, limits) and the enterprise layer |
| `POST` | `/v0/admin/calibration` | Enterprise admins: add human labels; recompute judge agreement |
| `GET` | `/v0/metrics?team=&agent=&from=&to=` | Analytics, filtered to what the caller can see |
| — | `/mcp` | Every agent with an `mcp` trigger, exposed as an MCP tool |

The CLI mirrors the API: `reagent validate | run | eval | publish | fork <name>`. `reagent publish` starts the pipeline and prints the review link, because review happens in the dashboard.

---

## 15. Observability (every run)

Every run records: run and parent IDs, agent@version, team, trigger, user, model, tokens, `cost_usd` (from OpenRouter `usage.cost`), latency, steps, each tool call (action, resource, allowed or denied, duration), approvals, retries, errors, redaction hits and policy violations. All payloads pass through redaction before storage.

The dashboard groups these by enterprise, team and agent: runs over time, success rate, p50/p95 latency, cost against budget, top tools, denials and violations, human intervention rate, and eval pass rate by version.

## 16. Not in v0

- **Data classification and model routing.** Confidentiality isn't a concern for the hackathon. A later version can tag data and restrict which models may see it.
- **Signed files, SSO, row-level security, sandboxed execution, self-hosted inference.**
