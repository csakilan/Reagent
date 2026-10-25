"""Schema validation + semantic tighten-only checks for ReAgent v0 examples."""
import json, sys, glob, fnmatch, os
import yaml
from jsonschema import Draft202012Validator

root = sys.argv[1]
schema = json.load(open(f"{root}/schema/reagent.schema.json"))
# The schema's $id ("reagent/v0") is relative. Older jsonschema (the apt package
# CI picks up) resolves it against itself and tries to fetch "reagent/reagent/v0".
# An absolute id on the in-memory copy keeps every $ref local, on old and new.
schema["$id"] = "urn:reagent:" + schema.get("$id", "schema")
Draft202012Validator.check_schema(schema)
V = Draft202012Validator(schema)

ok = True
def fail(msg):
    global ok
    ok = False
    print("  FAIL:", msg)

docs = {}
for f in sorted(glob.glob(f"{root}/examples/**/*", recursive=True)):
    if os.path.isdir(f):
        continue
    d = yaml.safe_load(open(f))
    errs = list(V.iter_errors(d))
    print(("OK   " if not errs else "FAIL ") + os.path.relpath(f, root))
    for e in errs:
        fail(f"{list(e.path)} {e.message[:160]}")
    docs.setdefault(d["kind"], {})[d["metadata"]["name"]] = d

ent = docs["Enterprise"]["nmc2"]["spec"]
teams = {k: v["spec"] for k, v in docs["Team"].items()}
conns = docs["Connector"]
models = docs["Model"]
agents = docs["Agent"]
m = lambda a, pats: any(fnmatch.fnmatch(a, p) for p in pats)

def chain(team):  # one team layer: [enterprise, team]
    if team not in teams:
        fail(f"unknown team {team}")
        return [("enterprise", ent)]
    return [("enterprise", ent), (team, teams[team])]

# evaluation case checks
def check_cases(owner, cases):
    for c in cases:
        for se in c["expect"].get("side_effects", []):
            if "mocks" not in c: fail(f"{owner}/{c['name']}: side_effects need a mocked sandbox")
        if c.get("kind") == "safety" and "judge" in c["expect"] and len(c["expect"]) == 1:
            fail(f"{owner}/{c['name']}: safety case relies on the LLM judge alone; add a code check")

check_cases("enterprise", ent.get("evaluation", {}).get("default_cases", []))
if ent.get("evaluation", {}).get("judge", {}).get("model") not in models:
    fail("enterprise judge model not in registry")

def ancestors(team):
    return [n for n, _ in chain(team)]

buckets = {b["name"]: ("enterprise", b) for b in ent.get("memory", {}).get("buckets", [])}
for tn, t in teams.items():
    for b in t.get("memory", {}).get("buckets", []):
        buckets[b["name"]] = (tn, b)

def principal_ok(plist, team):
    return "*" in plist or any(a in plist for a in ancestors(team))

print("\nSemantic checks")
# team layers: subsets of parent
for tn, t in teams.items():
    parent_layers = chain(tn)[:-1]
    for cn, g in t.get("connectors", {}).items():
        if cn == "platform": fail(f"team {tn} granted platform connector")
        pg = parent_layers[-1][1].get("connectors", {}).get(cn)
        if pg is None: fail(f"team {tn} grants {cn} but parent doesn't"); continue
        acts = [a["name"] for a in conns[cn]["spec"]["actions"]]
        for a in acts:
            if m(a, g.get("allow", [])) and not m(a, g.get("deny", [])):
                for ln, l in parent_layers:
                    lg = l.get("connectors", {}).get(cn, {})
                    if not (m(a, lg.get("allow", [])) and not m(a, lg.get("deny", []))):
                        fail(f"team {tn} allows {cn}:{a} beyond {ln}")
        for rt, vals in g.get("resources", {}).items():
            pr = parent_layers[-1][1].get("connectors", {}).get(cn, {}).get("resources", {}).get(rt)
            if pr:
                for v in vals:
                    if not m(v, pr) and not any(fnmatch.fnmatch(v.rstrip("*"), p) for p in pr):
                        fail(f"team {tn} resource {rt}={v} outside parent {pr}")
    pm = parent_layers[-1][1].get("models", {}).get("allow", ["*"])
    for mid in t.get("models", {}).get("allow", []):
        if not m(mid, pm): fail(f"team {tn} model {mid} not allowed by parent")
    for k, v in t.get("limits", {}).items():
        for ln, l in parent_layers:
            if k in l.get("limits", {}) and v > l["limits"][k]:
                fail(f"team {tn} limit {k}={v} > {ln}")

for name, a in agents.items():
    md, s = a["metadata"], a["spec"]
    print(" agent", name, f"({md['level']}{', ' + md.get('team','') if md['level']=='team' else ', ' + md['access']})")
    layers = chain(md["team"]) if md["level"] == "team" else [("enterprise", ent)]
    # models
    allowed_models = ["*"]
    for ln, l in layers:
        if "models" in l: allowed_models = l["models"]["allow"]
    for mid in [s["model"]["id"], s["model"].get("fallback")]:
        if mid is None: continue
        if mid not in models: fail(f"unknown model {mid}")
        if not m(mid, allowed_models): fail(f"model {mid} not allowed")
    # tools
    for p in s.get("tools", []):
        cn = p["connector"]
        c = conns.get(cn)
        if not c: fail(f"unknown connector {cn}"); continue
        if cn == "platform" and md["level"] != "enterprise": fail("team agent uses platform connector")
        acts = {x["name"] for x in c["spec"]["actions"]}
        for act in p["actions"]:
            if act not in acts: fail(f"{cn}:{act} not an action")
            for ln, l in layers:
                g = l.get("connectors", {}).get(cn)
                if g is None: fail(f"{cn} not granted at {ln}"); break
                if not (m(act, g.get("allow", [])) and not m(act, g.get("deny", []))):
                    fail(f"{cn}:{act} blocked at {ln}")
        for rt, vals in p.get("resources", {}).items():
            for ln, l in layers:
                lr = l.get("connectors", {}).get(cn, {}).get("resources", {}).get(rt)
                if lr:
                    for v in vals:
                        if not m(v, lr): fail(f"resource {rt}={v} outside {ln} scope {lr}")
        if p.get("auth", "service") not in c["spec"]["auth"]: fail(f"{cn} lacks auth {p.get('auth')}")
    # memory
    for mem in s.get("memory", []):
        if mem["bucket"] not in buckets: fail(f"unknown bucket {mem['bucket']}"); continue
        _, b = buckets[mem["bucket"]]
        if md["level"] == "team":
            if not principal_ok(b.get("read", []), md["team"]): fail(f"can't read {mem['bucket']}")
            if "write" in mem["mode"] and not principal_ok(b.get("write", []), md["team"]): fail(f"can't write {mem['bucket']}")
            if b["type"] == "episodic" and "write" in mem["mode"]: fail("agents can't write episodic memory")
            if not any(l.get("connectors", {}).get("memory") for _, l in layers[1:]): fail("memory not granted to team")
    # sub agents
    for sub in s.get("sub_agents", []):
        r = sub["ref"].split("@")[0]
        if r not in agents: fail(f"unknown sub-agent {r}"); continue
        tm = agents[r]["metadata"]
        if not (tm.get("access") == "service" or (tm["level"] == "team" and tm.get("team") == md.get("team"))):
            fail(f"sub-agent {r} not callable")
    # forks
    if "forked_from" in md:
        src = agents.get(md["forked_from"].split("@")[0])
        if not src: fail("forked_from unknown")
        elif src["metadata"].get("team") != md.get("team"): fail("fork source outside team collection")
    check_cases(name, s.get("evaluation", {}).get("cases", []))
    # publish-pipeline coverage (stage 3): report what the evaluator must still generate
    req = ent.get("evaluation", {}).get("requirements", {})
    own = s.get("evaluation", {}).get("cases", [])
    allc = own + ent.get("evaluation", {}).get("default_cases", [])
    gaps = []
    for kind, n in req.get("min_cases", {}).items():
        have = sum(1 for c in allc if c.get("kind", "positive") == kind)
        if have < n: gaps.append(f"{n - have} {kind}")
    if req.get("tool_coverage"):
        mentioned = set()
        for c in own:
            e = c["expect"]
            mentioned |= set(e.get("tool_calls", [])) | set(e.get("no_tool_calls", []))
            mentioned |= {x["action"] for x in e.get("side_effects", [])}
            mentioned |= set(c.get("mocks", {}).keys())
        for p_ in s.get("tools", []):
            for act in p_["actions"]:
                if f"{p_['connector']}:{act}" not in mentioned: gaps.append(f"case for {p_['connector']}:{act}")
    if gaps: print("   stage 3 would generate:", ", ".join(gaps))
    # limits
    for k, v in s.get("budget", {}).items():
        for ln, l in layers:
            if k in l.get("limits", {}) and v > l["limits"][k]: fail(f"budget {k}={v} > {ln} {l['limits'][k]}")
    # triggers
    allowed_tr = ent["triggers"]["allow"]
    for ln, l in layers:
        if "triggers" in l: allowed_tr = l["triggers"]["allow"]
    for t in s["triggers"]:
        if t["type"] not in allowed_tr: fail(f"trigger {t['type']} not allowed")
    # locked
    for ln, l in layers[:-1] if md["level"] == "team" else []:
        pass

print("\nALL PASS" if ok else "\nFAILURES")
sys.exit(0 if ok else 1)
