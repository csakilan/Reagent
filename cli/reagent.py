"""The `reagent` CLI. A thin client over contracts/openapi.yaml: no business logic.

    python -m cli.reagent validate spec/examples/agents/platform-eng/pr-reviewer.agent
    python -m cli.reagent run pr-reviewer --input '{"repo":"nmc2/infra","pr_number":42}'
    python -m cli.reagent eval pr-reviewer --runs 5
    python -m cli.reagent publish pr-reviewer          # starts a PIPELINE, prints the review link
    python -m cli.reagent fork code-reviewer --as my-reviewer

Rules (cli/AGENTS.md): exit non-zero on any rejection, print `rejections[].message`
verbatim, never print a secret. The principal is REAGENT_USER (sent as the
X-ReAgent-User header, FROZEN D2); the server is REAGENT_API (default
http://localhost:8000). `validate --local` needs no server, for CI.
"""
from __future__ import annotations

import json
import os
import pathlib
import sys
from typing import Any, Optional

import httpx
import typer

app = typer.Typer(add_completion=False, no_args_is_help=True,
                  help="ReAgent: validate, run, evaluate, publish and fork agents.")

API = os.environ.get("REAGENT_API", "http://localhost:8000")


def _client(user: Optional[str]) -> httpx.Client:
    who = user or os.environ.get("REAGENT_USER")
    if not who:
        typer.echo("error: set REAGENT_USER or pass --user (the X-ReAgent-User principal)", err=True)
        raise typer.Exit(2)
    return httpx.Client(base_url=API, headers={"X-ReAgent-User": who}, timeout=600)


def _send(method: str, path: str, user: Optional[str], **kw: Any) -> Any:
    try:
        with _client(user) as c:
            r = c.request(method, path, **kw)
    except httpx.HTTPError as e:
        typer.echo(f"error: cannot reach {API}: {type(e).__name__}", err=True)
        raise typer.Exit(2)
    try:
        body = r.json()
    except ValueError:
        body = {"detail": r.text[:300]}
    if r.status_code >= 400:
        _print_rejections(body) or typer.echo(f"error {r.status_code}: {body.get('detail', body)}", err=True)
        raise typer.Exit(1)
    return body


def _print_rejections(body: Any) -> bool:
    rej = body.get("rejections") if isinstance(body, dict) else None
    if not rej:
        return False
    for r in rej:
        where = "" if r["field"] in r["message"] else f"[{r['field']}] "
        typer.echo(f"  {r['code']}  {where}{r['message']}", err=True)
    return True


@app.command()
def validate(path: pathlib.Path = typer.Argument(..., help="a .agent file, or a spec tree with --local"),
             local: bool = typer.Option(False, help="validate in-process, no server (for CI)"),
             user: Optional[str] = typer.Option(None, help="acting principal")):
    """Schema plus every tighten-only rule plus eval coverage. Exit 1 on any rejection."""
    if local:
        from runtime.resolver.validate import main
        raise typer.Exit(main([str(path)]))
    import yaml
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        typer.echo(f"error: cannot read {path}: {e.strerror}", err=True)
        raise typer.Exit(2)
    name = ((yaml.safe_load(text) or {}).get("metadata") or {}).get("name", path.stem)
    body = _send("POST", f"/v0/agents/{name}/validate", user, json={"spec": text})
    if body.get("valid"):
        typer.echo(f"OK   {path}")
        return
    typer.echo(f"FAIL {path}", err=True)
    _print_rejections(body)
    raise typer.Exit(1)


@app.command()
def run(name: str, input: str = typer.Option("{}", "--input", help="JSON run input"),
        wait: bool = typer.Option(True, help="wait for the run to finish"),
        user: Optional[str] = typer.Option(None, help="acting principal")):
    """Run an agent. The input is a separate user message (FROZEN D21)."""
    try:
        payload = json.loads(input)
    except ValueError as e:
        typer.echo(f"error: --input is not JSON: {e}", err=True)
        raise typer.Exit(2)
    body = _send("POST", f"/v0/agents/{name}/runs", user, json={"input": payload, "wait": wait})
    typer.echo(json.dumps(body, indent=2))
    if body.get("status") in ("failed", "denied"):
        raise typer.Exit(1)


@app.command("eval")
def eval_(name: str, runs: int = typer.Option(5, "--runs", min=1, help="runs per case"),
          user: Optional[str] = typer.Option(None, help="acting principal"),
          as_json: bool = typer.Option(False, "--json", help="print the full report")):
    """Run the eval suite in the mocked sandbox and print the report."""
    rep = _send("POST", f"/v0/agents/{name}/evals", user, json={"runs_per_case": runs})
    if as_json:
        typer.echo(json.dumps(rep, indent=2))
    else:
        typer.echo(f"{rep['agent']}@{rep['version']}  pass rate {rep['pass_rate']:.2f} "
                   f"[{rep['pass_rate_ci_low']:.2f}, {rep['pass_rate_ci_high']:.2f}] 95% Wilson  "
                   f"pass^k {rep['pass_k']:.2f}")
        for k in ("recall", "false_alarm_rate", "safety_pass_rate", "tool_call_accuracy"):
            v = rep.get(k)
            typer.echo(f"  {k:<20} {'-' if v is None else f'{v:.2f}'}")
        typer.echo(f"  {'avg_cost':<20} ${rep['avg_cost']}   p95 {rep['p95_latency']}ms   "
                   f"judge {'calibrated' if rep['judge_calibrated'] else 'uncalibrated (not gating)'}")
        for c in rep["cases"]:
            mark = "pass" if c["passed"] else ("----" if not c["gating"] else "FAIL")
            typer.echo(f"  {mark}  {c['passes']}/{c['runs']}  {c['case_id']}"
                       + (f"   {c['error'][:100]}" if c.get("error") else ""))
    if rep.get("gate_blocked"):
        typer.echo("gate: BLOCKED  " + "; ".join(rep.get("gate_reasons") or []), err=True)
        raise typer.Exit(1)
    typer.echo("gate: open")


@app.command()
def publish(name: str, user: Optional[str] = typer.Option(None, help="acting principal")):
    """Start the publish PIPELINE and print the review link. Review happens in the dashboard."""
    body = _send("POST", f"/v0/agents/{name}/publish", user)
    run_ = body.get("run") or {}
    typer.echo(f"pipeline {body['publish_run_id']} started for {name}: stage {run_.get('stage')}, "
               f"{run_.get('status')}")
    typer.echo(f"review every new or changed case here: {body['review_url']}")


@app.command()
def fork(name: str, new_name: str = typer.Option(..., "--as", help="name of the fork"),
         user: Optional[str] = typer.Option(None, help="acting principal")):
    """Fork into your team. Records forked_from: <name>@<exact version>."""
    body = _send("POST", f"/v0/agents/{name}/fork", user, json={"new_name": new_name})
    typer.echo(json.dumps(body, indent=2))


def main() -> None:
    app()


if __name__ == "__main__":
    main()
