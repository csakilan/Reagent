"use client";

import Link from "next/link";
import { Fragment, useState } from "react";
import { call, decideApproval, listApprovals, type Approval, type RunResult } from "../../lib/api";
import { recordedApprovals } from "../../lib/fixtures";
import { useDemoUser, useLive } from "../../lib/live";
import { Failure, Icon, PageHead, Payload, Skeleton, SourceTag, Verdict, relative, when } from "../ui";

type Status = "pending" | "decided" | "all";
type Result = { id: number; ok: boolean; text: string; run?: RunResult };

function DecisionCell({ item }: { item: Approval }) {
  if (item.decision === "expired") return <Verdict kind="fail">expired · action denied</Verdict>;
  if (item.decision === "rejected") return <Verdict kind="fail">rejected{item.approver ? ` · ${item.approver}` : ""}</Verdict>;
  if (item.decision === "approved") return <Verdict kind="pass">approved{item.approver ? ` · ${item.approver}` : ""}</Verdict>;
  return <Verdict kind="pending">waiting</Verdict>;
}

export default function ApprovalsPage() {
  const user = useDemoUser();
  const [status, setStatus] = useState<Status>("pending");
  const [state, reload] = useLive<{ approvals?: Approval[] }>(user && `approvals:${user}:${status}`, () => listApprovals(status), {
    fallback: () => {
      const all = recordedApprovals().approvals;
      return { approvals: status === "all" ? all : all.filter(item => status === "pending" ? !item.decision : Boolean(item.decision)) };
    },
  });
  const [busy, setBusy] = useState<number | null>(null);
  const [results, setResults] = useState<Record<number, Result>>({});
  const [open, setOpen] = useState<number | null>(null);
  const items = state.phase === "ready" ? state.data.approvals ?? [] : [];

  async function decide(item: Approval, decision: "approved" | "rejected") {
    setBusy(item.id);
    const outcome = await call<RunResult>(decideApproval(item.id, decision));
    setBusy(null);
    const result: Result = outcome.ok
      ? { id: item.id, ok: true, text: `${decision === "approved" ? "Approved" : "Rejected"}. Run ${outcome.data.run_id} is now ${outcome.data.status.replace("_", " ")}.`, run: outcome.data }
      : { id: item.id, ok: false, text: outcome.status === 410 ? "This approval already expired, so the action was denied and the run failed." : `HTTP ${outcome.status} · ${outcome.detail}` };
    setResults(current => ({ ...current, [item.id]: result }));
    reload();
  }

  return <>
    <PageHead crumb="Runs / Approvals" title="Approvals" subtitle="Paused runs waiting for a person. An approval that times out denies the action and fails the run.">
      <SourceTag state={state} />
      <div className="segmented" role="tablist" aria-label="Filter approvals">
        {(["pending", "decided", "all"] as Status[]).map(value => <button key={value} type="button" role="tab" aria-selected={status === value} className={status === value ? "is-on" : undefined} onClick={() => setStatus(value)}>{value}</button>)}
      </div>
    </PageHead>
    <p className="hint">Showing the queue for <b>{user ?? "…"}</b>. The API returns only teams this identity approves for.</p>
    {Object.values(results).reverse().map(result => <div className={`notice ${result.ok ? "" : "notice-denied"}`} key={result.id}>
      <Verdict kind={result.ok ? "pass" : "fail"}>approval {result.id}</Verdict> {result.text} {result.run && <Link href={`/traces?run=${result.run.run_id}`}>Open trace</Link>}
    </div>)}
    {state.phase === "loading" && <Skeleton rows={4} columns={5} label="Loading approvals" />}
    <Failure state={state} />
    {state.phase === "ready" && <section className="table approval-table" aria-label="Approvals">
      <div className="table-head"><span>Request</span><span>Action</span><span>Requested</span><span>Expires</span><span>Decision</span></div>
      {items.map(item => {
        const expired = !item.decision && item.expires_at && new Date(item.expires_at).getTime() < Date.now();
        return <Fragment key={item.id}>
          <div className="table-row">
            <div><code>approval {item.id}</code><p><Link href={`/traces?run=${item.run_id}`}>run {item.run_id}</Link> · {item.agent ?? "agent"} · {item.team ?? "team"}</p></div>
            <div><code>{item.action}</code><p>{item.risk ? `${item.risk} risk` : "risk not reported"}</p>
              <button type="button" className="text-button" aria-expanded={open === item.id} onClick={() => setOpen(open === item.id ? null : item.id)}><Icon name={open === item.id ? "collapse" : "expand"} />Arguments</button></div>
            <span>{when(item.requested_at)}</span>
            <span>{item.decision ? when(item.decided_at) : <>{when(item.expires_at)}<small className={expired ? "denied-text" : undefined}>{relative(item.expires_at)}</small></>}</span>
            <div className="decision-cell">
              <DecisionCell item={item} />
              {!item.decision && <div className="toolbar">
                <button className="button primary" type="button" disabled={busy === item.id} onClick={() => decide(item, "approved")}>Approve</button>
                <button className="button button-denied" type="button" disabled={busy === item.id} onClick={() => decide(item, "rejected")}>Reject</button>
              </div>}
            </div>
          </div>
          {open === item.id && <div className="row-extra"><Payload value={item.args_redacted} /></div>}
        </Fragment>;
      })}
      {!items.length && <div className="empty">{status === "pending" ? "Nothing is waiting on you." : "No approvals to show."}</div>}
    </section>}
  </>;
}
