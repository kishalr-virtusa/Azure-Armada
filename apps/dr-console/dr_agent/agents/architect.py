"""DR Architect agent: recommends a DR strategy from tier, RTO, RPO and budget (requirements R3.2)."""
from __future__ import annotations

from dr_agent.agents import prompt
from dr_agent.llm import AgentResult, run_agent
from dr_agent.models import AppProfile, StrategyOption, StrategyRec

# Typical Azure characteristics per strategy (fallback + baseline given to the model).
# (name, expected_rto_min, expected_rpo_min, relative_cost, short note)
STRATEGY_GUIDE = [
    ("backup-restore", 480, 1440, 1.1, "Geo-redundant backups restored into a new region; cheapest, slowest."),
    ("pilot-light", 120, 15, 1.3, "Data replicated continuously; compute provisioned only at failover time."),
    ("warm-standby", 15, 1, 1.6, "Scaled-down but running copy in the DR region; data replicated via failover group."),
    ("active-passive", 5, 1, 1.9, "Full-size hot standby; traffic switches by DNS/priority routing."),
    ("active-active", 1, 0, 2.2, "Both regions serve traffic; needs multi-write data design."),
]

DESIGN_MAPPING = {
    "warm-standby": [
        "Region B App Service (B1) kept running as a warm standby behind Traffic Manager priority routing.",
        "Azure SQL failover group replicates paymentsdb from East Asia to Southeast Asia continuously.",
        "Failover = SQL failover group switch + Traffic Manager endpoint switch, orchestrated by the DR Agent.",
        "Failback = planned (no data loss) SQL failover back to Region A + endpoint re-enable.",
    ],
}


def options_for(profile: AppProfile, region_usd: float = 0.0) -> list[StrategyOption]:
    """region_usd: monthly cost of one region at list prices; 0 = unknown (budget not evaluated)."""
    out = []
    for n, rto, rpo, cost, note in STRATEGY_GUIDE:
        usd = round(region_usd * cost, 2)
        out.append(StrategyOption(name=n, expected_rto_min=rto, expected_rpo_min=rpo, relative_cost=cost,
                                  meets_targets=rto <= profile.rto_min and rpo <= profile.rpo_min, note=note,
                                  est_monthly_usd=usd, within_budget=not region_usd or usd <= profile.monthly_budget_usd))
    return out


def fallback(profile: AppProfile, region_usd: float = 0.0) -> StrategyRec:
    opts = options_for(profile, region_usd)
    fitting = [o for o in opts if o.meets_targets]
    best = min(fitting, key=lambda o: o.relative_cost) if fitting else min(opts, key=lambda o: o.expected_rto_min)
    why = (f"{best.name} is the cheapest strategy meeting RTO {profile.rto_min} min and RPO {profile.rpo_min} min "
           f"(expected RTO ~{best.expected_rto_min} min, RPO ~{best.expected_rpo_min} min, ~{best.relative_cost}x single-region cost)."
           if fitting else f"No strategy meets RTO {profile.rto_min} / RPO {profile.rpo_min} min; {best.name} is the closest.")
    return StrategyRec(recommended=best.name, options=opts, rationale=why,
                       design_mapping=DESIGN_MAPPING.get(best.name, [f"Implement {best.name} across East Asia and Southeast Asia."]))


def run(profile: AppProfile, inventory_summary: dict) -> AgentResult[StrategyRec]:
    from dr_agent import trace
    from dr_agent.tools.pricing import region_monthly_usd
    price = trace.step("tool", "Azure Retail Prices API: monthly cost of one region (our SKUs)", region_monthly_usd,
                       summary=lambda p: " + ".join(f"{c['name']} ${c['monthly_usd']:.2f}" for c in p["components"])
                       + f" = ${p['total_usd']:.2f}/month" + (" (fallback prices)" if p["source"] == "fallback" else ""))
    region_usd = price["total_usd"]
    opts = options_for(profile, region_usd)
    facts = {o.name: o for o in opts}
    # everything numeric is computed here, so the agent compares facts instead of guessing units
    cost_guide = [{"name": o.name, "rto_min": o.expected_rto_min, "rpo_min": o.expected_rpo_min,
                   "relative_cost": o.relative_cost, "est_monthly_usd": o.est_monthly_usd,
                   "meets_rto": o.expected_rto_min <= profile.rto_min, "meets_rpo": o.expected_rpo_min <= profile.rpo_min,
                   "within_budget": o.within_budget, "note": o.note} for o in opts]
    payload = {"profile": profile.model_dump(), "inventory": inventory_summary,
               "single_region_monthly_usd": region_usd, "cost_guide": cost_guide}
    trace.emit("input", "Strategy cost guide in dollars",
               f"5 strategies: RTO, RPO and estimated monthly cost (multiplier x ${region_usd:.2f}), checked against the "
               f"${profile.monthly_budget_usd}/month budget", data=cost_guide)
    expected = fallback(profile, region_usd).recommended  # cheapest strategy whose numbers really meet RTO and RPO

    def validate(rec: StrategyRec) -> None:
        if {o.name for o in rec.options} != set(facts):
            raise ValueError("architect must evaluate all five strategies")
        if rec.recommended != expected:
            raise ValueError(f"recommended {rec.recommended!r} but the cheapest strategy meeting RTO {profile.rto_min} min / "
                             f"RPO {profile.rpo_min} min is {expected!r}")
        # numbers and target checks are facts from the guide, never the model's estimate; keep the model's notes
        rec.options = [facts[o.name].model_copy(update={"note": o.note or facts[o.name].note}) for o in rec.options]

    return run_agent("architect", prompt("architect"), payload, StrategyRec, lambda: fallback(profile, region_usd),
                     validate=validate)
