"""One test (at least) per row of SPEC.md §1's merge table, plus the omitted-field
rules and every FROZEN decision the resolver enforces.

Each test mutates a deep copy of the real example layers, so a failure here
reads in terms of the demo data: platform-eng, people-ops, pr-reviewer.
"""
from __future__ import annotations

import copy

import pytest

from runtime.resolver import load_catalog, resolve_document
from runtime.resolver.globs import is_subset, match

_BASE = load_catalog()
#: platform-eng's github repo scope in spec/examples (the live demo repo included).
TEAM_REPOS = ["nmc2/infra", "nmc2/platform-api", "csakilan/reagent-demo"]


@pytest.fixture
def cat():
    return copy.deepcopy(_BASE)


def agent(cat, name):
    return copy.deepcopy(cat.latest(name))


def ent(cat):
    return cat.enterprise["spec"]


def team(cat, name="platform-eng"):
    return cat.teams[name]["spec"]


def resolve(cat, doc, caller=None, **kw):
    return resolve_document(doc, catalog=cat, caller=caller, **kw)


def codes(doc):
    return [(r["code"], r["blocked_by"]) for r in doc["rejections"]]


def perms(doc):
    return {t["permission"] for t in doc["effective"]["tool_permissions"]}


def perm(doc, p):
    return next(t for t in doc["effective"]["tool_permissions"] if t["permission"] == p)


def the(doc, code):
    hits = [r for r in doc["rejections"] if r["code"] == code]
    assert hits, f"expected {code}, got {codes(doc)}"
    return hits[0]


# =============================================================================
# Row 1. Allowlists (connector actions, resources, models, trigger types):
#        INTERSECTION, the child must be a subset of the parent. (FROZEN D10)
# =============================================================================

def test_row1_actions_child_subset_is_accepted(cat):
    doc = resolve(cat, agent(cat, "pr-reviewer"))
    assert doc["rejections"] == []
    assert {"github:pulls.read", "github:pulls.comment"} <= perms(doc)


def test_row1_action_outside_team_grant_is_refused_naming_the_team(cat):
    a = agent(cat, "pr-reviewer")
    a["spec"]["tools"][0]["actions"].append("contents.write")
    r = the(resolve(cat, a), "E_ACTION_NOT_GRANTED")
    assert r["blocked_by"] == "team:platform-eng"
    assert "github:contents.write" in r["message"] and "team:platform-eng" in r["message"]


def test_row1_team_element_matching_nothing_in_parent_fails_not_dropped(cat):
    """D10: a child element outside the parent is an error, never silently dropped."""
    ent(cat)["connectors"]["github"]["allow"] = ["pulls.*"]
    doc = resolve(cat, agent(cat, "pr-reviewer"))
    r = the(doc, "E_WIDEN_ALLOWLIST")
    assert r["blocked_by"] == "enterprise"
    assert r["field"] == "teams/platform-eng.spec.connectors.github.allow[2]"   # issues.*


def test_row1_intersection_is_across_all_three_layers_not_last_wins(cat):
    """The prototype did last-layer-wins. Enterprise narrowing must bind the agent."""
    ent(cat)["connectors"]["github"]["allow"] = ["pulls.read"]
    team(cat)["connectors"]["github"].pop("allow")          # team inherits pulls.read only
    doc = resolve(cat, agent(cat, "pr-reviewer"))
    r = the(doc, "E_ACTION_NOT_GRANTED")
    assert r["attempted"] == "github:pulls.comment" and r["blocked_by"] == "enterprise"


def test_row1_resource_widening_is_refused(cat):
    a = agent(cat, "pr-reviewer")
    a["spec"]["tools"][0]["resources"] = {"repo": ["nmc2/*"]}
    r = the(resolve(cat, a), "E_RESOURCE_OUT_OF_SCOPE")
    assert r["blocked_by"] == "team:platform-eng"
    assert r["attempted"] == ["nmc2/*"]


def test_row1_models_must_be_in_team_allowlist(cat):
    a = agent(cat, "pr-reviewer")
    a["spec"]["model"]["id"] = "gpt-oss"
    r = the(resolve(cat, a), "E_MODEL_NOT_ALLOWED")
    assert r["blocked_by"] == "team:platform-eng"


def test_row1_team_model_allowlist_must_be_subset_of_enterprise(cat):
    ent(cat)["models"]["allow"] = ["qwen-coder", "claude-sonnet"]
    doc = resolve(cat, agent(cat, "pr-reviewer"))
    r = the(doc, "E_WIDEN_ALLOWLIST")
    assert r["field"] == "teams/platform-eng.spec.models.allow[2]" and r["attempted"] == ["gpt-mini"]


def test_row1_trigger_types_intersect(cat):
    ent(cat)["triggers"]["allow"] = ["http", "mcp"]
    r = the(resolve(cat, agent(cat, "pr-reviewer")), "E_TRIGGER_NOT_ALLOWED")
    assert r["attempted"] == "github" and r["blocked_by"] == "enterprise"


def test_row1_team_triggers_are_checked_against_enterprise(cat):
    """D10: the prototype never checked team triggers against the enterprise."""
    ent(cat)["triggers"]["allow"] = ["http", "mcp", "github"]
    team(cat)["triggers"] = {"allow": ["http", "slack"]}
    doc = resolve(cat, agent(cat, "pr-reviewer"))
    assert ("E_WIDEN_ALLOWLIST", "enterprise") in codes(doc)          # slack
    assert ("E_TRIGGER_NOT_ALLOWED", "team:platform-eng") in codes(doc)  # pr-reviewer's github


# =============================================================================
# Row 2. Denylists: UNION. (FROZEN D13: both forms, one list, deny beats allow)
# =============================================================================

def test_row2_denylist_unions_both_forms_across_layers(cat):
    team(cat).setdefault("guardrails", {})["deny"] = ["github:issues.create"]
    doc = resolve(cat, agent(cat, "pr-reviewer"))
    assert set(doc["effective"]["denylist"]) == {
        "github:repos.delete", "github:branches.force_push",   # enterprise, bare -> qualified
        "github:issues.create",                                # team guardrails.deny
        "github:pulls.merge",                                  # agent guardrails.deny
    }


def test_row2_deny_beats_allow(cat):
    """With pulls.* granted, the agent's own deny still wins."""
    team(cat)["connectors"]["github"]["allow"] = ["pulls.*"]
    a = agent(cat, "pr-reviewer")
    a["spec"]["tools"][0]["actions"].append("pulls.merge")
    r = the(resolve(cat, a), "E_DENIED_ACTION")
    assert r["attempted"] == "github:pulls.merge" and r["blocked_by"] == "agent"
    assert "github:pulls.merge" not in perms(resolve(cat, a))


def test_row2_guardrails_deny_is_read_at_team_level(cat):
    """The prototype never read guardrails.deny at all."""
    team(cat).setdefault("guardrails", {})["deny"] = ["github:pulls.comment"]
    r = the(resolve(cat, agent(cat, "pr-reviewer")), "E_DENIED_ACTION")
    assert r["blocked_by"] == "team:platform-eng"


def test_row2_enterprise_bare_deny_blocks_a_glob_grant(cat):
    team(cat)["connectors"]["github"]["allow"] = ["*"]
    a = agent(cat, "pr-reviewer")
    a["spec"]["tools"][0]["actions"] = ["repos.delete"]
    r = the(resolve(cat, a), "E_DENIED_ACTION")
    assert r["blocked_by"] == "enterprise"


# =============================================================================
# Row 3. Numeric limits: MINIMUM. (FROZEN D7 splits per-run from aggregate)
# =============================================================================

def test_row3_minimum_wins_and_is_tagged(cat):
    doc = resolve(cat, agent(cat, "pr-reviewer"))
    assert doc["effective"]["limits"] == {"usd_per_run": 0.25, "max_steps": 25,
                                          "timeout_seconds": 240, "max_call_depth": 3}
    assert doc["provenance"]["limits"]["max_call_depth"]["source"] == "enterprise"
    assert doc["provenance"]["budget"]["timeout_seconds"]["narrowed_from"] == \
        {"value": 900, "from_layer": "enterprise"}


def test_row3_raising_a_limit_is_refused_naming_field_and_layer(cat):
    a = agent(cat, "pr-reviewer")
    a["spec"]["budget"]["timeout_seconds"] = 1200
    r = the(resolve(cat, a), "E_LIMIT_ABOVE_PARENT")
    assert r["field"] == "spec.budget.timeout_seconds" and r["blocked_by"] == "enterprise"
    assert r["message"] == ("spec.budget.timeout_seconds is 1200, above the limit of 900 set by "
                            "enterprise. A lower layer may set a smaller value, never a larger one.")


def test_row3_team_limit_above_enterprise_is_refused(cat):
    team(cat)["limits"]["max_concurrent_runs"] = 500
    r = the(resolve(cat, agent(cat, "pr-reviewer")), "E_LIMIT_ABOVE_PARENT")
    assert r["field"] == "teams/platform-eng.spec.limits.max_concurrent_runs"


def test_row3_override_below_the_agent_is_tighten_only(cat):
    """A run-time override (eval sweep, cost-optimizer) may not loosen the agent."""
    doc = resolve(cat, agent(cat, "pr-reviewer"),
                  overrides={"spec": {"budget": {"timeout_seconds": 900}}})
    r = the(doc, "E_LIMIT_ABOVE_PARENT")
    assert r["blocked_by"] == "agent" and r["allowed"] == 240
    ok = resolve(cat, agent(cat, "pr-reviewer"), overrides={"spec": {"budget": {"max_steps": 5}}})
    assert ok["rejections"] == [] and ok["effective"]["limits"]["max_steps"] == 5


def test_row3_aggregates_are_per_layer_not_minimums(cat):
    """D7: usd_per_month and max_concurrent_runs are summed per layer, never min'd."""
    doc = resolve(cat, agent(cat, "pr-reviewer"))
    assert "usd_per_month" not in doc["effective"]["limits"]
    assert doc["effective"]["aggregate_limits"] == {
        "enterprise": {"usd_per_month": 1000, "max_concurrent_runs": 50},
        "team": {"usd_per_month": 200, "max_concurrent_runs": 5},
        "agent": {"usd_per_month": 50, "max_concurrent_runs": None},
    }


def test_row3_approval_timeout_follows_minimum(cat):
    """D16."""
    team(cat)["guardrails"]["approval"]["timeout_minutes"] = 15
    doc = resolve(cat, agent(cat, "pr-reviewer"))
    assert doc["effective"]["guardrails"]["approval_timeout_minutes"] == 15
    team(cat)["guardrails"]["approval"]["timeout_minutes"] = 600
    assert the(resolve(cat, agent(cat, "pr-reviewer")), "E_LIMIT_ABOVE_PARENT")["blocked_by"] == "enterprise"


# =============================================================================
# Row 4. Safety flags: STRICTER WINS. A flag that is on cannot be turned off below.
# =============================================================================

def test_row4_safety_flag_cannot_be_switched_off_below(cat):
    ent(cat)["locked"] = []                        # prove the flag rule alone holds
    team(cat)["guardrails"]["redaction"] = {"secrets": False}
    doc = resolve(cat, agent(cat, "pr-reviewer"))
    r = the(doc, "E_SAFETY_FLAG_DISABLED")
    assert r["field"] == "teams/platform-eng.spec.guardrails.redaction.secrets"
    assert r["blocked_by"] == "enterprise"
    assert doc["effective"]["guardrails"]["redaction"]["secrets"] is True


def test_row4_safety_flag_can_be_switched_on_below(cat):
    ent(cat)["locked"] = []
    ent(cat)["guardrails"]["redaction"]["pii"] = False
    team(cat)["guardrails"]["redaction"] = {"pii": True}
    doc = resolve(cat, agent(cat, "pr-reviewer"))
    assert doc["rejections"] == []
    assert doc["effective"]["guardrails"]["redaction"]["pii"] is True
    assert doc["provenance"]["guardrails"]["redaction"]["pii"]["source"] == "team:platform-eng"


def test_row4_audit_log_cannot_be_switched_off_by_the_agent(cat):
    ent(cat)["locked"] = []
    a = agent(cat, "pr-reviewer")
    a["spec"]["guardrails"]["redaction"] = {"secrets": False}
    assert ("E_SAFETY_FLAG_DISABLED", "enterprise") in codes(resolve(cat, a))


# =============================================================================
# Row 5. Approval requirements: UNION.
# =============================================================================

def test_row5_approval_requirements_union(cat):
    doc = resolve(cat, agent(cat, "pr-reviewer"))
    assert perm(doc, "github:pulls.comment")["requires_approval"] is True   # team: write
    assert perm(doc, "github:pulls.read")["requires_approval"] is False


def test_row5_a_lower_layer_cannot_clear_an_approval_rule(cat):
    """Clearing require_for_risk at the team leaves the enterprise rule standing."""
    team(cat)["guardrails"]["approval"]["require_for_risk"] = []
    team(cat)["connectors"]["github"]["allow"].append("contents.write")
    a = agent(cat, "pr-reviewer")
    a["spec"]["guardrails"]["approval"]["actions"] = []
    doc = resolve(cat, a)
    assert perm(doc, "github:pulls.comment")["requires_approval"] is False  # write no longer gated
    ent(cat)["guardrails"]["approval"]["require_for_risk"] = ["write", "destructive"]
    assert perm(resolve(cat, a), "github:pulls.comment")["requires_approval"] is True


def test_row5_agent_can_add_an_approval_action(cat):
    team(cat)["guardrails"]["approval"]["require_for_risk"] = []
    doc = resolve(cat, agent(cat, "pr-reviewer"))     # agent lists github:pulls.comment
    assert perm(doc, "github:pulls.comment")["requires_approval"] is True


# =============================================================================
# Row 6. Prompts: APPENDED enterprise -> team -> agent. (FROZEN D21)
# =============================================================================

def test_row6_prompt_is_one_message_in_order_stripped(cat):
    doc = resolve(cat, agent(cat, "pr-reviewer"))
    parts = [p["value"] for p in doc["provenance"]["prompt"]]
    assert [p["source"] for p in doc["provenance"]["prompt"]] == \
        ["enterprise", "team:platform-eng", "agent"]
    assert doc["effective"]["prompt"] == "\n\n".join(parts)
    assert all(p == p.strip() for p in parts)


def test_row6_enterprise_agent_never_sees_the_callers_team_prompt(cat):
    doc = resolve(cat, agent(cat, "evaluator"), root_team="platform-eng")
    assert [p["source"] for p in doc["provenance"]["prompt"]] == ["enterprise", "agent"]
    assert team(cat)["prompt"] not in doc["effective"]["prompt"]


# =============================================================================
# Row 7. Default evals: APPENDED; inherited cases run alongside the agent's own.
# =============================================================================

def test_row7_default_cases_are_appended_with_scoped_identity(cat):
    """D15: identity is <scope>/<name>."""
    team(cat)["evaluation"] = {"default_cases": [
        {"name": "cites-file-and-line", "kind": "positive", "input": {},
         "expect": {"output_contains": [":"]}}]}
    ev = resolve(cat, agent(cat, "pr-reviewer"))["provenance"]["evaluation"]
    ids = [c["id"] for c in ev["default_cases"]]
    assert ids == ["enterprise/never-echoes-a-secret", "enterprise/resists-prompt-injection",
                   "team:platform-eng/cites-file-and-line"]
    assert "agent/flags-leaked-aws-key" in [c["id"] for c in ev["cases"]]


# =============================================================================
# Row 8. Free parameters (temperature, max_tokens, retries): OVERRIDE.
# =============================================================================

def test_row8_free_parameters_override(cat):
    doc = resolve(cat, agent(cat, "pr-reviewer"),
                  overrides={"spec": {"model": {"params": {"temperature": 0.9}}}})
    assert doc["rejections"] == []
    assert doc["effective"]["model"]["params"] == {"temperature": 0.9, "max_tokens": 4096}


def test_row8_override_may_pick_any_team_model_but_not_beyond(cat):
    ok = resolve(cat, agent(cat, "pr-reviewer"), overrides={"spec": {"model": {"id": "gpt-mini"}}})
    assert ok["rejections"] == [] and ok["effective"]["model"]["openrouter_model"] == "openai/gpt-5-mini"
    bad = resolve(cat, agent(cat, "pr-reviewer"), overrides={"spec": {"model": {"id": "gpt-oss"}}})
    assert the(bad, "E_MODEL_NOT_ALLOWED")["blocked_by"] == "team:platform-eng"


# =============================================================================
# Row 9. locked: [paths]. PREFIX locks that union and never relax. (FROZEN D5)
# =============================================================================

def test_row9_locked_path_refuses_even_the_same_value(cat):
    a = agent(cat, "pr-reviewer")
    a["spec"]["guardrails"]["redaction"] = {"secrets": True}
    r = the(resolve(cat, a), "E_LOCKED_PATH")
    assert r["field"] == "spec.guardrails.redaction.secrets" and r["blocked_by"] == "enterprise"
    assert r["message"] == ("spec.guardrails.redaction.secrets is locked by enterprise and cannot "
                            "be set at a lower layer.")


def test_row9_lock_is_a_prefix_on_whole_components(cat):
    team(cat)["locked"] = ["limits.max_steps"]
    a = agent(cat, "pr-reviewer")                    # sets budget.max_steps (== limits.max_steps)
    doc = resolve(cat, a)
    r = the(doc, "E_LOCKED_PATH")
    assert r["field"] == "spec.budget.max_steps" and r["blocked_by"] == "team:platform-eng"
    assert sum(1 for x in doc["rejections"] if x["code"] == "E_LOCKED_PATH") == 1


def test_row9_locks_union_across_layers(cat):
    team(cat)["locked"] = ["limits.max_steps"]
    a = agent(cat, "pr-reviewer")
    del a["spec"]["budget"]["max_steps"]
    doc = resolve(cat, a)
    assert doc["rejections"] == []
    assert {(l["value"], l["source"]) for l in doc["provenance"]["locked"]} >= {
        ("guardrails.redaction", "enterprise"), ("limits.max_steps", "team:platform-eng")}


def test_row9_team_cannot_set_an_enterprise_locked_path(cat):
    team(cat)["guardrails"]["audit_log"] = False
    assert ("E_LOCKED_PATH", "enterprise") in codes(resolve(cat, agent(cat, "pr-reviewer")))


# =============================================================================
# "What an omitted field means" (SPEC.md §1)
# =============================================================================

def test_omitted_connector_means_no_access(cat):
    """people-ops was never granted GitHub."""
    a = agent(cat, "people-ops-bot")
    a["spec"]["tools"].append({"connector": "github", "actions": ["pulls.read"]})
    r = the(resolve(cat, a), "E_CONNECTOR_NOT_GRANTED")
    assert r["blocked_by"] == "team:people-ops"


def test_omitted_models_triggers_limits_guardrails_inherit_from_enterprise(cat):
    doc = resolve(cat, agent(cat, "people-ops-bot"))     # team omits triggers and guardrails
    assert doc["rejections"] == []
    g = doc["provenance"]["guardrails"]
    assert g["redaction"]["secrets"]["source"] == "enterprise"
    assert g["approval"]["require_for_risk"]["value"] == ["destructive"]
    assert doc["effective"]["limits"]["max_call_depth"] == 3


def test_omitted_tools_mean_no_tools(cat):
    a = agent(cat, "pr-reviewer")
    del a["spec"]["tools"], a["spec"]["memory"]
    assert resolve(cat, a)["effective"]["tool_permissions"] == []


def test_omitted_team_allowlist_inherits_parent_set_unchanged(cat):
    """D10."""
    del team(cat)["connectors"]["github"]["allow"]
    a = agent(cat, "pr-reviewer")
    a["spec"]["tools"][0]["actions"].append("contents.write")   # enterprise allows *
    doc = resolve(cat, a)
    assert doc["rejections"] == [] and "github:contents.write" in perms(doc)


def test_omitted_resources_inherit_the_parent_scope(cat):
    """D18."""
    a = agent(cat, "pr-reviewer")
    del a["spec"]["tools"][0]["resources"]
    doc = resolve(cat, a)
    assert perm(doc, "github:pulls.read")["resources"] == {"repo": TEAM_REPOS}


# =============================================================================
# FROZEN decisions the resolver owns
# =============================================================================

def test_d18_rstrip_widening_is_refused(cat):
    """`nmc2/platform-api*` also matches `nmc2/platform-api-secrets`."""
    a = agent(cat, "pr-reviewer")
    a["spec"]["tools"][0]["resources"] = {"repo": ["nmc2/platform-api*"]}
    assert the(resolve(cat, a), "E_RESOURCE_OUT_OF_SCOPE")["attempted"] == ["nmc2/platform-api*"]


def test_d18_team_resource_glob_must_be_inside_enterprise_scope(cat):
    ent(cat)["connectors"]["github"]["resources"] = {"repo": ["nmc2/*"]}
    team(cat)["connectors"]["github"]["resources"] = {"repo": ["*"]}
    r = the(resolve(cat, agent(cat, "pr-reviewer")), "E_RESOURCE_OUT_OF_SCOPE")
    assert r["blocked_by"] == "enterprise"


def test_d18_undeclared_resource_type_fails(cat):
    a = agent(cat, "pr-reviewer")
    a["spec"]["tools"][0]["resources"] = {"channel": ["#x"]}
    assert the(resolve(cat, a), "E_RESOURCE_OUT_OF_SCOPE")["blocked_by"] == "enterprise"


def test_d18_star_never_crosses_a_slash():
    assert match("nmc2/infra", "nmc2/*") and not match("nmc2/infra/x", "nmc2/*")
    assert not is_subset("nmc2/platform-api*", ["nmc2/platform-api"])
    assert is_subset("#platform-eng-alerts", ["#platform-eng*"])
    assert is_subset("a*", ["a", "a*b", "a*"]) and not is_subset("a*", ["a", "ab*"])


def test_d1_enterprise_agent_is_scoped_to_the_root_runs_team(cat):
    """`service` gets no exemption: a People Ops caller cannot reach GitHub through it."""
    s = agent(cat, "secret-scanner")
    s["spec"]["tools"] = [{"connector": "github", "actions": ["pulls.read", "contents.read"]}]
    people = resolve(cat, s, root_team="people-ops")
    assert not any(t["connector"] == "github" for t in people["effective"]["tool_permissions"])
    plat = resolve(cat, s, root_team="platform-eng")
    assert perm(plat, "github:pulls.read")["resources"] == {"repo": TEAM_REPOS}


def test_d1_enterprise_agent_keeps_platform_connector(cat):
    doc = resolve(cat, agent(cat, "evaluator"), caller="alice")
    assert "platform:run_evals" in perms(doc)


def test_d2_on_behalf_of_refused_without_a_user(cat):
    doc = resolve(cat, agent(cat, "people-ops-bot"), trigger="schedule")
    assert the(doc, "E_ON_BEHALF_OF_NO_USER")
    assert resolve(cat, agent(cat, "people-ops-bot"), caller="dana", trigger="http")["rejections"] == []


def test_d3_member_sees_enterprise_leaves_redacted_but_tagged(cat):
    doc = resolve(cat, agent(cat, "pr-reviewer"), caller="alice")
    assert doc["caller"] == {"user": "alice", "team": "platform-eng", "is_enterprise_admin": False}
    assert doc["effective"]["prompt"] is None
    lim = doc["provenance"]["limits"]["max_call_depth"]
    assert lim == {"value": None, "source": "enterprise", "redacted": True}
    assert doc["provenance"]["budget"]["timeout_seconds"]["narrowed_from"] == \
        {"from_layer": "enterprise", "redacted": True}
    assert doc["effective"]["aggregate_limits"]["enterprise"] == \
        {"usd_per_month": None, "max_concurrent_runs": None}


def test_d3_admin_sees_real_values(cat):
    doc = resolve(cat, agent(cat, "pr-reviewer"), caller="frank")
    assert doc["caller"]["is_enterprise_admin"] is True
    assert doc["provenance"]["limits"]["max_call_depth"]["value"] == 3
    assert doc["effective"]["prompt"].startswith("You are an internal NMC2 agent.")


def test_d6_team_cannot_redeclare_an_enterprise_bucket(cat):
    team(cat)["memory"]["buckets"].append(
        {"name": "enterprise-kb", "type": "semantic", "read": ["*"], "write": ["*"]})
    r = the(resolve(cat, agent(cat, "pr-reviewer")), "E_BUCKET_REDEFINED")
    assert r["blocked_by"] == "enterprise"


def test_d6_team_bucket_may_name_only_its_own_team(cat):
    team(cat)["memory"]["buckets"][0]["write"] = ["*"]
    r = the(resolve(cat, agent(cat, "pr-reviewer")), "E_WIDEN_ALLOWLIST")
    assert r["field"] == "teams/platform-eng.spec.memory.buckets[0].write"


def test_d6_bucket_access_and_episodic_writes(cat):
    a = agent(cat, "pr-reviewer")
    a["spec"]["memory"] = [{"bucket": "people-ops-faq", "mode": "read"}]
    assert the(resolve(cat, a), "E_BUCKET_ACCESS_DENIED")["blocked_by"] == "team:people-ops"
    a["spec"]["memory"] = [{"bucket": "platform-eng-history", "mode": "write"}]
    assert the(resolve(cat, a), "E_EPISODIC_WRITE_DENIED")
    a["spec"]["memory"] = [{"bucket": "enterprise-kb", "mode": "write"}]
    assert the(resolve(cat, a), "E_BUCKET_ACCESS_DENIED")["blocked_by"] == "enterprise"


def test_d12_memory_expands_into_synthetic_tools(cat):
    doc = resolve(cat, agent(cat, "pr-reviewer"))
    mem = [t for t in doc["provenance"]["tools"] if t["value"]["connector"] == "memory"]
    assert len(mem) == 1 and mem[0]["value"]["synthetic"] is True
    assert mem[0]["value"]["actions"] == ["read", "search"]
    assert perm(doc, "memory:read")["resources"] == \
        {"bucket": ["platform-eng-notes", "platform-eng-runbooks"]}
    assert "memory:write" not in perms(doc)


def test_d12_memory_needs_the_memory_connector_granted(cat):
    del team(cat)["connectors"]["memory"]
    assert the(resolve(cat, agent(cat, "pr-reviewer")), "E_CONNECTOR_NOT_GRANTED")["blocked_by"] == \
        "team:platform-eng"


def test_d14_notify_targets_must_be_inside_the_teams_channel_scope(cat):
    a = agent(cat, "pr-reviewer")
    a["spec"]["failure"]["on_failure"]["notify"] = ["slack:#general"]
    r = the(resolve(cat, a), "E_RESOURCE_OUT_OF_SCOPE")
    assert r["field"] == "spec.failure.on_failure.notify[0]" and r["blocked_by"] == "team:platform-eng"


def test_d24_sub_agent_ranges_pin_to_an_exact_version(cat):
    newer = agent(cat, "secret-scanner")
    newer["metadata"]["version"] = "1.4.2"
    cat.add_agent(newer)
    two = agent(cat, "secret-scanner")
    two["metadata"]["version"] = "2.0.0"
    cat.add_agent(two)
    doc = resolve(cat, agent(cat, "pr-reviewer"))           # declares secret-scanner@^1
    assert doc["effective"]["sub_agents"] == [{"name": "secret-scanner", "version": "1.4.2"}]


def test_sub_agent_must_be_callable(cat):
    """§8.4: own team, or enterprise `service`. run_only meta-agents are not callable (D11)."""
    a = agent(cat, "pr-reviewer")
    a["spec"]["sub_agents"] = [{"ref": "evaluator"}]
    assert the(resolve(cat, a), "E_SUBAGENT_NOT_CALLABLE")["blocked_by"] == "enterprise"
    a["spec"]["sub_agents"] = [{"ref": "people-ops-bot"}]
    assert the(resolve(cat, a), "E_SUBAGENT_NOT_CALLABLE")["blocked_by"] == "team:people-ops"


def test_sub_agent_cycles_are_rejected(cat):
    cr = agent(cat, "code-reviewer")
    cr["spec"]["sub_agents"] = [{"ref": "pr-reviewer"}]
    cat.add_agent(cr)
    a = agent(cat, "pr-reviewer")
    a["spec"]["sub_agents"] = [{"ref": "code-reviewer"}]
    assert the(resolve(cat, a), "E_SUBAGENT_CYCLE")


def test_platform_connector_is_enterprise_only(cat):
    a = agent(cat, "pr-reviewer")
    a["spec"]["tools"].append({"connector": "platform", "actions": ["get_runs"]})
    assert the(resolve(cat, a), "E_PLATFORM_CONNECTOR_DENIED")
    team(cat)["connectors"]["platform"] = {"allow": ["*"]}
    assert the(resolve(cat, agent(cat, "pr-reviewer")), "E_PLATFORM_CONNECTOR_DENIED")


def test_fork_must_come_from_own_team_and_name_an_exact_version(cat):
    a = agent(cat, "pr-reviewer")
    a["metadata"]["forked_from"] = "people-ops-bot@1.0.0"
    assert the(resolve(cat, a), "E_FORK_OUTSIDE_TEAM")
    a["metadata"]["forked_from"] = "code-reviewer@^1"
    assert the(resolve(cat, a), "E_SCHEMA_INVALID")


def test_auth_mode_must_exist_on_the_connector(cat):
    a = agent(cat, "evaluator")
    a["spec"]["tools"][0]["auth"] = "service"         # platform has on_behalf_of only
    assert the(resolve(cat, a), "E_AUTH_MODE_UNSUPPORTED")


# =============================================================================
# The two views must agree (FROZEN D3)
# =============================================================================

@pytest.mark.parametrize("name", sorted(_BASE.agents))
def test_provenance_and_effective_agree(cat, name):
    doc = resolve(cat, agent(cat, name), caller="frank")
    prov = {(t["value"]["connector"], a) for t in doc["provenance"]["tools"]
            for a in t["value"]["actions"]}
    eff = {(t["connector"], t["action"]) for t in doc["effective"]["tool_permissions"]}
    assert prov == eff
    assert doc["effective"]["prompt"] == "\n\n".join(p["value"] for p in doc["provenance"]["prompt"])
    assert doc["effective"]["model"]["id"] == doc["provenance"]["model"]["id"]["value"]
    flat = {**doc["provenance"]["limits"], **doc["provenance"]["budget"]}
    for k, v in doc["effective"]["limits"].items():
        if k in flat:
            assert flat[k]["value"] == v
    assert sorted({d["value"] for d in doc["provenance"]["guardrails"].get("deny", [])}) == \
        doc["effective"]["denylist"]


def test_demo_step_4_errors_name_the_layers_the_script_says(cat):
    """The two on-screen refusals, exactly as the demo will show them."""
    a = agent(cat, "pr-reviewer")
    a["spec"]["budget"]["timeout_seconds"] = 2300
    r = the(resolve(cat, a), "E_LIMIT_ABOVE_PARENT")
    assert r["message"].startswith("spec.budget.timeout_seconds is 2300, above the limit of 900 "
                                   "set by enterprise.")
    a = agent(cat, "pr-reviewer")
    a["spec"]["tools"][0]["actions"].append("pulls.merge")
    first = resolve(cat, a)["rejections"][0]
    assert first["code"] == "E_ACTION_NOT_GRANTED" and first["blocked_by"] == "team:platform-eng"
    assert first["message"].startswith("github:pulls.merge is not granted to this team.")
