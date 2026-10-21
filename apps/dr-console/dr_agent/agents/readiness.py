"""Readiness agent: deterministic pre-exercise checks + LLM go/no-go summary (requirements R3.3).

Checks are produced by `build_checks` from raw facts gathered by tools (so the logic is testable);
the LLM only summarises them. A "fail" always forces go=false, whatever the model says.
"""
from __future__ import annotations

import dataclasses
from typing import Any, Optional

from dr_agent.agents import prompt
from dr_agent.llm import AgentResult, run_agent
from dr_agent.models import Check, ReadinessReport, ReadinessSummary

PARITY_IGNORE = {"REGION_NAME", "APPLICATIONINSIGHTS_CONNECTION_STRING", "KV_REFRESH"}
PARITY_IGNORE_PREFIXES = ("WEBSITE_", "WEBSITES_", "SCM_", "DIAGNOSTICS_", "APPSETTING_")
SECRET_HINTS = ("PASSWORD", "SECRET", "KEY", "CONNECTION", "TOKEN")


def _mask(k: str, v: Optional[str]) -> Optional[str]:
    if v is None:
        return None
    return "***" if any(h in k.upper() for h in SECRET_HINTS) and not v.startswith("@Microsoft.KeyVault") else v


def parity_check(settings_a: dict[str, str], settings_b: dict[str, str], ignore: set[str] = PARITY_IGNORE) -> Check:
    """Compare app settings between regions. Missing keys = fail; differing non-secret values = warn."""
    keys = {k for k in (set(settings_a) | set(settings_b)) - ignore if not k.upper().startswith(PARITY_IGNORE_PREFIXES)}
    missing_in_b = sorted(k for k in keys if k in settings_a and k not in settings_b)
    missing_in_a = sorted(k for k in keys if k in settings_b and k not in settings_a)
    differs = sorted(k for k in keys if k in settings_a and k in settings_b and settings_a[k] != settings_b[k]
                     and not any(h in k.upper() for h in SECRET_HINTS))
    evidence: dict[str, Any] = {
        "missing_in_region_b": missing_in_b, "missing_in_region_a": missing_in_a,
        "different_values": {k: {"A": _mask(k, settings_a[k]), "B": _mask(k, settings_b[k])} for k in differs},
        "compared_keys": len(keys),
    }
    if missing_in_b or missing_in_a:
        detail = "; ".join(filter(None, [
            f"missing in Region B: {', '.join(missing_in_b)}" if missing_in_b else "",
            f"missing in Region A: {', '.join(missing_in_a)}" if missing_in_a else ""]))
        return Check(name="App settings parity (A vs B)", status="fail",
                     detail=f"Configuration drift: {detail}. The app may fail after failover.", evidence=evidence)
    if differs:
        return Check(name="App settings parity (A vs B)", status="warn",
                     detail=f"Values differ for: {', '.join(differs)}", evidence=evidence)
    return Check(name="App settings parity (A vs B)", status="pass",
                 detail=f"{len(keys)} settings identical across regions", evidence=evidence)


def fog_check(fog_status: dict, lag_s: Optional[int]) -> Check:
    primary = fog_status.get("primary_region")
    states = {r: s.get("state") for r, s in fog_status.get("servers", {}).items()}
    ev = {"primary_region": primary, "replication_state": states, "replication_lag_s": lag_s}
    if primary != "A":
        return Check(name="SQL failover group", status="fail",
                     detail=f"Region A must be primary before the exercise (current: {primary}).", evidence=ev)
    if lag_s is not None and lag_s > 60:
        return Check(name="SQL failover group", status="warn", detail=f"Replication lag {lag_s}s is high.", evidence=ev)
    return Check(name="SQL failover group", status="pass",
                 detail=f"Region A primary, replicating to Region B (lag {lag_s if lag_s is not None else 'n/a'}s).", evidence=ev)


def probe_check(name: str, probe: dict, expect_region: str) -> Check:
    region_ok = probe.get("region_key", expect_region) == expect_region
    ok = bool(probe.get("ok")) and probe.get("db_ok") is not False and region_ok
    return Check(name=name, status="pass" if ok else "fail",
                 detail="healthy" if ok else f"unhealthy: {probe.get('error') or probe}"
                 + (f" ({probe['reason']})" if probe.get("reason") else ""), evidence=probe)


ENDPOINT_REGION = {"endpoint-a": "East Asia (A)", "endpoint-b": "Southeast Asia (B)"}


def _endpoint_problem(e: dict) -> str:
    where = f"{ENDPOINT_REGION.get(e.get('name'), e.get('name'))} endpoint"
    if not e.get("enabled"):
        return f"{where} is disabled, so Traffic Manager will not send users there"
    return f"{where} is {e.get('monitor_status')}: failing Traffic Manager health checks"


def tm_check(endpoints: list[dict]) -> Check:
    """endpoints: [{name, region, enabled: bool, monitor_status: str}]"""
    bad = [e for e in endpoints if not e.get("enabled") or e.get("monitor_status") not in ("Online", "CheckingEndpoint")]
    if not endpoints:
        return Check(name="Traffic Manager endpoints", status="fail", detail="no endpoints found", evidence={})
    if bad:
        return Check(name="Traffic Manager endpoints", status="fail",
                     detail="; ".join(_endpoint_problem(e) for e in bad),
                     evidence={"endpoints": endpoints})
    return Check(name="Traffic Manager endpoints", status="pass", detail="both endpoints enabled and Online",
                 evidence={"endpoints": endpoints})


def asr_check(st: dict) -> Check:
    """IaaS tier: the VM must be protected by Azure Site Recovery with healthy replication (warn, not block)."""
    if st.get("protected") and st.get("health") == "Normal":
        rpo = st.get("rpo_s")
        return Check(name="Azure Site Recovery (reports VM)", status="pass",
                     detail=f"Protected, replication healthy{f', RPO {rpo}s' if rpo is not None else ''}", evidence=st)
    pct = st.get("initial_replication_pct")
    detail = f"{st.get('state') or 'not protected'}" + (f" (initial replication {pct}%)" if pct is not None else "")
    return Check(name="Azure Site Recovery (reports VM)", status="warn",
                 detail=f"{detail}; the IaaS test failover will be skipped", evidence=st)


def build_checks(facts: dict) -> list[Check]:
    """facts keys (all optional): fog_status, lag_s, probe_a, probe_b, tm_endpoints, settings_a, settings_b, keyvault_ok."""
    checks: list[Check] = []
    if "fog_status" in facts:
        checks.append(fog_check(facts["fog_status"], facts.get("lag_s")))
    if "probe_a" in facts:
        checks.append(probe_check("Region A app health (direct)", facts["probe_a"], "A"))
    if "probe_b" in facts:
        checks.append(probe_check("Region B app health (direct)", facts["probe_b"], "B"))
    if "tm_endpoints" in facts:
        checks.append(tm_check(facts["tm_endpoints"]))
    if "settings_a" in facts and "settings_b" in facts:
        checks.append(parity_check(facts["settings_a"], facts["settings_b"]))
    if "asr" in facts:
        checks.append(asr_check(facts["asr"]))
    if "keyvault_ok" in facts:
        kv = facts.get("keyvault") or {}
        checks.append(Check(name="Key Vault reachable", status="pass" if facts["keyvault_ok"] else "fail",
                            detail=(f"secret read from {kv.get('vault', 'Key Vault')} with the managed identity"
                                    if facts["keyvault_ok"] else f"Key Vault not reachable: {kv.get('error', 'unknown')}"),
                            evidence={k: v for k, v in kv.items() if k != "error"}))
    return checks


def fallback_summary(checks: list[Check]) -> ReadinessSummary:
    fails = [c for c in checks if c.status == "fail"]
    warns = [c for c in checks if c.status == "warn"]
    if fails:
        s = f"NO-GO: {len(fails)} blocking issue(s) found before the DR exercise: {fails[0].name}: {fails[0].detail}"
    else:
        s = f"GO: all {len(checks)} readiness checks passed" + (f" with {len(warns)} warning(s)." if warns else ".")
    return ReadinessSummary(go=not fails, summary=s, risks=[f"{c.name}: {c.detail}" for c in fails + warns])


def run(checks: list[Check]) -> AgentResult[ReadinessReport]:
    payload = {"checks": [c.model_dump(mode="json") for c in checks]}
    res = run_agent("readiness", prompt("readiness"), payload, ReadinessSummary, lambda: fallback_summary(checks),
                    require_tool=any(c.status != "pass" for c in checks))
    has_fail = any(c.status == "fail" for c in checks)
    report = ReadinessReport(checks=checks, go=res.output.go and not has_fail, summary=res.output.summary,
                             risks=res.output.risks, source=res.source)
    # every field of the agent call (agent_version, model, usage, evidence, blocked...) travels with the report
    return dataclasses.replace(res, output=report)
