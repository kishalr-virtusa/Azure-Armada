"""Builds the exercise report (requirements R8): measured facts + Reporter agent narrative -> HTML in Blob."""
from __future__ import annotations

import json
from datetime import datetime, timezone

from jinja2 import Environment, select_autoescape

from dr_agent.engine.measurements import fmt_duration
from dr_agent.models import AppProfile
from dr_agent.tools import store

TEMPLATE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>DR Exercise Report {{ xid }}</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Montserrat:wght@300;400;500;600;700&display=swap">
<style>
:root{--bg:#F7F7F9;--surface:#FFFFFF;--surface-2:#F2F2F4;--ink:#161B28;--ink-2:#4A4F5E;--ink-3:#7C8190;--line:#E3E3E8;--line-2:#D0D0D8;--accent:#5E2CFE;--mag:#C105B2;
--have:#0A7A48;--have-bg:#D8F2E4;--upd:#9A6400;--upd-bg:#FDF0D5;--add:#C0392B;--add-bg:#FBE3E0}
*{box-sizing:border-box}body{background:var(--bg);color:var(--ink);font-family:"Montserrat",system-ui,"Segoe UI",sans-serif;font-size:14px;line-height:1.5;margin:0}
.wrap{max-width:1100px;margin:0 auto;padding:28px 24px 64px;display:flex;flex-direction:column;gap:20px}
.eyebrow{font-size:11px;font-weight:600;letter-spacing:.14em;text-transform:uppercase;color:var(--accent)}
h1{font-weight:300;font-size:34px;margin:6px 0}h2{font-size:18px;font-weight:600;margin:0 0 10px}
.card{background:var(--surface);border:1px solid var(--line);border-radius:10px;padding:18px}
.kpis{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px}@media(max-width:800px){.kpis{grid-template-columns:repeat(2,1fr)}}
.kpi{background:var(--surface);border:1px solid var(--line);border-radius:10px;padding:14px 16px;border-top:3px solid var(--accent)}
.kpi.ok{border-top-color:var(--have)}.kpi.bad{border-top-color:var(--add)}
.kpi .l{font-size:12px;font-weight:600}.kpi .v{font-size:32px;font-weight:300;line-height:1.1}.kpi .x{font-size:11.5px;color:var(--ink-3)}
.st{display:inline-block;font-size:10px;font-weight:700;letter-spacing:.05em;text-transform:uppercase;border-radius:4px;padding:2px 7px}
.st.pass{background:var(--have-bg);color:var(--have)}.st.fail{background:var(--add-bg);color:var(--add)}.st.warn{background:var(--upd-bg);color:var(--upd)}
table{border-collapse:collapse;width:100%;font-size:12.5px}th{text-align:left;font-size:10px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;color:var(--ink-3);padding:6px;border-bottom:1px solid var(--line-2)}
td{padding:6px;border-bottom:1px solid var(--line);vertical-align:top;color:var(--ink-2)}ul{margin:0;padding-left:18px}.sub{color:var(--ink-3);font-size:12.5px}
</style></head><body><div class="wrap">
<header><div class="eyebrow">Disaster Recovery (DR) Agent · Autonomous Disaster Recovery Validation</div>
<h1>DR Exercise Report: {{ profile.name }}</h1>
<div class="sub">Exercise {{ xid }} · generated {{ generated }} · Region A East Asia → Region B Southeast Asia ·
<span class="st {{ 'pass' if overall else 'fail' }}">{{ 'PASSED' if overall else 'FAILED' }}</span></div></header>
<div class="kpis">
 <div class="kpi {{ 'ok' if rto_met else 'bad' }}"><div class="l">RTO (measured)</div><div class="v">{{ rto }}</div><div class="x">target {{ profile.rto_min }} min · {{ '✅ met' if rto_met else '❌ missed' }}</div></div>
 <div class="kpi {{ 'ok' if rpo_met else 'bad' }}"><div class="l">RPO (measured)</div><div class="v">{{ rpo }}</div><div class="x">target {{ profile.rpo_min }} min · {{ '✅ met' if rpo_met else '❌ missed' }}</div></div>
 <div class="kpi"><div class="l">Failback</div><div class="v">{{ failback }}</div><div class="x">back to East Asia</div></div>
 <div class="kpi"><div class="l">Strategy</div><div class="v" style="font-size:22px">{{ strategy }}</div><div class="x">tier {{ profile.tier }}</div></div>
</div>
<div class="card"><h2>Executive summary</h2><p style="margin:0">{{ n.executive_summary }}</p></div>
{% if segs %}<div class="card"><h2>Where the {{ rto }} went</h2>
<p style="margin:0 0 8px;color:#666">From the engine's own step timestamps (one clock); segments add up to the RTO.{% if precision %} Recovery proven by a health check through Traffic Manager; the previous check {{ precision }} s earlier still failed.{% endif %}</p>
<table><tr><th>Segment</th><th style="text-align:right">Seconds</th></tr>{% for s in segs %}<tr><td>{{ s.label }}</td><td style="text-align:right">{{ '%.1f' % s.seconds }}</td></tr>{% endfor %}
<tr><td><b>Total (RTO)</b></td><td style="text-align:right"><b>{{ '%.1f' % segs_total }}</b></td></tr></table></div>{% endif %}
<div class="card" style="display:grid;grid-template-columns:1fr 1fr;gap:20px">
 <div><h2>What went well</h2><ul>{% for x in n.what_went_well %}<li>{{ x }}</li>{% else %}<li>-</li>{% endfor %}</ul></div>
 <div><h2>Issues</h2><ul>{% for x in n.issues %}<li>{{ x }}</li>{% else %}<li>None observed</li>{% endfor %}</ul></div>
</div>
<div class="card"><h2>Remediation</h2><table><tr><th>Priority</th><th>Item</th><th>Owner</th></tr>
{% for r in n.remediation %}<tr><td><span class="st {{ 'fail' if r.priority=='high' else 'warn' if r.priority=='medium' else 'pass' }}">{{ r.priority }}</span></td><td>{{ r.item }}</td><td>{{ r.owner }}</td></tr>
{% else %}<tr><td colspan="3">No open items</td></tr>{% endfor %}</table></div>
<div class="card"><h2>Evidence</h2><table>
<tr><td>Outage injected (t0)</td><td>{{ m.t0 }}</td></tr><tr><td>Forced DB failover started</td><td>{{ m.t_failover }}</td></tr>
<tr><td>Recovered via Traffic Manager (Southeast Asia, DB OK)</td><td>{{ m.t_recovered }}</td></tr>
<tr><td>Failback completed</td><td>{{ m.t_failback_done }}</td></tr>
<tr><td>Last acknowledged heartbeat before failover</td><td>seq {{ ev.last_acked_seq }} at {{ ev.last_acked_ts }}</td></tr>
<tr><td>Newest heartbeat present in new primary</td><td>{{ ev.newest_present_ts }} · lost writes: {{ ev.lost_writes }}</td></tr>
</table></div>
<div class="card"><h2>Timeline</h2><table><tr><th>Time (UTC)</th><th>Step</th><th>Event</th></tr>
{% for e in events %}<tr><td>{{ e.ts[11:19] if e.ts else '' }}</td><td>{{ e.step_id or '' }}</td><td>{{ e.message }}</td></tr>{% endfor %}</table></div>
<div class="sub">Approvals: {{ approvals }} · RTO/RPO are computed from recorded timestamps by the engine; the AI agents only write the narrative.{% if grounding %} {{ grounding }}{% endif %}</div>
</div></body></html>"""


def grounding_note(g: dict) -> str:
    """One line for the report footer: how much of the narrative code verified against the measurements."""
    if not g or not g.get("checked"):
        return ""
    if g.get("verified"):
        return f"Narrative verified against the measurements: {g['checked']}/{g['checked']} figures and verdicts match."
    return (f"Narrative check: {g['supported']}/{g['checked']} figures and verdicts match the measurements; "
            f"not supported: {'; '.join(g.get('unsupported') or [])}.")


ASR_TERMS = ("asr", "site recovery", "test failover", "replication of the reports vm")


def build_report(xid: str) -> str:
    from dr_agent.agents import reporter
    m = store.get_exercise(xid) or {}
    prof = AppProfile.model_validate_json(m["profile_json"]) if m.get("profile_json") else AppProfile()
    events = store.get_events(xid)
    ev = json.loads(m.get("rpo_evidence_json") or "{}")
    num = lambda k: float(m[k]) if m.get(k) not in (None, "") else None  # noqa: E731
    rto, rpo, fb = num("rto_s"), num("rpo_s"), num("failback_s")
    rto_met = str(m.get("rto_met")).lower() == "true"
    rpo_met = str(m.get("rpo_met")).lower() == "true"
    strategy = json.loads(m["strategy_json"]).get("recommended") if m.get("strategy_json") else "warm-standby"
    readiness = json.loads(m["readiness_json"]) if m.get("readiness_json") else {}
    runbook = json.loads(m["runbook_json"]) if m.get("runbook_json") else {}
    open_findings = list(readiness.get("risks", [])) + [f"Runbook step {s['step_no']}: {s['reason']}" for s in runbook.get("steps", [])
                                                        if s.get("verdict") in ("stale", "missing")]
    from dr_agent.engine.measurements import rto_breakdown
    def _p(v):
        try:
            d = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
            return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            return None
    segs = rto_breakdown(_p(m.get("t0")), _p(m.get("t_recovered")), events) if rto else []
    precision = json.loads(m.get("rto_evidence_json") or "{}").get("precision_s")
    iaas_s = num("iaas_recovered_s")
    iaas = {"tier": "reports VM (Azure Site Recovery test failover, isolated network)",
            "recovered_in": fmt_duration(iaas_s) if iaas_s is not None else None,
            "result": json.loads(m["iaas_result_json"]) if m.get("iaas_result_json") else None,
            "status": "validated" if iaas_s is not None else ("not run" if not m.get("iaas_started") else "failed")}
    # A measured result supersedes a readiness warning: once the ASR test failover was proven in this drill,
    # earlier Site Recovery warnings are no longer open findings (neither as input nor in the agent's output).
    asr_proven = iaas["status"] == "validated"
    superseded = lambda text: asr_proven and any(k in text.lower() for k in ASR_TERMS)  # noqa: E731
    open_findings = [f for f in open_findings if not superseded(f)]
    # memory across drills: save this drill's RTO breakdown, compare with earlier drills, find recurring findings
    seg_sum: dict[str, float] = {}
    for sg in segs:
        seg_sum[sg["label"]] = round(seg_sum.get(sg["label"], 0.0) + sg["seconds"], 3)
    if seg_sum:
        store.upsert_exercise(xid, {"rto_breakdown_json": json.dumps(seg_sum)})
    from dr_agent.engine.history import drill_history, recurring
    hist = drill_history(xid)
    facts_history = {k: hist.get(k) for k in ("previous_drills", "median_rto_s", "rto_vs_median_pct", "biggest_segment_change")}
    facts_history["recurring_findings"] = recurring(open_findings, hist)
    facts = {
        "profile": prof.model_dump(), "strategy": strategy, "iaas": iaas, "history": facts_history,
        "rto_breakdown_s": [(s["label"], s["seconds"]) for s in segs], "rto_precision_s": precision,
        "results": {"rto": fmt_duration(rto), "rto_target": f"{prof.rto_min} min", "rto_met": rto_met,
                    "rpo": fmt_duration(rpo), "rpo_target": f"{prof.rpo_min} min", "rpo_met": rpo_met,
                    "failback": fmt_duration(fb) if fb is not None else None},
        "open_findings": open_findings,
        "errors": [e["message"] for e in events if e.get("kind") in ("error", "diagnosis")],
        "timeline": [f"{e.get('ts', '')[11:19]} {e.get('step_id') or ''} {e.get('message')}" for e in events][-60:],
    }
    res = reporter.run(facts)
    narrative = res.output
    narrative.issues = [x for x in narrative.issues if not superseded(x)]
    narrative.remediation = [r for r in narrative.remediation if not superseded(r.item)]
    env = Environment(autoescape=select_autoescape(["html"]))
    html = env.from_string(TEMPLATE).render(
        xid=xid, profile=prof, n=narrative, m=m, ev=ev, events=events, strategy=strategy,
        rto=fmt_duration(rto), rpo=fmt_duration(rpo), failback=fmt_duration(fb),
        rto_met=rto_met, rpo_met=rpo_met, overall=rto_met and rpo_met, iaas=iaas,
        segs=segs, segs_total=round(sum(s["seconds"] for s in segs), 3), precision=precision,
        generated=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        approvals=m.get("approvals_json", ""), grounding=grounding_note(res.grounding))
    url = store.upload_report(xid, html)
    store.upsert_exercise(xid, {"report_url": url, "remediation_json": json.dumps([r.model_dump() for r in narrative.remediation]),
                                "report_grounding_json": json.dumps(res.grounding),
                                "report_agent_version": res.agent_version, "report_via": res.via})
    return url
