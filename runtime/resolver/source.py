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


def load_catalog(root: str | os.PathLike | None = None) -> Catalog:
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
    return cat
