# dashboard/ — binding rules

Codex loads this **in addition** to the root `AGENTS.md`, and because it sits
closer to the code it appears later in your context and carries more weight.
Read the root file too: it has the frozen decisions, the verified research and
the handoff protocol.

Your full brief is `../docs/agents/WF3-dashboard.md`.

## The one idea behind every visual decision

**Colour encodes provenance and verdict, and nothing else.** If something is
coloured, it is telling the reader which policy layer a value came from, or
whether an action was permitted. There is no decorative colour anywhere in this
app.

That single rule is what stops this looking generated. It also means most of the
banned list below is impossible by construction: there is nothing left to put a
gradient on.

## Banned. Do not introduce any of these.

Gradients. Drop shadows. Glassmorphism. Radial orbs. Dot grids. Purple and
black. Neon. Pastels. Pure white backgrounds. A coloured stripe down the left
edge of a card. Three-feature-card rows. Bento grids. Fake terminal windows.
Checkmark bullet lists. Inter, Geist or Space Grotesk. Lucide or any icon
library. Emoji. Hover animation on everything. Animated arrows. Invented
testimonials or fabricated content of any kind.

Tailwind's default palette is **deleted** from the config, so `bg-purple-500`
will not resolve. That is deliberate. Do not add it back.

## Required. Do not skip these.

- **Skeleton loaders** on every screen that fetches. This reads a live Postgres
  over conference Wi-Fi, so empty flashes will happen on camera.
- **A `/privacy` route** stating what the platform logs, what redaction strips,
  and the 30-day trace retention. For a self-hosted governance tool that is
  product surface, not boilerplate.
- A real `LICENSE` and `SECURITY.md`.

## Tokens

Defined as CSS variables in `app/globals.css`, in light and dark. Use the
variables, never a literal hex.

| Token | Light | Dark | Only for |
|---|---|---|---|
| background | `#faf9f7` | `#171614` | the page |
| ink | `#1a1a18` | `#eae8e4` | text |
| layer-enterprise | `#2f4858` | `#8fb0c4` | provenance |
| layer-team | `#2d6a62` | `#7fbdb2` | provenance |
| layer-agent | `#9a6b1f` | `#d9ad5e` | provenance |
| denied | `#8c3a2b` | `#d98775` | violations, rejections, denials |

The three layer colours are load-bearing rather than decorative, so they are
chosen to stay distinguishable under deuteranopia.

## Type and motion

IBM Plex Sans for interface. IBM Plex Mono for YAML, action names, run ids and
trace spans. Plex was designed for IBM's own technical products, which suits
configuration and traces.

Five hand-drawn inline SVGs for expand, collapse, pass, fail and pending. No
icon library.

Transitions only where state genuinely changes, such as a pipeline stage moving
from running to passed. 120ms. Nothing else animates.

## Layout

**Tables over cards.** This data is dense and tabular and deserves to be treated
that way. A persistent left nav with a dense content area, not a dashboard of
tiles.

## The four screens that are the demo

Build these before anything else, in this order.

1. **Resolved view.** `git blame` for policy: the merged config in monospace on
   the right, a narrow gutter on the left carrying the source layer per line.
   Clicking a gutter mark shows the chain, which `narrowed_from` in the contract
   gives you. When `rejections[]` is non-empty, render `message` **verbatim** —
   that text is contract, and it is demo step 4.
2. **Trace viewer.** A span waterfall with indentation for the nested sub-agent
   call and duration bars, closer to a devtools network panel than a log list.
   Mark a denied call (`allowed: false`) distinctly: a denial is the product
   working.
3. **Publish pipeline and case review.** A horizontal seven-stage strip where the
   human gate is drawn as a genuine stop, because the gate is the product claim.
   Then per-case approve, edit or reject with a Dry run button.
4. **Collection, editor, approvals, login-as.** Search and fork, Monaco with
   inline validation, the approvals queue with `expired` as a real state, and a
   login-as switcher in the chrome.

## Two things that will cost you real time

**Generate the API client from `../contracts/openapi.yaml`.** Do not hand-write
fetch calls. They drift the moment a route changes, and that drift surfaces at
09:00 when there is no time left.

**Next.js dev mode leaks database connections on hot reload.** Use a single
module-scope client, never one per request. It is the fastest way to exhaust the
pool and take the whole team's database down.

## You are never blocked

Every screen has data to build against in `../fixtures/`. Check
`python ../scripts/handoff.py status` and swap a fixture for a live call as each
handoff turns ready. Never sit idle waiting.

## Stay inside this directory

You own `dashboard/`. If an API response looks wrong, say so in your report
rather than reaching into `runtime/` to fix it, and keep building against the
fixture meanwhile. `contracts/` is frozen; CI will refuse a change to it from a
workflow branch.
