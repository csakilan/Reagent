"""Where the three layers come from.

SPEC.md §1: the runtime re-reads all three layers on every run and never trusts
layer data copied into a file. Until the database lands (H4) the layers are read
from a spec directory laid out like `spec/examples/`. The resolver only ever sees
a `Catalog`, so swapping in a database-backed loader changes nothing else.

Set REAGENT_SPEC_ROOT to point at a different directory.
"""
from __future__ import annotations

import json
import os
import pathlib
from dataclasses import dataclass, field
from typing import Any

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
DEFAULT_SPEC_ROOT = ROOT / "spec" / "examples"
CONNECTOR_ACTIONS = ROOT / "contracts" / "connector_actions.json"


@dataclass
class Catalog:
    enterprise: dict[str, Any]
    teams: dict[str, dict[str, Any]]
    connectors: dict[str, dict[str, Any]]
    models: dict[str, dict[str, Any]]
    # name -> version -> document. Several versions may coexist (FROZEN D24).
    agents: dict[str, dict[str, dict[str, Any]]] = field(default_factory=dict)
    # connector -> action -> {mcp_tool, risk, resources}. Read, never recomputed.
    wire: dict[str, dict[str, dict[str, Any]]] = field(default_factory=dict)

    # -- lookups ------------------------------------------------------------
    @property
    def enterprise_name(self) -> str:
        return self.enterprise["metadata"]["name"]

    @property
    def ent(self) -> dict[str, Any]:
        return self.enterprise.get("spec", {})

    def team(self, name: str) -> dict[str, Any] | None:
        doc = self.teams.get(name)
        return None if doc is None else doc.get("spec", {})

    def admins(self) -> set[str]:
        return set(self.ent.get("admins", []))

    def team_of(self, user: str | None) -> str | None:
        if user is None:
            return None
        for name, doc in sorted(self.teams.items()):
            if user in doc.get("spec", {}).get("members", []):
                return name
        return None

    def actions(self, connector: str) -> dict[str, dict[str, Any]]:
        doc = self.connectors.get(connector)
        if doc is None:
            return {}
        return {a["name"]: a for a in doc["spec"].get("actions", [])}

    def auth_modes(self, connector: str) -> set[str] | None:
        """None means the connector takes no credentials, so any mode is fine."""
        auth = self.connectors[connector]["spec"].get("auth", {})
        if auth.get("none"):
            return None
        return set(auth)

    def agent_versions(self, name: str) -> dict[str, dict[str, Any]]:
        return self.agents.get(name, {})

    def latest(self, name: str) -> dict[str, Any] | None:
        versions = self.agent_versions(name)
        if not versions:
            return None
        return versions[max(versions, key=semver_key)]

    def add_agent(self, doc: dict[str, Any]) -> None:
        md = doc["metadata"]
        self.agents.setdefault(md["name"], {})[str(md.get("version", "0.0.0"))] = doc


def semver_key(v: str) -> tuple[int, ...]:
    parts = []
    for p in str(v).split("."):
        try:
            parts.append(int(p))
        except ValueError:
            parts.append(-1)
    return tuple(parts + [0] * (3 - len(parts)))


def load_wire() -> dict[str, dict[str, dict[str, Any]]]:
    data = json.loads(CONNECTOR_ACTIONS.read_text(encoding="utf-8"))
    return {
        cname: {a["action"]: a for a in c["actions"]}
        for cname, c in data["connectors"].items()
    }


def load_yaml(path: pathlib.Path) -> Any:
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def iter_documents(root: pathlib.Path):
    """Every YAML document under `root`, with its path. Skips directories."""
    for p in sorted(root.rglob("*")):
        if p.is_file() and p.suffix in (".yaml", ".yml", ".agent"):
            yield p, load_yaml(p)


#: Drafts created in this process (forks) when there is no database to hold them.
_DRAFTS: dict[str, dict[str, Any]] = {}


def register_draft(doc: dict[str, Any]) -> None:
    _DRAFTS[doc["metadata"]["name"]] = doc


#: Layer edits made by enterprise admins in this process when there is no database
#: (runtime/resolver/layers.py). Keyed by kind, then name; applied on every load.
_LAYERS: dict[str, dict[str, dict[str, Any]]] = {"Enterprise": {}, "Team": {}, "Connector": {}, "Model": {}}


def overlay(doc: dict[str, Any]) -> None:
    _LAYERS[doc["kind"]][doc["metadata"]["name"]] = doc


def load_catalog(root: str | os.PathLike | None = None) -> Catalog:
    """The layers, re-read on every call. REAGENT_LAYERS=db reads the database
    (SPEC.md §1); anything else, or an explicit `root`, reads a spec directory."""
    if root is None and os.environ.get("REAGENT_LAYERS", "files").lower() == "db":
        return load_catalog_db()
    return load_catalog_files(root)


def load_catalog_db(url: str | None = None) -> Catalog:
    """Every layer from the tables in contracts/db/001_init.sql. Each row's `spec`
    column holds the whole document, exactly as the files do."""
    import psycopg

    with psycopg.connect(url or os.environ.get("DATABASE_URL") or "", connect_timeout=15) as c:
        ent = c.execute("SELECT spec FROM enterprise ORDER BY id LIMIT 1").fetchone()
        if ent is None:
            raise LookupError("no enterprise layer in the database")
        teams = {n: s for n, s in c.execute("SELECT name, spec FROM teams")}
        reg = c.execute("SELECT kind, name, spec FROM registry_items "
                        "WHERE status IS DISTINCT FROM 'deprecated'").fetchall()
        agents = [s for (s,) in c.execute(
            "SELECT v.spec FROM agent_versions v JOIN agents a ON a.id = v.agent_id "
            "WHERE v.status IN ('published', 'draft')")]
    cat = Catalog(ent[0], teams,
                  {n: s for k, n, s in reg if k == "connector"},
                  {n: s for k, n, s in reg if k == "model"}, wire=load_wire())
    for a in agents:
        cat.add_agent(a)
    return cat


def load_catalog_files(root: str | os.PathLike | None = None) -> Catalog:
    root = pathlib.Path(root or os.environ.get("REAGENT_SPEC_ROOT") or DEFAULT_SPEC_ROOT)
    enterprise = None
    teams: dict[str, Any] = {}
    connectors: dict[str, Any] = {}
    models: dict[str, Any] = {}
    agents: list[dict[str, Any]] = []
    for _, doc in iter_documents(root):
        if not isinstance(doc, dict):
            continue
        kind = doc.get("kind")
        name = doc.get("metadata", {}).get("name")
        if kind == "Enterprise":
            enterprise = doc
        elif kind == "Team":
            teams[name] = doc
        elif kind == "Connector":
            connectors[name] = doc
        elif kind == "Model":
            models[name] = doc
        elif kind == "Agent":
            agents.append(doc)
    if enterprise is None:
        raise FileNotFoundError(f"no Enterprise document under {root}")
    cat = Catalog(enterprise, teams, connectors, models, wire=load_wire())
    for a in agents:
        cat.add_agent(a)
    for d in _DRAFTS.values():
        if d["metadata"]["name"] not in cat.agents:
            cat.add_agent(d)
    for d in _LAYERS["Enterprise"].values():
        cat.enterprise = d
    cat.teams.update(_LAYERS["Team"])
    cat.connectors.update(_LAYERS["Connector"])
    cat.models.update(_LAYERS["Model"])
    return cat
