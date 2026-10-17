"""Exercise-plan rules: tool allowlist, mandatory sequence, validation and the canonical default plan
(requirements R4, design §4.4). The engine only ever runs plans that pass `validate_plan`.
"""
from __future__ import annotations

from dr_agent.models import ExercisePlan, Param, PlanStep

PHASES = ["pre-checks", "outage", "failover", "validation", "failback", "closure"]

# action -> (required params, description shown to the Planner)
ALLOWED_ACTIONS: dict[str, tuple[set[str], str]] = {
    "snapshot_inventory": (set(), "Record the workload inventory + config (before/after diff)."),
    "check_fog": (set(), "Verify the SQL failover group is healthy and replicating."),
    "start_heartbeat": (set(), "Start the 2s heartbeat writer used to measure RPO."),
    "inject_outage": ({"region"}, "Simulate a regional outage: stop the web app in `region`. Records t0."),
    "sql_failover": ({"target_region", "allow_data_loss"}, "Make `target_region` the SQL primary. Forced if allow_data_loss=true. Records t_failover."),
    "tm_set_endpoint": ({"region", "enabled"}, "Enable/disable the Traffic Manager endpoint of `region`."),
    "wait_healthy": ({"target", "expect_region", "timeout_s"}, "Poll target (tm|A|B) until /health is OK from expect_region. On target=tm during failover records t_recovered."),
    "functional_txn_check": (set(), "POST a payment via Traffic Manager and read it back."),
    "measure_rpo": (set(), "Compute RPO from heartbeats vs the new primary."),
    "start_webapp": ({"region"}, "Start the web app in `region`."),
    "stop_heartbeat": (set(), "Stop the heartbeat writer."),
    "generate_report": (set(), "Build the exercise report and notify stakeholders."),
    "asr_test_failover": (set(), "IaaS: start an Azure Site Recovery TEST failover of the reports VM into the isolated Region B network (non-disruptive, runs in the background)."),
    "asr_verify_recovery": ({"timeout_s"}, "IaaS: wait for the ASR test failover, then run a command inside the recovered VM to prove the service answers from Region B."),
    "asr_test_cleanup": (set(), "IaaS: clean up the ASR test failover (deletes the test VM)."),
}

# (action, key params) that must appear, in this relative order
MANDATORY_SEQUENCE: list[tuple[str, dict[str, str]]] = [
    ("start_heartbeat", {}),
    ("inject_outage", {"region": "A"}),
    ("sql_failover", {"target_region": "B", "allow_data_loss": "true"}),
    ("tm_set_endpoint", {"region": "A", "enabled": "false"}),
    ("wait_healthy", {"target": "tm", "expect_region": "B"}),
    ("measure_rpo", {}),
    ("start_webapp", {"region": "A"}),
    ("sql_failover", {"target_region": "A", "allow_data_loss": "false"}),
    ("tm_set_endpoint", {"region": "A", "enabled": "true"}),
    ("wait_healthy", {"target": "tm", "expect_region": "A"}),
    ("stop_heartbeat", {}),
    ("generate_report", {}),
]


class PlanError(ValueError):
    def __init__(self, problems: list[str]):
        super().__init__("; ".join(problems))
        self.problems = problems


def _matches(step: PlanStep, action: str, params: dict[str, str]) -> bool:
    if step.action != action:
        return False
    p = {k: v.strip().lower() for k, v in step.param_dict().items()}
    return all(p.get(k) == v.lower() for k, v in params.items())


def validate_plan(plan: ExercisePlan) -> None:
    """Raise PlanError listing every problem; return None if the plan is safe to execute."""
    problems: list[str] = []
    ids = [s.id for s in plan.steps]
    if len(ids) != len(set(ids)):
        problems.append("duplicate step ids")
    seen: set[str] = set()
    last_phase = 0
    for s in plan.steps:
        for d in s.depends_on:
            if d not in seen:
                problems.append(f"{s.id}: depends_on {d!r} must reference an EARLIER step")
        seen.add(s.id)
        if PHASES.index(s.phase) < last_phase:
            problems.append(f"{s.id}: phase {s.phase!r} is out of order")
        last_phase = max(last_phase, PHASES.index(s.phase))
        if s.type == "automated":
            if s.action not in ALLOWED_ACTIONS:
                problems.append(f"{s.id}: action {s.action!r} is not in the allowlist")
            else:
                missing = ALLOWED_ACTIONS[s.action][0] - set(s.param_dict())
                if missing:
                    problems.append(f"{s.id}: action {s.action} missing params {sorted(missing)}")
        elif s.action:
            problems.append(f"{s.id}: human steps must not have an action")
    # mandatory actions present, in order
    pos = -1
    for action, params in MANDATORY_SEQUENCE:
        idx = next((i for i, s in enumerate(plan.steps) if i > pos and _matches(s, action, params)), None)
        if idx is None:
            problems.append(f"missing or out-of-order mandatory step: {action} {params}")
        else:
            pos = idx
    if not any(s.type == "human" for s in plan.steps):
        problems.append("plan must contain at least one human approval step")
    if problems:
        raise PlanError(problems)


def _step(id, phase, title, team, type_, action=None, params=None, deps=(), criteria="", abort=True) -> PlanStep:
    return PlanStep(id=id, phase=phase, title=title, owner_team=team, type=type_, action=action,
                    params=[Param(key=k, value=str(v).lower() if isinstance(v, bool) else str(v))
                            for k, v in (params or {}).items()],
                    depends_on=list(deps), success_criteria=criteria, abort_on_failure=abort)


def default_plan() -> ExercisePlan:
    """Canonical warm-standby exercise (design §4.4). Used as the Planner's fallback and example."""
    s = [
        _step("pre-1", "pre-checks", "Snapshot inventory & configuration", "dr-agent", "automated", "snapshot_inventory", criteria="Inventory captured"),
        _step("pre-2", "pre-checks", "Verify SQL failover group replication is healthy", "db", "automated", "check_fog", deps=["pre-1"], criteria="Region A primary, replication CATCH_UP/SEEDED"),
        _step("iaas-1", "pre-checks", "Start ASR test failover of the reports VM (isolated network)", "infra", "automated", "asr_test_failover", deps=["pre-2"], criteria="Test failover job started", abort=False),
        _step("pre-3", "pre-checks", "DB team confirms go for forced failover", "db", "human", deps=["pre-2"], criteria="Confirmed in console"),
        _step("pre-4", "pre-checks", "Start heartbeat writer (RPO measurement)", "dr-agent", "automated", "start_heartbeat", deps=["pre-3"], criteria="Heartbeats acknowledged"),
        _step("out-1", "outage", "Inject outage: stop Region A web app", "dr-agent", "automated", "inject_outage", {"region": "A"}, ["pre-4"], "Region A /health fails"),
        _step("fo-1", "failover", "Forced SQL failover to Region B", "db", "automated", "sql_failover", {"target_region": "B", "allow_data_loss": True}, ["out-1"], "Region B is SQL primary"),
        _step("fo-2", "failover", "Disable Region A Traffic Manager endpoint", "network", "automated", "tm_set_endpoint", {"region": "A", "enabled": False}, ["fo-1"], "Endpoint A disabled"),
        _step("val-1", "validation", "Wait until users are served from Region B", "app", "automated", "wait_healthy", {"target": "tm", "expect_region": "B", "timeout_s": 900}, ["fo-2"], "TM /health OK from Region B with db_ok"),
        _step("val-2", "validation", "Functional check: make a payment via Traffic Manager", "app", "automated", "functional_txn_check", deps=["val-1"], criteria="Payment written and read back"),
        _step("val-3", "validation", "Measure RPO from heartbeats", "dr-agent", "automated", "measure_rpo", deps=["val-2"], criteria="RPO computed"),
        _step("fb-1", "failback", "App team approves failback", "app", "human", deps=["val-3"], criteria="Confirmed in console"),
        _step("fb-2", "failback", "Start Region A web app", "app", "automated", "start_webapp", {"region": "A"}, ["fb-1"], "Region A running"),
        _step("fb-3", "failback", "Wait for Region A to be healthy (direct)", "app", "automated", "wait_healthy", {"target": "A", "expect_region": "A", "timeout_s": 600}, ["fb-2"], "Region A /health OK"),
        _step("fb-4", "failback", "Planned SQL failover back to Region A", "db", "automated", "sql_failover", {"target_region": "A", "allow_data_loss": False}, ["fb-3"], "Region A is SQL primary"),
        _step("fb-5", "failback", "Re-enable Region A Traffic Manager endpoint", "network", "automated", "tm_set_endpoint", {"region": "A", "enabled": True}, ["fb-4"], "Endpoint A enabled"),
        _step("fb-6", "failback", "Wait until users are served from Region A again", "app", "automated", "wait_healthy", {"target": "tm", "expect_region": "A", "timeout_s": 900}, ["fb-5"], "TM /health OK from Region A"),
        # IaaS verification runs last so a slow ASR test failover (5-10 min) never holds up the failback
        _step("iaas-2", "closure", "Verify the recovered reports VM serves from Region B (ASR test failover)", "infra", "automated", "asr_verify_recovery", {"timeout_s": 1200}, ["iaas-1", "fb-6"], "Recovered VM /health OK in Southeast Asia", abort=False),
        _step("iaas-3", "closure", "Clean up the ASR test failover", "infra", "automated", "asr_test_cleanup", deps=["iaas-2"], criteria="Test VM removed", abort=False),
        _step("cl-1", "closure", "Stop heartbeat writer", "dr-agent", "automated", "stop_heartbeat", deps=["fb-6"], criteria="Writer stopped", abort=False),
        _step("cl-2", "closure", "Generate report & notify stakeholders", "dr-agent", "automated", "generate_report", deps=["cl-1"], criteria="Report published", abort=False),
    ]
    return ExercisePlan(steps=s, assumptions=[
        "Region A (Central India) is primary and Region B (Southeast Asia) is a warm standby.",
        "The IaaS reports VM is replicated by Azure Site Recovery; it is validated with a non-disruptive TEST failover.",
        "The outage is simulated by stopping the Region A web app; the database is failed over as if Region A were lost.",
        "No production systems are affected; this runs on the demo environment.",
    ])


def allowlist_for_prompt() -> str:
    return "\n".join(f"- {a}({', '.join(sorted(p)) or ''}): {d}" for a, (p, d) in ALLOWED_ACTIONS.items())


def sequence_for_prompt() -> str:
    return "\n".join(f"{i + 1}. {a} {p or ''}" for i, (a, p) in enumerate(MANDATORY_SEQUENCE))
