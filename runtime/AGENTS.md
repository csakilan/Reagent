# runtime/ — shared by two workflows

**Two different agents work in this directory.** Check which you are before
touching anything, because the boundary is strict and you will collide
otherwise.

| If you own | You are | Your brief |
|---|---|---|
| `engine/`, `guardrails/`, `subagents/`, `api/runs.py` | WF1 runtime | `../docs/agents/WF1-runtime.md` |
| `resolver/`, `evals/`, `publish/`, `api/agents.py` | WF2 resolver | `../docs/agents/WF2-resolver.md` |
| `db/` | WF4 data | `../docs/agents/WF4-data.md` |

Read your brief. Do not edit the other workflow's subdirectories; if you need a
change there, say so in your report.

## Python conventions

- Pin exactly, no ranges: `langchain==1.4.7`, `langgraph==1.2.17`,
  `langchain-mcp-adapters==0.3.2`, `mcp>=2.3.0,<3`, and
  **`langchain-openai>=1.1.11`** which is load-bearing, since below it the cost
  field is silently dropped and every budget reads zero.
- Python 3.11+. Write `python`, never `python3`: three of the four laptops are
  Windows where `python3` does not exist.
- `pathlib` or `os.path.join`, never a hardcoded `/`.
- Type hints on anything crossing a module boundary. The contracts are typed, so
  there is no excuse for an untyped boundary.

## Database facts that are not obvious

`runs` and `run_events` are **hypertables with composite primary keys**
(`(id, started_at)` and `(id, ts)`), because every unique index on a hypertable
must contain the partitioning column. **Nothing can foreign-key to `runs(id)`.**
`run_events.run_id`, `approvals.run_id` and `eval_runs` deliberately carry no FK.
Do not try to add one; Postgres will reject it.

`agent_versions.embedding` is a plain column the application writes. A generated
vector column compiles, which is the trap, but cannot be written.

Connection pools stay small everywhere: `pool_size=2, max_overflow=1,
pool_pre_ping=True`. Four laptops plus a checkpointer share one database.

## Secrets

Credentials are injected at the MCP wrapper and **never** reach model context,
a log, or a trace payload. `run_events.payload_redacted` is named that way
because redaction happens before the write, never on read. Nothing raw is
stored. A secret that reaches the database has already lost the demo, because
the trace viewer renders what is stored.

## Before you write middleware

Read `../docs/research/langchain.md`. Two assumptions in the spec were refuted
against the actual source: **there is no `before_tool` hook** (the pre-tool gate
is `after_model` with `can_jump_to=["end"]`), and **`langchain-mcp-adapters`
cannot filter a server to a subset of its tools**, so permission enforcement
lives in a `wrap_tool_call` middleware rather than in the loaded tool list.
