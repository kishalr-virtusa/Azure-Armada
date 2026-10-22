"""Autopilot: autonomous readiness checks, autonomy levels, never during a drill."""
import json

import pytest

from dr_agent import autopilot, service
from dr_agent.agents import readiness
from dr_agent.llm import AgentResult
from dr_agent.models import Check, ReadinessReport


class FakeStore:
    def __init__(self):
        self.rows, self.events = {}, []

    def get_exercise(self, k):
        return dict(self.rows.get(k, {})) or None

    def upsert_exercise(self, k, d):
        self.rows.setdefault(k, {}).update(d)

    def add_event(self, xid, kind, message, step_id=None, data=None):
        self.events.append({"kind": kind, "message": message, "data": data})


@pytest.fixture
def env(monkeypatch):
    fs = FakeStore()
    monkeypatch.setattr(autopilot, "store", fs)
    monkeypatch.setattr("dr_agent.engine.runner.any_running", lambda: False)
    monkeypatch.setattr(service, "gather_readiness_facts", lambda: {})
    monkeypatch.setattr("dr_agent.tools.notify.notify", lambda *a, **k: None)
    drift = Check(name="App settings parity (A vs B)", status="fail", detail="missing in Region B: PAYMENT_PROVIDER",
                  evidence={"missing_in_region_b": ["PAYMENT_PROVIDER"]})
    monkeypatch.setattr(readiness, "build_checks", lambda facts: [drift])
    report = ReadinessReport(checks=[drift], go=False, summary="Region B misses PAYMENT_PROVIDER.", risks=["drift"])
    monkeypatch.setattr(readiness, "run", lambda checks: AgentResult(report, "llm", 1, None, "foundry-agent"))
    applied = []
    monkeypatch.setattr(service, "apply_parity_fix", lambda keys, by: applied.append((keys, by)) or {"message": "copied"})
    return fs, applied


def test_propose_level_waits_for_a_human(env):
    fs, applied = env
    rec = autopilot.run_once("schedule")
    assert rec["new"] == ["App settings parity (A vs B)"] and rec["ai"]["via"] == "foundry-agent"
    assert autopilot.get_state()["proposal"]["keys"] == ["PAYMENT_PROVIDER"] and applied == []
    autopilot.approve("Kishal")
    assert applied == [(["PAYMENT_PROVIDER"], "Kishal")] and autopilot.get_state()["proposal"] is None


def test_auto_fix_level_applies_the_allowlisted_fix_itself(env):
    fs, applied = env
    autopilot.set_settings(level="auto-fix", by="Kishal")
    rec = autopilot.run_once("schedule")
    assert applied and applied[0][1].startswith("Autopilot") and rec["proposal"]["status"] == "applied automatically"


def test_known_problem_is_not_reannounced(env):
    fs, applied = env
    autopilot.run_once("schedule")
    rec = autopilot.run_once("schedule")
    assert rec["new"] == [] and "ai" not in rec          # no new agent call or alert for the same failure


def test_never_runs_during_a_drill(env, monkeypatch):
    monkeypatch.setattr("dr_agent.engine.runner.any_running", lambda: True)
    assert autopilot.run_once("schedule")["skipped"] == "a drill is running"


def test_pending_fix_survives_later_checks_until_resolved(env, monkeypatch):
    fs, applied = env
    autopilot.run_once("schedule")
    autopilot.run_once("schedule")                      # same problem, not new: the proposal must stay
    assert autopilot.get_state()["proposal"]["keys"] == ["PAYMENT_PROVIDER"]
    monkeypatch.setattr(readiness, "build_checks", lambda facts: [])   # problem gone
    autopilot.run_once("schedule")
    assert autopilot.get_state()["proposal"] is None
