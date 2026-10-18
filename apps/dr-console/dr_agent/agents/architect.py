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


def options_for(profile: AppProfile) -> list[StrategyOption]:
    return [StrategyOption(name=n, expected_rto_min=rto, expected_rpo_min=rpo, relative_cost=cost,
                           meets_targets=rto <= profile.rto_min and rpo <= profile.rpo_min, note=note)
            for n, rto, rpo, cost, note in STRATEGY_GUIDE]


def fallback(profile: AppProfile) -> StrategyRec:
    opts = options_for(profile)
    fitting = [o for o in opts if o.meets_targets]
    best = min(fitting, key=lambda o: o.relative_cost) if fitting else min(opts, key=lambda o: o.expected_rto_min)
    why = (f"{best.name} is the cheapest strategy meeting RTO {profile.rto_min} min and RPO {profile.rpo_min} min "
           f"(expected RTO ~{best.expected_rto_min} min, RPO ~{best.expected_rpo_min} min, ~{best.relative_cost}x single-region cost)."
           if fitting else f"No strategy meets RTO {profile.rto_min} / RPO {profile.rpo_min} min; {best.name} is the closest.")
    return StrategyRec(recommended=best.name, options=opts, rationale=why,
                       design_mapping=DESIGN_MAPPING.get(best.name, [f"Implement {best.name} across East Asia and Southeast Asia."]))


def run(profile: AppProfile, inventory_summary: dict) -> AgentResult[StrategyRec]:
    payload = {"profile": profile.model_dump(), "inventory": inventory_summary,
               "cost_guide": [dict(zip(("name", "rto_min", "rpo_min", "relative_cost", "note"), s)) for s in STRATEGY_GUIDE]}

    facts = {o.name: o for o in options_for(profile)}
    from dr_agent import trace
    trace.emit("input", "Strategy cost guide", "5 DR strategies with RTO, RPO and relative cost (the numbers the answer is checked against)",
               data=payload["cost_guide"])
    expected = fallback(profile).recommended  # cheapest strategy whose numbers really meet RTO and RPO

    def validate(rec: StrategyRec) -> None:
        if {o.name for o in rec.options} != set(facts):
            raise ValueError("architect must evaluate all five strategies")
        if rec.recommended != expected:
            raise ValueError(f"recommended {rec.recommended!r} but the cheapest strategy meeting RTO {profile.rto_min} min / "
                             f"RPO {profile.rpo_min} min is {expected!r}")
        # numbers and target checks are facts from the guide, never the model's estimate; keep the model's notes
        rec.options = [facts[o.name].model_copy(update={"note": o.note or facts[o.name].note}) for o in rec.options]

    return run_agent("architect", prompt("architect"), payload, StrategyRec, lambda: fallback(profile), validate=validate)
