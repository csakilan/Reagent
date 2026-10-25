"""The three-layer tighten-only merge. SPEC.md §1, overridden by FROZEN.md.

    enterprise ──► team:<name> ──► agent ──► overrides (optional, run-time)

Every rule in the §1 table, and every frozen decision that touches it, has one
method here. Each refusal is a `contracts.errors.Rejection`, whose message text
is contract. Nothing is ever silently clamped: a lower layer that asks for more
than the layer above allows is refused, naming the field and the blocking layer.

`overrides` is a partial agent spec applied BELOW the agent, the way a run-time
override, an eval sweep or a cost-optimizer variant would be. It may tighten the
agent, never loosen it. Model choice is the one exception: an override may pick
any model the team allows, since one model is a choice, not an allowlist.

The output has two views of one result, and they must agree:
  provenance  the merged tree, every leaf tagged by its source layer (dashboard)
  effective   the same result flattened, for enforcement (runtime)
"""
from __future__ import annotations

import datetime as _dt
import re
from dataclasses import dataclass, field
from typing import Any, Iterable

from contracts.errors import Code, Rejection

from .globs import intersect_scopes, is_subset, match, match_any
from .source import Catalog, semver_key

ENTERPRISE = "enterprise"
AGENT = "agent"
OVERRIDE = "override"           # internal only; tagged "agent" in the output
DEFAULT = "default"

PER_RUN = ("usd_per_run", "max_steps", "timeout_seconds", "max_call_depth")
AGGREGATE = ("usd_per_month", "max_concurrent_runs")              # FROZEN D7
DEFAULT_LIMITS = {"usd_per_run": 0.5, "max_steps": 25, "timeout_seconds": 300,
                  "max_call_depth": 3}

MEMORY_ACTIONS = {"read": ["read", "search"], "write": ["write"],     # FROZEN D12
                  "read_write": ["read", "search", "write"]}
TRIGGER_TYPES = ["http", "mcp", "schedule", "github", "slack", "agent"]
TRACE_STRICTNESS = {"redacted": 0, "metadata_only": 1}
SAFETY_DEFAULTS = {("redaction", "secrets"): True, ("redaction", "pii"): False,
                   ("audit_log",): True}
ON_BEHALF_OF_TRIGGERS = {"schedule", "github", "slack"}             # FROZEN D2

_EXACT = re.compile(r"^\d+\.\d+\.\d+$")


def tag(layer: str) -> str:
    return AGENT if layer == OVERRIDE else layer


def leaf(value: Any, source: str, narrowed_from: Any = None) -> dict[str, Any]:
    d = {"value": value, "source": tag(source)}
    if narrowed_from is not None:
        d["narrowed_from"] = narrowed_from
    return d


def fpath(layer: str, path: str) -> str:
    """A rejection's `field`: a dot path that also says which document it is in."""
    if layer == ENTERPRISE:
        return f"enterprise.spec.{path}"
    if layer.startswith("team:"):
        return f"teams/{layer[5:]}.spec.{path}"
    if layer == OVERRIDE:
        return f"overrides.spec.{path}"
    return f"spec.{path}"


def dig(d: Any, path: Iterable[str]) -> Any:
    for k in path:
        if not isinstance(d, dict) or k not in d:
            return None
        d = d[k]
    return d


def leaf_paths(d: Any, prefix: tuple[str, ...] = ()) -> Iterable[tuple[str, ...]]:
    """Every path a document sets. Dicts are walked; anything else is a leaf."""
    if isinstance(d, dict) and d:
        for k, v in d.items():
            yield from leaf_paths(v, prefix + (str(k),))
    elif prefix:
        yield prefix


def satisfies(version: str, rng: str) -> bool:
    """Semver range test for sub-agent refs: exact, partial, ^ and ~."""
    if not rng:
        return True
    v = semver_key(version)
    op = rng[0] if rng[0] in "^~" else ""
    want = [int(x) for x in rng.lstrip("^~").split(".")]
    if op == "":
        return list(v[:len(want)]) == want
    base = tuple(want + [0] * (3 - len(want)))
    if v < base:
        return False
    if op == "~":
        return list(v[:2]) == list(base[:2]) if len(want) >= 2 else v[0] == base[0]
    # ^: same leftmost non-zero component
    for i, b in enumerate(base):
        if b != 0 or i == len(want) - 1:
            return list(v[:i + 1]) == list(base[:i + 1])
    return v[:3] == base


@dataclass
class Scope:
    """What one layer permits, per connector, per concrete action."""
    layer: str
    # connector -> {"allow": [patterns], "resources": {k: [pats]},
    #               "actions": {action: {k: [pats]}}, "auth_modes": set | None}
    grants: dict[str, dict[str, Any]] = field(default_factory=dict)


@dataclass
class Accepted:
    connector: str
    actions: list[str]
    per_action: dict[str, dict[str, list[str]]]
    auth: str
    synthetic: bool
    source: str
    narrowed_from: Any = None


class Resolution:
    def __init__(self, cat: Catalog, doc: dict[str, Any], *, caller: str | None,
                 root_team: str | None, overrides: dict[str, Any] | None,
                 trigger: str | None) -> None:
        self.cat = cat
        self.doc = doc
        self.md = doc["metadata"]
        self.spec = doc.get("spec", {})
        self.ov = (overrides or {}).get("spec", {}) if overrides else {}
        self.level = self.md.get("level", "team")
        self.team = self.md.get("team") if self.level == "team" else None
        self.trigger = trigger
        self.rej: list[Rejection] = []
        self._seen: set[tuple] = set()

        # The principal. FROZEN D2: from the session, never a request body.
        # `None` / "system" is the runtime itself, which sees real values.
        self.system = caller in (None, "system")
        self.user = None if self.system else caller
        self.is_admin = self.system or caller in cat.admins()
        self.caller_team = root_team or cat.team_of(self.user)
        # FROZEN D1: an enterprise agent is scoped to the ROOT run's team.
        self.root_team = root_team or self.caller_team

        self.ent = cat.ent
        self.team_spec = cat.team(self.team) if self.team else None
        self.team_layer = f"team:{self.team}" if self.team else None

    # -- plumbing -------------------------------------------------------------
    def reject(self, code: Code, field_: str, layer: str, attempted: Any = None,
               allowed: Any = None) -> None:
        key = (code, field_, layer)
        if key in self._seen:
            return
        self._seen.add(key)
        self.rej.append(Rejection(code, field_, tag(layer), attempted, allowed))

    def layers(self) -> list[tuple[str, dict[str, Any]]]:
        """The policy layers above the agent, outermost first."""
        out = [(ENTERPRISE, self.ent)]
        if self.team_layer:
            out.append((self.team_layer, self.team_spec or {}))
        return out

    def lower(self) -> list[tuple[str, dict[str, Any]]]:
        out = [(AGENT, self.spec)]
        if self.ov:
            out.append((OVERRIDE, self.ov))
        return out

    # -- entry point ----------------------------------------------------------
    def run(self) -> dict[str, Any]:
        if self.level == "team" and self.team_spec is None:
            self.reject(Code.UNKNOWN_TEAM, "metadata.team", DEFAULT, attempted=self.team)
            self.team_layer = None

        self.collect_deny()
        self.check_locks()
        if self.team_layer:
            self.check_buckets_declared()
        ent_scope = self.enterprise_scope()
        scopes = [ent_scope]
        team_scope = None
        if self.team_layer:
            team_scope = self.team_scope(ent_scope, self.team, self.team_layer)
            scopes.append(team_scope)

        limits = self.merge_limits()
        guard = self.merge_guardrails()
        models = self.merge_models()
        triggers = self.merge_triggers()
        tools = self.merge_tools(scopes)
        sub_agents = self.merge_sub_agents()
        self.check_fork()
        self.check_notify(team_scope or ent_scope)
        self.check_on_behalf_of(tools)
        return self.build(limits, guard, models, triggers, tools, sub_agents)

    # -- D13: one denylist ----------------------------------------------------
    def collect_deny(self) -> None:
        self.deny: list[tuple[str, str]] = []
        for layer, spec in self.layers():
            for c, g in (spec.get("connectors") or {}).items():
                for d in g.get("deny", []):
                    self.deny.append((f"{c}:{d}", layer))
        for layer, spec in self.layers() + self.lower():
            for d in dig(spec, ("guardrails", "deny")) or []:
                self.deny.append((d, layer))

    def denied(self, connector: str, action: str) -> tuple[str, str] | None:
        perm = f"{connector}:{action}"
        return next(((p, l) for p, l in self.deny if match(perm, p)), None)

    # -- D5: prefix locks -----------------------------------------------------
    def check_locks(self) -> None:
        self.locks: list[tuple[str, str]] = []
        lower = ([(self.team_layer, self.team_spec or {})] if self.team_layer else []) + self.lower()
        for layer, spec in self.layers():
            for p in spec.get("locked", []):
                self.locks.append((p, layer))
        order = [ENTERPRISE] + ([self.team_layer] if self.team_layer else []) + [AGENT, OVERRIDE]
        for layer, spec in lower:
            for path in leaf_paths({k: v for k, v in spec.items() if k != "locked"}):
                # An agent's `budget` is the same policy surface as a layer's `limits`.
                aliases = {path}
                if layer in (AGENT, OVERRIDE) and path[0] == "budget":
                    aliases.add(("limits",) + path[1:])
                for lock, lock_layer in self.locks:
                    if order.index(lock_layer) >= order.index(layer):
                        continue
                    lk = tuple(lock.split("."))
                    if any(a[:len(lk)] == lk for a in aliases):
                        if (lock, layer) in self._seen:
                            continue
                        self._seen.add((lock, layer))
                        self.reject(Code.LOCKED_PATH, fpath(layer, ".".join(path)),
                                    lock_layer, attempted=".".join(path), allowed=lock)

    # -- D6: global, immutable bucket names -----------------------------------
    def buckets(self) -> dict[str, tuple[str, dict[str, Any]]]:
        out = {b["name"]: (ENTERPRISE, b) for b in dig(self.ent, ("memory", "buckets")) or []}
        for tname in sorted(self.cat.teams):
            for b in dig(self.cat.team(tname), ("memory", "buckets")) or []:
                out.setdefault(b["name"], (f"team:{tname}", b))
        return out

    def check_buckets_declared(self) -> None:
        ent_names = {b["name"] for b in dig(self.ent, ("memory", "buckets")) or []}
        others = {b["name"]: f"team:{t}" for t in sorted(self.cat.teams) if t != self.team
                  for b in dig(self.cat.team(t), ("memory", "buckets")) or []}
        for i, b in enumerate(dig(self.team_spec, ("memory", "buckets")) or []):
            base = f"memory.buckets[{i}]"
            if b["name"] in ent_names or b["name"] in others:
                owner = ENTERPRISE if b["name"] in ent_names else others[b["name"]]
                self.reject(Code.BUCKET_REDEFINED, fpath(self.team_layer, base + ".name"),
                            owner, attempted=b["name"])
                continue
            for mode in ("read", "write"):
                bad = [p for p in b.get(mode, []) if p not in (self.team, "admins")]
                if bad:
                    self.reject(Code.WIDEN_ALLOWLIST, fpath(self.team_layer, f"{base}.{mode}"),
                                ENTERPRISE, attempted=bad, allowed=[self.team, "admins"])

    # -- allowlists: connectors, actions, resources (D10, D18) ----------------
    def enterprise_scope(self) -> Scope:
        sc = Scope(ENTERPRISE)
        for c, g in (self.ent.get("connectors") or {}).items():
            reg = self.cat.actions(c)
            if not reg:
                self.reject(Code.UNKNOWN_CONNECTOR, fpath(ENTERPRISE, f"connectors.{c}"),
                            DEFAULT, attempted=c)
                continue
            allow = g.get("allow", ["*"])
            acts: set[str] = set()
            for i, p in enumerate(allow):
                hit = [a for a in reg if match(a, p)]
                if not hit:
                    self.reject(Code.UNKNOWN_ACTION, fpath(ENTERPRISE, f"connectors.{c}.allow[{i}]"),
                                DEFAULT, attempted=f"{c}:{p}")
                acts |= set(hit)
            res = dict(g.get("resources") or {})
            sc.grants[c] = {"allow": list(allow), "resources": res,
                            "actions": {a: self._filter_res(res, reg[a]) for a in acts},
                            "auth_modes": set(g["auth_modes"]) if "auth_modes" in g else None}
        return sc

    def team_scope(self, parent: Scope, team: str, layer: str, silent: bool = False) -> Scope:
        reject = self.reject if not silent else (lambda *a, **k: None)
        spec = self.cat.team(team) or {}
        sc = Scope(layer)
        for c, g in (spec.get("connectors") or {}).items():
            base = f"connectors.{c}"
            reg = self.cat.actions(c)
            if not reg:
                reject(Code.UNKNOWN_CONNECTOR, fpath(layer, base), DEFAULT, attempted=c)
                continue
            if c == "platform":
                reject(Code.PLATFORM_CONNECTOR_DENIED, fpath(layer, base), ENTERPRISE, attempted=c)
                continue
            pg = parent.grants.get(c)
            if pg is None:
                reject(Code.CONNECTOR_NOT_GRANTED, fpath(layer, base), parent.layer, attempted=c)
                continue
            res = dict(pg["resources"])                         # D18: omitted inherits
            for k, pats in (g.get("resources") or {}).items():
                if not any(k in a.get("resources", []) for a in reg.values()):
                    reject(Code.RESOURCE_OUT_OF_SCOPE, fpath(layer, f"{base}.resources.{k}"),
                           ENTERPRISE, attempted=k,
                           allowed=sorted({r for a in reg.values() for r in a.get("resources", [])}))
                    continue
                pp = pg["resources"].get(k)
                bad = [p for p in pats if pp is not None and not is_subset(p, pp)]
                if bad:
                    reject(Code.RESOURCE_OUT_OF_SCOPE, fpath(layer, f"{base}.resources.{k}"),
                           parent.layer, attempted=bad, allowed=pp)
                    continue
                res[k] = list(pats)
            if "allow" in g:                                    # D10: true intersection
                acts: set[str] = set()
                for i, p in enumerate(g["allow"]):
                    hit = [a for a in reg if match(a, p)]
                    if not hit:
                        reject(Code.UNKNOWN_ACTION, fpath(layer, f"{base}.allow[{i}]"), DEFAULT,
                               attempted=f"{c}:{p}")
                        continue
                    live = [a for a in hit if not self.denied(c, a)]
                    wide = [f"{c}:{a}" for a in live if a not in pg["actions"]]
                    if wide:
                        reject(Code.WIDEN_ALLOWLIST, fpath(layer, f"{base}.allow[{i}]"),
                               parent.layer, attempted=wide,
                               allowed=[f"{c}:{x}" for x in pg["allow"]])
                        continue
                    acts |= set(live)
                allow = list(g["allow"])
            else:
                acts, allow = set(pg["actions"]), list(pg["allow"])
            modes = pg["auth_modes"]
            if "auth_modes" in g:
                extra = set(g["auth_modes"]) - modes if modes is not None else set()
                if extra:
                    reject(Code.WIDEN_ALLOWLIST, fpath(layer, f"{base}.auth_modes"), parent.layer,
                           attempted=sorted(extra), allowed=sorted(modes))
                modes = set(g["auth_modes"]) & modes if modes is not None else set(g["auth_modes"])
            sc.grants[c] = {"allow": allow, "resources": res,
                            "actions": {a: self._filter_res(res, reg[a]) for a in acts},
                            "auth_modes": modes}
        return sc

    @staticmethod
    def _filter_res(res: dict[str, list[str]], action: dict[str, Any]) -> dict[str, list[str]]:
        declared = set(action.get("resources", []))
        return {k: list(v) for k, v in res.items() if k in declared}

    def check_entries(self, entries: list[dict[str, Any]], scopes: list[Scope],
                      layer: str) -> list[Accepted]:
        """Check tool entries against every scope above them, outermost first."""
        out: list[Accepted] = []
        for e in entries:
            c, path = e["connector"], e["_path"]
            reg = self.cat.actions(c)
            if not reg:
                self.reject(Code.UNKNOWN_CONNECTOR, fpath(layer, f"{path}.connector"), DEFAULT,
                            attempted=c)
                continue
            if c == "platform" and self.level == "team":
                self.reject(Code.PLATFORM_CONNECTOR_DENIED, fpath(layer, f"{path}.connector"),
                            ENTERPRISE, attempted=c)
                continue
            auth = e.get("auth", "service")
            modes = self.cat.auth_modes(c)
            if not e.get("synthetic") and modes is not None and auth not in modes:
                self.reject(Code.AUTH_MODE_UNSUPPORTED, fpath(layer, f"{path}.auth"), ENTERPRISE,
                            attempted=auth, allowed=sorted(modes))
                continue
            gap = next((s for s in scopes if c not in s.grants), None)
            if gap is not None:
                self.reject(Code.CONNECTOR_NOT_GRANTED, fpath(layer, f"{path}.connector"),
                            gap.layer, attempted=c)
                continue
            narrow = next((s for s in scopes if s.grants[c]["auth_modes"] is not None
                           and auth not in s.grants[c]["auth_modes"]), None)
            if narrow is not None and not e.get("synthetic"):
                self.reject(Code.AUTH_MODE_UNSUPPORTED, fpath(layer, f"{path}.auth"), narrow.layer,
                            attempted=auth, allowed=sorted(narrow.grants[c]["auth_modes"]))
                continue

            own_res = e.get("resources")
            acc = Accepted(c, [], {}, auth, bool(e.get("synthetic")), layer)
            for i, p in enumerate(e["actions"]):
                afield = fpath(layer, f"{path}.actions[{i}]")
                hit = [a for a in reg if match(a, p)]
                if not hit:
                    self.reject(Code.UNKNOWN_ACTION, afield, DEFAULT, attempted=f"{c}:{p}")
                    continue
                for a in hit:
                    perm = f"{c}:{a}"
                    d = self.denied(c, a)
                    if d is not None:                            # D13: deny beats allow
                        self.reject(Code.DENIED_ACTION, afield, d[1], attempted=perm, allowed=d[0])
                        continue
                    blocker = next((s for s in scopes if a not in s.grants[c]["actions"]), None)
                    if blocker is not None:
                        self.reject(Code.ACTION_NOT_GRANTED, afield, blocker.layer, attempted=perm,
                                    allowed=[f"{c}:{x}" for x in blocker.grants[c]["allow"]])
                        continue
                    res = self._resources_for(c, a, own_res, scopes, layer, path, reg)
                    if res is None:
                        continue
                    if a not in acc.actions:
                        acc.actions.append(a)
                    acc.per_action[a] = res
            if not acc.actions:
                continue
            parent = scopes[-1].grants[c]
            if not acc.synthetic and set(acc.actions) != set(parent["actions"]):
                acc.narrowed_from = {"actions": parent["allow"], "from_layer": tag(scopes[-1].layer)}
            out.append(acc)
        return out

    def _resources_for(self, c: str, a: str, own: dict[str, list[str]] | None,
                       scopes: list[Scope], layer: str, path: str,
                       reg: dict[str, Any]) -> dict[str, list[str]] | None:
        declared = set(reg[a].get("resources", []))
        res: dict[str, list[str]] = {}
        for k, pats in (own or {}).items():
            rfield = fpath(layer, f"{path}.resources.{k}")
            if k not in declared:                      # D18: undeclared resource type
                self.reject(Code.RESOURCE_OUT_OF_SCOPE, rfield, ENTERPRISE,
                            attempted=f"{k} on {c}:{a}", allowed=sorted(declared))
                return None
            for s in scopes:
                pp = s.grants[c]["actions"][a].get(k)
                if pp is None:
                    continue
                bad = [x for x in pats if not is_subset(x, pp)]
                if bad:
                    self.reject(Code.RESOURCE_OUT_OF_SCOPE, rfield, s.layer, attempted=bad, allowed=pp)
                    return None
            res[k] = list(pats)
        for k, pp in scopes[-1].grants[c]["actions"][a].items():   # D18: omitted inherits
            res.setdefault(k, list(pp))
        return res

    # -- D12: memory expands into tools ---------------------------------------
    def memory_entries(self, spec: dict[str, Any], layer: str) -> list[dict[str, Any]]:
        buckets = self.buckets()
        principals = {self.team} if self.team else {"admins", self.root_team}
        by_mode: dict[str, list[str]] = {}
        first: dict[str, int] = {}
        for i, m in enumerate(spec.get("memory") or []):
            base = fpath(layer, f"memory[{i}]")
            if m["bucket"] not in buckets:
                self.reject(Code.UNKNOWN_BUCKET, base + ".bucket", DEFAULT, attempted=m["bucket"])
                continue
            owner, b = buckets[m["bucket"]]
            wants_write = "write" in m["mode"]
            if not ("*" in b.get("read", []) or principals & set(b.get("read", []))):
                self.reject(Code.BUCKET_ACCESS_DENIED, base + ".bucket", owner, attempted=m["bucket"],
                            allowed=b.get("read", []))
                continue
            if wants_write and b["type"] == "episodic":
                self.reject(Code.EPISODIC_WRITE_DENIED, base + ".mode", DEFAULT, attempted=m["bucket"])
                continue
            if wants_write and not ("*" in b.get("write", []) or principals & set(b.get("write", []))):
                self.reject(Code.BUCKET_ACCESS_DENIED, base + ".mode", owner, attempted=m["bucket"],
                            allowed=b.get("write", []))
                continue
            by_mode.setdefault(m["mode"], []).append(m["bucket"])
            first.setdefault(m["mode"], i)
        return [{"connector": "memory", "actions": MEMORY_ACTIONS[mode],
                 "resources": {"bucket": names}, "auth": "service", "synthetic": True,
                 "_path": f"memory[{first[mode]}]"} for mode, names in by_mode.items()]

    def merge_tools(self, scopes: list[Scope]) -> list[Accepted]:
        explicit = [dict(t, _path=f"tools[{i}]") for i, t in enumerate(self.spec.get("tools") or [])]
        accepted = self.check_entries(explicit, scopes, AGENT)
        memory = self.check_entries(self.memory_entries(self.spec, AGENT), scopes, AGENT)

        if "tools" in self.ov:
            agent_scope = Scope(AGENT)
            for acc in accepted:
                g = agent_scope.grants.setdefault(acc.connector, {
                    "allow": [], "resources": {}, "actions": {}, "auth_modes": None})
                for a in acc.actions:
                    g["actions"][a] = acc.per_action[a]
                    g["allow"].append(a)
            ov = [dict(t, _path=f"tools[{i}]") for i, t in enumerate(self.ov["tools"] or [])]
            accepted = self.check_entries(ov, scopes + [agent_scope], OVERRIDE)

        tools = accepted + memory
        if self.level == "enterprise":
            tools = self.scope_to_root_team(tools)
        return tools

    def scope_to_root_team(self, tools: list[Accepted]) -> list[Accepted]:
        """FROZEN D1: own tools ∩ the ROOT run's team grants. `service` is not exempt.

        `platform` stays: it is enterprise-only and filters by the caller's team
        itself (SPEC.md §2). With no root team, an admin keeps the enterprise
        scope and anyone else keeps nothing but `platform`.
        """
        if self.root_team is None:
            return tools if self.is_admin else [t for t in tools if t.connector == "platform"]
        ent_scope = self.enterprise_scope_silent()
        team = self.team_scope(ent_scope, self.root_team, f"team:{self.root_team}", silent=True)
        out: list[Accepted] = []
        for t in tools:
            if t.connector == "platform":
                out.append(t)
                continue
            g = team.grants.get(t.connector)
            if g is None:
                continue
            kept = Accepted(t.connector, [], {}, t.auth, t.synthetic, t.source,
                            {"scoped_to": f"team:{self.root_team}"})
            for a in t.actions:
                if a not in g["actions"]:
                    continue
                res: dict[str, list[str]] = {}
                ok = True
                for k in set(t.per_action[a]) | set(g["actions"][a]):
                    own, scope = t.per_action[a].get(k), g["actions"][a].get(k)
                    both = (intersect_scopes(own, scope) if own is not None and scope is not None
                            else list(own if own is not None else scope))
                    if not both:
                        ok = False
                        break
                    res[k] = both
                if ok:
                    kept.actions.append(a)
                    kept.per_action[a] = res
            if kept.actions:
                out.append(kept)
        return out

    def enterprise_scope_silent(self) -> Scope:
        saved, seen = list(self.rej), set(self._seen)
        sc = self.enterprise_scope()
        self.rej, self._seen = saved, seen
        return sc

    # -- numeric limits: minimum (D7 splits per-run from aggregate) -----------
    def merge_limits(self) -> dict[str, tuple[Any, str, Any]]:
        chain = [(l, s.get("limits") or {}) for l, s in self.layers()]
        chain += [(AGENT, self.spec.get("budget") or {})]
        if self.ov.get("budget"):
            chain.append((OVERRIDE, self.ov["budget"]))
        cur: dict[str, tuple[Any, str, Any]] = {}
        for layer, vals in chain:
            key = "budget" if layer in (AGENT, OVERRIDE) else "limits"
            for k, v in vals.items():
                if k in cur and v > cur[k][0]:
                    self.reject(Code.LIMIT_ABOVE_PARENT, fpath(layer, f"{key}.{k}"), cur[k][1],
                                attempted=v, allowed=cur[k][0])
                    continue
                nf = ({"value": cur[k][0], "from_layer": tag(cur[k][1])}
                      if k in cur and v < cur[k][0] else None)
                cur[k] = (v, layer, nf)
        self.aggregate = {}
        for layer, spec in self.layers() + [(AGENT, {"limits": self.spec.get("budget") or {}})]:
            name = "team" if layer.startswith("team:") else layer
            vals = spec.get("limits") or {}
            self.aggregate[name] = {k: vals.get(k) for k in AGGREGATE}
        return cur

    # -- safety flags: stricter wins; approvals: union -------------------------
    def merge_guardrails(self) -> dict[str, Any]:
        chain = [(l, s.get("guardrails") or {}) for l, s in self.layers() + self.lower()]
        flags: dict[tuple[str, ...], tuple[bool, str]] = {}
        for path, default in SAFETY_DEFAULTS.items():
            cur: tuple[bool, str] | None = None
            for layer, g in chain:
                v = dig(g, path)
                if v is None:
                    continue
                if cur is not None and cur[0] is True and v is False:
                    self.reject(Code.SAFETY_FLAG_DISABLED, fpath(layer, "guardrails." + ".".join(path)),
                                cur[1], attempted=False, allowed=True)
                    continue
                if cur is None or (v and not cur[0]):
                    cur = (bool(v), layer)
            flags[path] = cur or (default, DEFAULT)

        trace: tuple[str, str] = ("redacted", DEFAULT)
        seen_trace = False
        for layer, spec in self.layers() + self.lower():
            v = dig(spec, ("observability", "trace_payloads"))
            if v is None:
                continue
            if seen_trace and TRACE_STRICTNESS[v] < TRACE_STRICTNESS[trace[0]]:
                self.reject(Code.SAFETY_FLAG_DISABLED, fpath(layer, "observability.trace_payloads"),
                            trace[1], attempted=v, allowed=trace[0])
                continue
            trace, seen_trace = (v, layer), True

        risks: list[str] = []
        risk_src: tuple[str, Any] | None = None
        actions: list[str] = []
        actions_src: str | None = None
        timeout: tuple[int, str, Any] | None = None
        for layer, g in chain:
            a = g.get("approval") or {}
            mine = list(a.get("require_for_risk", []))
            if [r for r in mine if r not in risks]:
                prev = {"value": list(risks), "from_layer": tag(risk_src[0])} if risks else None
                risks = mine + [r for r in risks if r not in mine]
                risk_src = (layer, prev)
            new = [x for x in a.get("actions", []) if x not in actions]
            if new:
                actions += new
                actions_src = layer
            if "timeout_minutes" in a:
                v = a["timeout_minutes"]
                if timeout is not None and v > timeout[0]:                     # D16: minimum
                    self.reject(Code.LIMIT_ABOVE_PARENT,
                                fpath(layer, "guardrails.approval.timeout_minutes"), timeout[1],
                                attempted=v, allowed=timeout[0])
                else:
                    nf = ({"value": timeout[0], "from_layer": tag(timeout[1])}
                          if timeout is not None and v < timeout[0] else None)
                    timeout = (v, layer, nf)
        self.approval_risks, self.approval_actions = set(risks), actions
        return {"flags": flags, "trace": trace, "risks": (risks, risk_src),
                "actions": (actions, actions_src), "timeout": timeout}

    # -- models and triggers: allowlists, intersection -------------------------
    def _allow_chain(self, key: str, universe: list[str], unknown: Code) -> list[tuple[set[str], str, list[str]]]:
        """[(allowed set, layer, patterns)] outermost first. Omitted inherits (D10)."""
        out: list[tuple[set[str], str, list[str]]] = []
        for layer, spec in self.layers():
            pats = dig(spec, (key, "allow"))
            if pats is None:
                if out:
                    out.append((out[-1][0], out[-1][1], out[-1][2]))
                else:
                    out.append((set(universe), DEFAULT, ["*"]))
                continue
            got: set[str] = set()
            for i, p in enumerate(pats):
                hit = {u for u in universe if match(u, p)}
                if not hit:
                    self.reject(unknown, fpath(layer, f"{key}.allow[{i}]"), DEFAULT, attempted=p)
                    continue
                if out and not hit <= out[-1][0]:
                    self.reject(Code.WIDEN_ALLOWLIST, fpath(layer, f"{key}.allow[{i}]"), out[-1][1],
                                attempted=sorted(hit - out[-1][0]), allowed=out[-1][2])
                    continue
                got |= hit
            out.append((got, layer, list(pats)))
        return out

    def merge_models(self) -> dict[str, Any]:
        chain = self._allow_chain("models", sorted(self.cat.models), Code.UNKNOWN_MODEL)
        model = dict(self.spec.get("model") or {})
        params = dict(model.get("params") or {})
        src = {"id": AGENT, "fallback": AGENT}
        ovm = self.ov.get("model") or {}
        for k in ("id", "fallback"):
            if k in ovm:
                model[k], src[k] = ovm[k], OVERRIDE
        params.update(ovm.get("params") or {})          # free parameters: override
        for k in ("id", "fallback"):
            mid = model.get(k)
            if mid is None:
                continue
            layer = src[k]
            mfield = fpath(layer, f"model.{k}")
            if mid not in self.cat.models:
                self.reject(Code.UNKNOWN_MODEL, mfield, DEFAULT, attempted=mid)
                continue
            for allowed, blayer, pats in chain:
                if mid not in allowed:
                    self.reject(Code.MODEL_NOT_ALLOWED, mfield, blayer, attempted=mid, allowed=pats)
                    break
        last = chain[-1]
        return {"id": model.get("id"), "fallback": model.get("fallback"), "params": params,
                "src": src, "narrowed_from": {"allow": last[2], "from_layer": tag(last[1])}}

    def merge_triggers(self) -> list[dict[str, Any]]:
        chain = self._allow_chain("triggers", TRIGGER_TYPES, Code.TRIGGER_NOT_ALLOWED)
        out = []
        for i, t in enumerate(self.spec.get("triggers") or []):
            blocker = next(((b, p) for s, b, p in chain if t["type"] not in s), None)
            if blocker is not None:
                self.reject(Code.TRIGGER_NOT_ALLOWED, fpath(AGENT, f"triggers[{i}].type"), blocker[0],
                            attempted=t["type"], allowed=blocker[1])
                continue
            out.append(t)
        return out

    # -- sub-agents (§8, D24) ---------------------------------------------------
    def merge_sub_agents(self) -> list[tuple[str, str, str]]:
        out = []
        for i, s in enumerate(self.spec.get("sub_agents") or []):
            name, _, rng = s["ref"].partition("@")
            sfield = fpath(AGENT, f"sub_agents[{i}].ref")
            versions = self.cat.agent_versions(name)
            pick = max((v for v in versions if satisfies(v, rng)), key=semver_key, default=None)
            if pick is None:
                self.reject(Code.SUBAGENT_NOT_CALLABLE, sfield, DEFAULT, attempted=s["ref"])
                continue
            callee = versions[pick]["metadata"]
            same_team = (callee.get("level") == "team" and self.team is not None
                         and callee.get("team") == self.team)
            service = callee.get("level") == "enterprise" and callee.get("access") == "service"
            if not (same_team or service):
                owner = f"team:{callee['team']}" if callee.get("level") == "team" else ENTERPRISE
                self.reject(Code.SUBAGENT_NOT_CALLABLE, sfield, owner, attempted=s["ref"])
                continue
            if self._cycles_back(name, {self.md["name"]}):
                self.reject(Code.SUBAGENT_CYCLE, sfield, AGENT, attempted=s["ref"])
                continue
            out.append((name, pick, s["ref"]))
        return out

    def _cycles_back(self, name: str, path: set[str]) -> bool:
        if name in path:
            return True
        doc = self.cat.latest(name)
        if doc is None:
            return False
        for s in doc.get("spec", {}).get("sub_agents") or []:
            if self._cycles_back(s["ref"].partition("@")[0], path | {name}):
                return True
        return False

    # -- forks, notifications, on_behalf_of -----------------------------------
    def check_fork(self) -> None:
        ref = self.md.get("forked_from")
        if not ref:
            return
        name, _, ver = ref.partition("@")
        if not _EXACT.match(ver):                                       # D24
            self.reject(Code.SCHEMA_INVALID, "metadata.forked_from", DEFAULT, attempted=ref,
                        allowed="<name>@<x.y.z>")
            return
        src = self.cat.agent_versions(name)
        src_doc = src.get(ver) or self.cat.latest(name)
        if src_doc is None or src_doc["metadata"].get("team") != self.team:
            self.reject(Code.FORK_OUTSIDE_TEAM, "metadata.forked_from",
                        self.team_layer or ENTERPRISE, attempted=ref)

    def check_notify(self, scope: Scope) -> None:
        """FROZEN D14: notify targets must sit inside the TEAM's slack channel scope."""
        targets = []
        for i, a in enumerate(dig(self.spec, ("observability", "alerts")) or []):
            targets += [(f"observability.alerts[{i}].notify[{j}]", t) for j, t in enumerate(a.get("notify", []))]
        for j, t in enumerate(dig(self.spec, ("failure", "on_failure", "notify")) or []):
            targets.append((f"failure.on_failure.notify[{j}]", t))
        for path, t in targets:
            if not t.startswith("slack:"):
                continue
            chan = t[len("slack:"):]
            g = scope.grants.get("slack")
            pats = None if g is None else g["resources"].get("channel")
            if g is None or (pats is not None and not is_subset(chan, pats)):
                self.reject(Code.RESOURCE_OUT_OF_SCOPE, fpath(AGENT, path), scope.layer,
                            attempted=t, allowed=pats)

    def check_on_behalf_of(self, tools: list[Accepted]) -> None:
        if self.trigger is None:
            return
        no_user = (self.trigger in ON_BEHALF_OF_TRIGGERS
                   or (self.trigger == "agent" and self.user is None))
        if not no_user:
            return
        for t in tools:
            if t.auth == "on_behalf_of":
                self.reject(Code.ON_BEHALF_OF_NO_USER, f"spec.tools[{t.connector}].auth", DEFAULT,
                            attempted=self.trigger)

    # -- output -----------------------------------------------------------------
    def build(self, limits, guard, models, triggers, tools: list[Accepted], sub_agents) -> dict[str, Any]:
        cat, spec = self.cat, self.spec

        # D21: one system message, enterprise -> team -> agent, each stripped.
        parts = [(ENTERPRISE, self.ent.get("prompt"))]
        if self.team_layer:
            parts.append((self.team_layer, (self.team_spec or {}).get("prompt")))
        parts.append((AGENT, dig(spec, ("prompt", "task"))))
        parts = [(l, p.strip()) for l, p in parts if p and p.strip()]
        prompt = "\n\n".join(p for _, p in parts)

        # effective tool permissions, one per (connector, action, auth)
        perms: dict[tuple[str, str, str], dict[str, Any]] = {}
        for t in tools:
            reg = cat.actions(t.connector)
            for a in t.actions:
                wire = cat.wire.get(t.connector, {}).get(a)
                if wire is None:
                    self.reject(Code.UNKNOWN_ACTION, f"spec.tools[{t.connector}]", DEFAULT,
                                attempted=f"{t.connector}:{a}")
                    continue
                key = (t.connector, a, t.auth)
                perm = f"{t.connector}:{a}"
                if key not in perms:
                    perms[key] = {
                        "permission": perm, "connector": t.connector, "action": a,
                        "mcp_tool": wire["mcp_tool"], "resources": {}, "auth": t.auth,
                        "risk": reg[a]["risk"],
                        "requires_approval": (reg[a]["risk"] in self.approval_risks
                                              or match_any(perm, self.approval_actions)),
                    }
                res = perms[key]["resources"]
                for k, v in t.per_action[a].items():
                    res[k] = res.get(k, []) + [x for x in v if x not in res.get(k, [])]
        for p in perms.values():
            if not p["resources"]:
                del p["resources"]

        prov_tools = []
        for t in tools:
            res: dict[str, list[str]] = {}
            for a in t.actions:
                for k, v in t.per_action[a].items():
                    res[k] = res.get(k, []) + [x for x in v if x not in res.get(k, [])]
            value: dict[str, Any] = {"connector": t.connector, "actions": list(t.actions)}
            if res:
                value["resources"] = res
            value["auth"] = t.auth
            if t.synthetic:
                value["synthetic"] = True
            prov_tools.append(leaf(value, t.source, t.narrowed_from))

        budget_p, limits_p = {}, {}
        for k, (v, layer, nf) in limits.items():
            (budget_p if layer in (AGENT, OVERRIDE) else limits_p)[k] = leaf(v, layer, nf)
        eff_limits = {k: (limits[k][0] if k in limits else DEFAULT_LIMITS[k]) for k in PER_RUN}

        flags = guard["flags"]
        g_prov: dict[str, Any] = {
            "redaction": {k[1]: leaf(v, l) for k, (v, l) in flags.items() if k[0] == "redaction"},
            "audit_log": leaf(*flags[("audit_log",)]),
            "trace_payloads": leaf(*guard["trace"]),
        }
        approval: dict[str, Any] = {}
        risks, rsrc = guard["risks"]
        if risks:
            approval["require_for_risk"] = leaf(risks, rsrc[0], rsrc[1])
        if guard["timeout"]:
            approval["timeout_minutes"] = leaf(*guard["timeout"])
        acts, asrc = guard["actions"]
        if acts:
            approval["actions"] = leaf(acts, asrc)
        if approval:
            g_prov["approval"] = approval
        denylist = sorted({p for p, _ in self.deny})
        first_src = {}
        for p, l in self.deny:
            first_src.setdefault(p, l)
        if denylist:
            g_prov["deny"] = [leaf(p, first_src[p]) for p in denylist]   # D3: per element

        model_id, fallback = models["id"], models["fallback"]
        def orid(m):
            return cat.models[m]["spec"]["model"] if m in cat.models else None
        model_p: dict[str, Any] = {"id": leaf(model_id, models["src"]["id"], models["narrowed_from"])}
        if fallback:
            model_p["fallback"] = leaf(fallback, models["src"]["fallback"])
        if models["params"]:
            model_p["params"] = leaf(models["params"], AGENT)

        obs: dict[str, Any] = {}
        for k in ("metrics", "alerts"):
            if dig(spec, ("observability", k)) is not None:
                obs[k] = leaf(spec["observability"][k], AGENT)
        if dig(self.ent, ("observability", "log_retention_days")) is not None:
            obs["log_retention_days"] = leaf(self.ent["observability"]["log_retention_days"], ENTERPRISE)

        buckets = self.buckets()
        memory_p = [leaf({"bucket": m["bucket"], "mode": m["mode"],
                          **({"type": buckets[m["bucket"]][1]["type"]} if m["bucket"] in buckets else {})},
                         AGENT) for m in spec.get("memory") or []]

        ev = spec.get("evaluation") or {}
        evaluation: dict[str, Any] = {}
        for k in ("runs_per_case", "thresholds", "production"):
            if k in ev:
                evaluation[k] = leaf(ev[k], AGENT)
        evaluation["cases"] = [{"id": f"agent/{c['name']}", "case": leaf(c, AGENT)}
                               for c in ev.get("cases") or []]
        defaults = [(ENTERPRISE, c) for c in dig(self.ent, ("evaluation", "default_cases")) or []]
        if self.team_layer:
            defaults += [(self.team_layer, c)
                         for c in dig(self.team_spec, ("evaluation", "default_cases")) or []]
        evaluation["default_cases"] = [{"id": f"{l}/{c['name']}", "case": leaf(c, l)} for l, c in defaults]

        provenance: dict[str, Any] = {
            "use_case": {k: leaf(v, AGENT) for k, v in (spec.get("use_case") or {}).items()},
            "prompt": [leaf(p, l) for l, p in parts],
            "tools": prov_tools,
            "model": model_p,
            "budget": budget_p,
            "limits": limits_p,
            "triggers": [leaf(t, AGENT) for t in triggers],
            "guardrails": g_prov,
            "memory": memory_p,
            "sub_agents": [leaf({"name": n, "version": v}, AGENT, {"declared": ref})
                           for n, v, ref in sub_agents],
            "observability": obs,
            "failure": {k: leaf(v, AGENT) for k, v in (spec.get("failure") or {}).items()},
            "evaluation": evaluation,
            "ownership": {k: leaf(v, AGENT) for k, v in (spec.get("ownership") or {}).items()},
            "locked": [leaf(p, l) for p, l in self.locks],
        }
        for k in ("input", "output"):
            if k in spec:
                provenance[k] = leaf(spec[k], AGENT)

        effective: dict[str, Any] = {
            "tool_permissions": list(perms.values()),
            "denylist": denylist,
            "limits": eff_limits,
            "aggregate_limits": self.aggregate,
            "model": {"id": model_id or "", "openrouter_model": orid(model_id) or "",
                      "fallback": fallback, "fallback_openrouter_model": orid(fallback),
                      "params": models["params"]},
            "guardrails": {
                "redaction": {"secrets": flags[("redaction", "secrets")][0],
                              "pii": flags[("redaction", "pii")][0]},
                "audit_log": flags[("audit_log",)][0],
                "trace_payloads": guard["trace"][0],
                "approval_timeout_minutes": guard["timeout"][0] if guard["timeout"] else None,
            },
            "prompt": prompt,
            "sub_agents": [{"name": n, "version": v} for n, v, _ in sub_agents],
            "triggers": triggers,
        }

        layers = [ENTERPRISE] + ([self.team_layer] if self.team_layer else []) + [AGENT]
        return {
            "agent": self.md["name"],
            "version": str(self.md.get("version", "0.0.0")),
            "level": self.level,
            "team": self.team,
            "access": self.md.get("access"),
            "resolved_at": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
            "layers": layers,
            "caller": {"user": self.user, "team": self.caller_team,
                       "is_enterprise_admin": bool(self.is_admin)},
            "provenance": provenance,
            "effective": effective,
            "rejections": [r.to_dict() for r in self.rej],
        }
