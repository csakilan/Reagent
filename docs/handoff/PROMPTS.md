# The four prompts

Setup first (`SETUP.md`), then paste the matching block into the agent.

**These work on either tool.** Claude Code loads `CLAUDE.md` automatically;
Codex loads `AGENTS.md`, which is generated from it and CI-checked. Every prompt
below also tells the agent to open the `AGENTS.md` in the directory it owns,
because Codex only walks from the repo root down to the *working directory*, so
a session started at the root does not pick those up on its own.

Any of the four can switch tools: `claude` or `codex`, same prompt.

## Dependency chain

```
me: contracts (H0, done)
  -> YOU: database H4 03:30, secrets H7 03:45
       -> RESOLVER: H1 05:00  (blocks runtime + dashboard)
            -> RUNTIME: H2 06:30  (blocks resolver's evals + dashboard traces)
                 -> RESOLVER: H5 07:30, RUNTIME: H6 08:00  (block dashboard screens)
       -> YOU: live GitHub H3 07:00  (blocks runtime's live connectors)
  -> DASHBOARD: blocks nobody, never waits, builds on fixtures
08:00 integ/all, 09:00 rehearsal, 10:30 freeze, 11:30 submit
```

---

## 1. YOU — data · `claude` or `codex` on wf/data

> You are the WF4 data agent on branch `wf/data`. Read `docs/agents/WF4-data.md`,
> `connectors/AGENTS.md` and `runtime/AGENTS.md` first, and follow them exactly. Read `docs/research/tigerdata.md` before touching the
> database and `docs/research/mcp.md` before any MCP wrapper, because the MCP SDK
> renamed its main class and your training data is wrong about it.
>
> ORDER. You are first and everything is behind you. Task 1 only: apply
> `contracts/db/001_init.sql` to the DATABASE_URL I give you, then write the seed
> script. All three other workflows are blocked on H4, due 03:30. Release it the
> moment `pytest tests/contract/test_db_schema.py -q` passes, then stop and tell
> me. Do not start the MCP wrappers until H4 is out.
>
> `001_init.sql` is already written and tested against real Postgres, so apply it
> rather than rewriting it. If it errors, tell me instead of patching it. I am
> doing the Tiger signup and the GitHub App myself, so skip anything needing a
> browser and ask me for values. Run `python scripts/handoff.py status` first.

---

## 2. RESOLVER — `claude` or `codex` on wf/resolver

> You are the WF2 resolver agent on branch `wf/resolver`. Read
> `docs/agents/WF2-resolver.md`, `runtime/AGENTS.md` and `cli/AGENTS.md` first and
> follow them exactly, then read all 25 entries in `docs/decisions/FROZEN.md`: most govern your code and they override SPEC.md
> where they conflict.
>
> ORDER. You are the critical path. Two agents cannot start their real work until
> you release H1, due 05:00. So: task 1 the validator, then task 2 the resolver,
> then STOP and release H1 before you touch the eval engine. The eval engine is
> task 3 and it needs the runtime, which does not exist yet.
>
> Write one unit test per row of the merge table in SPEC.md section 1; the rules
> are a table so there is no excuse for skipping any. Be careful with
> `spec/tools/check_examples.py`: it prints ALL PASS on a config with
> enterprise-locked redaction switched off, so treat it as a sketch, not an
> oracle. You need nothing from anyone to start. Run
> `python scripts/handoff.py status` first.

---

## 3. RUNTIME — `claude` or `codex` on wf/runtime

> You are the WF1 runtime agent on branch `wf/runtime`. Read
> `docs/agents/WF1-runtime.md` and `runtime/AGENTS.md` first and follow them
> exactly. Before writing ANY
> middleware read `docs/research/langchain.md`: two assumptions were refuted
> against the real LangChain source. There is no `before_tool` hook, and
> `langchain-mcp-adapters` cannot filter a server to a subset of its tools, so
> permission enforcement must live in a `wrap_tool_call` middleware. Read
> `docs/research/openrouter.md` before the budget limit, because cost arrives
> only in the final streaming chunk.
>
> ORDER. You are waiting on H1 (resolver, 05:00) and you block two agents with
> H2, due 06:30. Run `python scripts/handoff.py wait H1` now: while it is
> pending, build tasks 1 and 2 against `fakes/resolver.py` and
> `fakes/openrouter.py`. Never sit idle and never poll in a loop. Release H2 as
> soon as `pytest tests/contract/test_run_lifecycle.py -q` passes, before moving
> on, because the eval engine and the trace viewer are both behind it. H6 is
> after that, due 08:00.
>
> Pin `langchain-openai>=1.1.11`; below that, cost is silently dropped and every
> budget reads zero. Start with task 1.

---

## 4. DASHBOARD — `codex` (or `claude`) on wf/dashboard

> You are the WF3 dashboard agent on branch `wf/dashboard`. Read
> `docs/agents/WF3-dashboard.md` and `dashboard/AGENTS.md` first and follow them
> exactly. Treat `dashboard/AGENTS.md` as binding rather than advisory: colour encodes
> provenance and verdict and nothing else, and the banned list is absolute.
> Generate your typed API client from `contracts/openapi.yaml` rather than
> hand-writing fetch calls.
>
> ORDER. You block nobody and you never wait. Every screen has data in
> `fixtures/`, so build against those and swap to live calls as
> `python scripts/handoff.py status` shows handoffs turning ready. Screens in
> this order, and do not reorder them: 1 resolved view, 2 trace viewer, 3 publish
> pipeline and case review, 4 collection plus editor plus approvals plus
> login-as. Those four are the demo. Admin, analytics and privacy come after.
>
> Start with the resolved view: it is the signature screen and demo step 3. It is
> `git blame` for policy, and `fixtures/resolved/pr-reviewer.admin.json` and
> `.member.json` are your test data. When `rejections[]` is non-empty, render
> `message` verbatim, because that text is contract and it is demo step 4.
