"""Shared data models (design §4.2). Owned by Claude: agents depend on the exact shapes.

Models marked (LLM) are used as Azure OpenAI structured-output schemas. Strict JSON schema mode forbids
free-form objects, so LLM-facing models use lists of key/value pairs instead of dicts.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

Strategy = Literal["backup-restore", "pilot-light", "warm-standby", "active-passive", "active-active"]
Phase = Literal["pre-checks", "outage", "failover", "validation", "failback", "closure"]
Team = Literal["app", "db", "network", "infra", "dr-agent"]
CheckStatus = Literal["pass", "warn", "fail"]


# ---------- input ----------
class AppProfile(BaseModel):
    name: str = "Contoso Payments"
    tier: int = Field(1, ge=0, le=3)
    rto_min: int = Field(30, gt=0)
    rpo_min: int = Field(15, ge=0)
    monthly_budget_usd: int = 500
    notes: str = ""


# ---------- Phase 1: Architect (LLM) ----------
class StrategyOption(BaseModel):
    name: Strategy
    expected_rto_min: int
    expected_rpo_min: int
    relative_cost: float  # 1.0 = single region
    meets_targets: bool
    note: str
    est_monthly_usd: float = 0.0  # relative_cost x one region at Azure list prices
    within_budget: bool = True


class StrategyRec(BaseModel):
    recommended: Strategy
    options: list[StrategyOption]
    rationale: str
    design_mapping: list[str]  # how the recommendation maps to the deployed Azure resources


# ---------- Phase 1: Readiness ----------
class Check(BaseModel):
    """Produced by deterministic tools, not by the LLM."""
    name: str
    status: CheckStatus
    detail: str
    evidence: dict[str, Any] = {}
    checked_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc).replace(tzinfo=None))


class ReadinessSummary(BaseModel):  # (LLM)
    go: bool
    summary: str
    risks: list[str]


class ReadinessReport(BaseModel):
    checks: list[Check]
    go: bool
    summary: str
    risks: list[str] = []
    source: str = "fallback"


# ---------- Phase 1: Runbook (LLM) ----------
class RunbookStepReview(BaseModel):
    step_no: int
    text: str
    verdict: Literal["valid", "stale", "missing"]
    reason: str
    suggested: str


class RunbookReview(BaseModel):
    steps: list[RunbookStepReview]
    updated_runbook_md: str


# ---------- Phase 2: Plan (LLM) ----------
class Param(BaseModel):
    key: str
    value: str


class PlanStep(BaseModel):
    id: str
    phase: Phase
    title: str
    owner_team: Team
    type: Literal["human", "automated"]
    action: Optional[str]  # tool name from the allowlist; None for human steps
    params: list[Param]
    depends_on: list[str]
    success_criteria: str
    abort_on_failure: bool

    def param_dict(self) -> dict[str, str]:
        return {p.key: p.value for p in self.params}


class ExercisePlan(BaseModel):
    steps: list[PlanStep]
    assumptions: list[str]


# ---------- execution ----------
class Diagnosis(BaseModel):  # (LLM)
    likely_cause: str
    evidence: list[str]
    recommended_action: Literal["retry", "rollback", "continue"]
    fix_action: Literal["none", "tm_set_endpoint", "start_webapp"]  # remediation the engine runs before a retry
    fix_params: list[Param]


class Measurement(BaseModel):
    t0: Optional[datetime] = None
    t_failover: Optional[datetime] = None
    t_recovered: Optional[datetime] = None
    t_failback_start: Optional[datetime] = None
    t_failback_done: Optional[datetime] = None
    rto_s: Optional[float] = None
    rpo_s: Optional[float] = None
    failback_s: Optional[float] = None
    rto_target_s: int = 0
    rpo_target_s: int = 0
    rto_met: bool = False
    rpo_met: bool = False
    evidence: dict[str, Any] = {}


# ---------- report ----------
class RemediationItem(BaseModel):
    item: str
    owner: Team
    priority: Literal["high", "medium", "low"]


class ReportNarrative(BaseModel):  # (LLM)
    executive_summary: str
    what_went_well: list[str]
    issues: list[str]
    remediation: list[RemediationItem]


class ExerciseReport(BaseModel):
    html: str
    summary_text: str
    remediation: list[RemediationItem]
