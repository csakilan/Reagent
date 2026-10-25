# cli/ — the reagent CLI · WF2

Your brief is `../docs/agents/WF2-resolver.md`. This is the last of your tasks;
the resolver and the eval engine come first.

Typer, wrapping the HTTP API in `../contracts/openapi.yaml`. The CLI holds no
business logic of its own: it is a thin client, so the same rules apply whether
a person runs `reagent validate` or the dashboard calls `POST /validate`.

```
reagent validate <file>     schema plus semantic checks
reagent run <name>          POST /v0/agents/{name}/runs
reagent eval <name>         POST /v0/agents/{name}/evals
reagent publish <name>      starts the pipeline, prints the review link
reagent fork <name>         forks into the caller's team
```

`publish` starts a **pipeline**, not a publish, and prints the review URL,
because the human review gate happens in the dashboard. Do not try to drive
case review from the terminal.

## Rules

- Exit non-zero on a rejection. This runs in CI.
- Print `rejections[].message` **verbatim**. That text is contract: SPEC.md §1
  requires an error naming the field and the blocking layer, and demo step 4
  puts it on screen.
- Write `python`, never `python3`. Three of the four laptops are Windows.
- No secrets in output, ever, including in an error path.
