"""Read-only investigation tools the Foundry agents may call themselves (function calling).

Every tool only READS Azure state. Writes stay in the engine, behind the allowlist and human approvals, so letting
an agent choose what to look at adds investigation, not risk. Each call is shown in the live activity trace.
"""
from __future__ import annotations

import json
from typing import Any, Callable

REGION = {"type": "string", "enum": ["A", "B"], "description": "A = East Asia (primary), B = Southeast Asia (DR)"}


def _obj(props: dict) -> dict:
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}


def _probe(target: str) -> dict:
    from dr_agent.engine.runner import _probe as probe
    r = probe(target)
    return {k: r.get(k) for k in ("ok", "status_code", "region", "db_ok", "db_server", "latency_ms", "reason", "error",
                                  "routed_to", "dns_region") if r.get(k) is not None}


def _traffic_manager() -> dict:
    from dr_agent.tools.azure_trafficmanager import get_profile_status
    st = get_profile_status()
    return {"endpoints": [{"name": e.get("name"), "enabled": e.get("endpoint_status"), "health": e.get("endpoint_monitor_status"),
                           "priority": e.get("priority")} for e in st.get("endpoints") or []],
            "ttl_s": (st.get("dns_config") or {}).get("ttl")}


def _web_app(region: str) -> dict:
    from dr_agent.config import config
    from dr_agent.tools.azure_webapp import get_webapp_state
    st = get_webapp_state(config.webapp_a_name if region == "A" else config.webapp_b_name)
    return {k: st.get(k) for k in ("name", "state", "location", "availability_state", "enabled")}


def _sql_failover_group() -> dict:
    from dr_agent.engine.runner import _fog
    return _fog().status()


def _app_settings_diff() -> dict:
    from dr_agent.config import config
    from dr_agent.tools.azure_webapp import list_app_settings
    a, b = set(list_app_settings(config.webapp_a_name)), set(list_app_settings(config.webapp_b_name))
    return {"only_in_A": sorted(a - b), "only_in_B": sorted(b - a), "in_both": len(a & b)}  # names only, never values


def _site_recovery() -> dict:
    from dr_agent.tools.azure_asr import AsrTestFailover
    st = AsrTestFailover.from_env().status()
    return {k: st.get(k) for k in ("protected", "state", "health", "rpo_s", "test_failover")}


# name -> (description, JSON schema for arguments, implementation)
TOOLS: dict[str, tuple[str, dict, Callable[..., Any]]] = {
    "probe": ("Health-check a region's payments app (HTTP status, which region answered, database write OK), or "
              "'tm' to check what a customer gets through Traffic Manager.",
              _obj({"target": {"type": "string", "enum": ["A", "B", "tm"]}}), _probe),
    "traffic_manager": ("Traffic Manager endpoints: enabled/disabled and health-check status per region.", _obj({}), _traffic_manager),
    "web_app": ("State of a region's App Service web app (Running/Stopped).", _obj({"region": REGION}), _web_app),
    "sql_failover_group": ("Azure SQL failover group: which region is primary and the replication state.", _obj({}), _sql_failover_group),
    "app_settings_diff": ("Names of app settings that exist in one region but not the other (configuration drift).",
                          _obj({}), _app_settings_diff),
    "site_recovery": ("Azure Site Recovery protection and replication health of the reports VM.", _obj({}), _site_recovery),
}

# which agents may call which tools
TOOLSETS: dict[str, list[str]] = {
    "validator": ["probe", "traffic_manager", "web_app", "sql_failover_group", "app_settings_diff"],
    "readiness": ["probe", "traffic_manager", "sql_failover_group", "app_settings_diff", "site_recovery"],
}
MAX_TOOL_CALLS = 6


def definitions(agent: str) -> list[dict]:
    """Foundry function-tool definitions for this agent (empty if it has no tools)."""
    return [{"type": "function", "name": n, "description": TOOLS[n][0], "parameters": TOOLS[n][1], "strict": True}
            for n in TOOLSETS.get(agent, [])]


def call(agent: str, name: str, arguments: str) -> str:
    """Execute one tool call requested by an agent; only tools in that agent's set are allowed."""
    if name not in TOOLSETS.get(agent, []):
        return json.dumps({"error": f"tool {name!r} is not available to the {agent} agent"})
    try:
        args = json.loads(arguments or "{}")
        result = TOOLS[name][2](**args)
    except Exception as e:  # noqa: BLE001 - the agent sees the error and can try something else
        result = {"error": f"{type(e).__name__}: {e}"[:300]}
    return json.dumps(result, default=str)[:4000]
