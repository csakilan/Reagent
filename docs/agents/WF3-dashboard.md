# WF3 — Dashboard

**Branch:** `wf/dashboard` · **Owner:** C · **MVP row:** 8 (plus row 9's UI)

You build the largest surface, and you are the only workflow a judge looks at
directly. You are also the workflow that can start immediately and never block,
because every screen has a fixture to build against.

## Read, in this order

1. `CLAUDE.md` (already loaded) — **§10 is the binding design spec**
2. `contracts/openapi.yaml` — generate your client, do not hand-write fetches
3. `contracts/resolved_spec.schema.json` — the resolved view is your signature screen
4. `docs/decisions/FROZEN.md` — **D3, D15, D16, D17, D22, D23**
5. `spec/SPEC.md` §2 (what a member may see), §11 (the pipeline), §14

## You own

```
dashboard/            everything
```

**Do not touch** anything else. If an API response is wrong, report it; do not
reach into `runtime/`.

## Design is already decided. It is not open for reinterpretation.

This has to read as an internal enterprise tool, not a generated template.
`CLAUDE.md` §10 has the full list. The short version:

**Colour encodes provenance and verdict, and nothing else.** If something is
coloured it is telling you which layer a value came from, or whether an action
was permitted. There is no decorative colour. Tokens are CSS variables in
`dashboard/app/globals.css`, and Tailwind's default palette is **deleted** from
the config, so `bg-purple-500` will not resolve.

**Banned:** gradients, drop shadows, glassmorphism, orbs, dot grids, purple,
neon, pastels, pure white backgrounds, coloured left-edge card stripes,
three-card rows, bento grids, fake terminals, checkmark lists, Inter, Geist,
Space Grotesk, Lucide, emoji, hover animation on everything, animated arrows,
invented testimonials.

**Required:** skeleton loaders on every fetching screen, a `/privacy` page
stating what the platform logs and the 30-day retention, LICENSE and SECURITY.md.

Type is IBM Plex Sans and IBM Plex Mono. Icons are five hand-drawn inline SVGs.
Motion only on genuine state change, 120ms. **Tables over cards**: this data is
dense and tabular, so respect it.

## You are never blocked

| You need | Fake until the handoff lands |
|---|---|
| resolved specs (H1) | `fixtures/resolved/*.json` |
| run traces (H2) | `fixtures/traces/*.json` |
| eval reports (H5) | `fixtures/eval_report.json` |
| approvals (H6) | `fixtures/approvals.json` |

Build every screen against the fixture first. The fixture and the real response
satisfy the same contract test, so your code works unchanged when the handoff
lands. **Do not wait on anything.** Run
`python scripts/handoff.py status` and swap fixtures for live calls as they turn
ready.

## Screens, in priority order

Build 1 to 4 before anything else. Those four are the demo.

### 1. Resolved view · due 05:00 — your signature screen

`git blame` for policy. The merged document in monospace, a narrow gutter on the
left carrying the source layer per line, coloured with the three provenance
tokens. Clicking a gutter mark shows the chain: what enterprise set, what the
team narrowed, what the agent asked for (`narrowed_from` in the contract gives
you this).

**Members must not see enterprise values.** The API already redacts them: a leaf
arrives with `value: null` and `redacted: true`, keeping its source tag. Render
that as a locked row showing the layer but not the value. Do not work around it.

Demo step 3 is this screen. Demo step 4 is a rejection on it: when
`rejections[]` is non-empty, show `message` **verbatim**, with the field and the
blocking layer. That text is contract, not decoration.

### 2. Trace viewer · due 06:00

A span waterfall, not a log list. Indentation for the nested sub-agent call,
duration bars, and a clear mark where a tool call was **denied**
(`allowed: false`) and where redaction fired. Closer to a devtools network panel
than to a console.

Demo step 6 lives here: the planted key must be visibly redacted everywhere.

### 3. Publish pipeline and case review · due 07:30

A horizontal seven-stage strip where the human gate is drawn as a genuine stop,
because the gate is the product claim. Then the review screen: each case shows
its input, mocked sandbox, code checks, judge rubric, answer key, and a **Dry
run** button. Approve, edit, or reject per case, with bulk approve for
hand-written ones.

Case identity is `<scope>/<name>`, not a bare name, because inherited cases land
in the same run. **Never compute `case_hash` client-side** (D15); the server owns
it. Editing an approved case voids its approval, so reflect that in the UI.

Pipeline `status` is `running | failed | abandoned | succeeded` and `stage` names
the current step; you need both to distinguish running from failed (D17).

### 4. Collection, editor, approvals, login-as · due 08:00

Collection with search and a **Fork** button. Editor is Monaco with inline
validation errors from `POST /validate`. Approvals queue showing pending items,
with `expired` as a real state (an expired approval **denied** the action and
failed the run, D16). A login-as switcher for the three demo users in the app
chrome, not a route.

### 5. Admin, meta-agents, analytics, privacy · due 09:00

Enterprise and team layer screens, registry forms, connector requests, a
meta-agents panel every user can run with configs hidden from non-admins, the
analytics page, and `/privacy`.

## Watch out for

**Next.js dev mode leaks database connections on hot reload.** Use a single
module-scope client, never one per request. We are on a standard Tiger service
with a pooler so this is survivable, but it is still the fastest way to exhaust
a connection pool.

Generate the typed client from `contracts/openapi.yaml`. Hand-written fetch
calls drift from the API the moment a route changes, and that drift surfaces at
09:00 when there is no time to fix it.

## Checkpoints

| Time | Expected |
|---|---|
| 04:00 | app runs, tokens in place, four routes with skeletons |
| 05:00 | resolved view against fixtures |
| 06:00 | trace viewer |
| 07:30 | pipeline and case review |
| 08:00 | collection, editor, approvals, login-as |
| 09:00 | admin, analytics, privacy |

## Verify

```
cd dashboard && npm run build     # must pass; a type error at 10:00 is fatal
npm run lint
python scripts/handoff.py status  # swap fixtures for live as these turn ready
```
