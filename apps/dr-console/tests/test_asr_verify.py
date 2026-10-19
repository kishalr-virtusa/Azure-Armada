"""ASR verify(): parsing what the recovered reports VM printed (Windows Run Command output) - no Azure calls."""
import json

import httpx

from dr_agent.engine.runner import iaas_report_summary
from dr_agent.tools import azure_asr
from dr_agent.tools.azure_asr import CACHED, GENERATED_IN_DR, AsrTestFailover, parse_verify_output

HEALTH = {"service": "contoso-reports", "status": "ok", "host": "reports", "region": "southeastasia",
          "last_report": "2026-10-19"}
REPORT = {"report": "daily-payments", "date": "2026-10-19", "final": False, "generated_at": "2026-10-19T10:31:02Z",
          "generated_in_region": "southeastasia", "generated_on_host": "REPORTS", "source": "live database",
          "database": {"endpoint": "dra-fog-abc.database.windows.net", "server": "dra-sql-eas-abc",
                       "database": "paymentsdb", "updateability": "READ_WRITE"},
          "payments_count": 42, "total_amount": 1234.5,
          "by_region": {"A": {"count": 40, "amount": 1200.0}, "B": {"count": 2, "amount": 34.5}},
          "first_payment_at": "2026-10-19T00:01:10.000Z", "last_payment_at": "2026-10-19T10:30:59.120Z"}
CACHED_REPORT = {**REPORT, "generated_in_region": "eastasia", "source": "cached",
                 "cached_file": "C:\\reports\\payments-2026-10-19.json", "age_minutes": 12.5}


def _c(o):
    return json.dumps(o, separators=(",", ":"))


def test_generated_in_dr_with_crlf_and_wrapped_line():
    body = _c(REPORT)
    wrapped = body[:80] + "\r\n" + body[80:]  # a console line break inside the JSON must not break parsing
    msg = f"DRA_HEALTH={_c(HEALTH)}\r\nDRA_GENERATE={wrapped}\r\n"
    r = parse_verify_output(msg)
    assert r["service"] == "ok" and r["reported_region"] == "SoutheastAsia"
    assert r["report_source"] == GENERATED_IN_DR
    assert (r["report_date"], r["payments_count"], r["generated_in_region"]) == ("2026-10-19", 42, "southeastasia")
    assert r["report_db_server"] == "dra-sql-eas-abc" and r["report_error"] is None


def test_database_unreachable_falls_back_to_cached_report():
    err = {"status": "error", "reason": "database unreachable: dra-fog-abc.database.windows.net: A network-related "
                                        "error occurred", "region": "southeastasia", "latest_cached": CACHED_REPORT}
    msg = f"DRA_HEALTH={_c(HEALTH)}\nDRA_GENERATE_ERROR={_c(err)}\nDRA_LATEST={_c(CACHED_REPORT)}\n"
    r = parse_verify_output(msg)
    assert r["service"] == "ok" and r["report_source"] == CACHED
    assert r["payments_count"] == 42 and r["generated_in_region"] == "eastasia"
    assert r["report_error"].startswith("database unreachable")


def test_cached_report_from_error_body_when_latest_missing():
    err = {"status": "error", "reason": "credentials cannot be decrypted on this machine (DPAPI LocalMachine)",
           "latest_cached": CACHED_REPORT}
    r = parse_verify_output(f"DRA_HEALTH={_c(HEALTH)} DRA_GENERATE_ERROR={_c(err)} DRA_LATEST_ERROR=timed out")
    assert r["report_source"] == CACHED and "DPAPI" in r["report_error"]


def test_no_report_anywhere():
    err = {"status": "error", "reason": "not configured", "latest_cached": None}
    r = parse_verify_output(f"DRA_HEALTH={_c(HEALTH)}\nDRA_GENERATE_ERROR={_c(err)}\n"
                            'DRA_LATEST_ERROR={"status":"error","reason":"no report on disk yet"}\n')
    assert r["service"] == "ok" and r["report_source"] is None and r["payments_count"] is None
    assert r["report_error"] == "not configured"


def test_old_service_and_old_output_stay_compatible():
    old = {"service": "contoso-reports", "status": "ok", "host": "reports", "region": "southeastasia"}
    r = parse_verify_output(_c(old))  # output of the previous /health-only check
    assert r["service"] == "ok" and r["reported_region"] == "SoutheastAsia" and r["report_source"] is None
    r2 = parse_verify_output(f"DRA_HEALTH={_c(old)}\nDRA_GENERATE={_c(old)}\n")  # old service answers every path
    assert r2["service"] == "ok" and r2["report_source"] is None and "old version" in r2["report_error"]
    assert parse_verify_output("DRA_HEALTH_FAIL")["service"] == "down"


class _FakeArm:
    def __init__(self, outputs):
        self.outputs, self.bodies = list(outputs), []

    def post(self, path, api, body):
        self.bodies.append(body)
        o = self.outputs.pop(0)
        if isinstance(o, Exception):
            raise o
        return httpx.Response(200, json=o)

    def wait(self, resp, timeout_s, poll_s=15):
        return resp.json()


def _asr(arm):
    a = AsrTestFailover.__new__(AsrTestFailover)  # skip __init__: no Azure credential
    a.arm, a.recovery_rg, a.vm, a.loc_b = arm, "rg-dra-recovery", "dra-vm-reports", "southeastasia"
    a.test_vm = lambda: {"id": "/subscriptions/x/vm/dra-vm-reports-test", "name": "dra-vm-reports-test",
                         "location": "southeastasia",
                         "properties": {"storageProfile": {"osDisk": {"osType": "Windows"}}}}
    return a


def test_verify_end_to_end_with_mocked_run_command(monkeypatch):
    monkeypatch.setattr(azure_asr.time, "sleep", lambda s: None)
    out = {"value": [{"code": "ComponentStatus/StdOut/succeeded",
                      "message": f"DRA_HEALTH={_c(HEALTH)}\nDRA_GENERATE={_c(REPORT)}\n"},
                     {"code": "ComponentStatus/StdErr/succeeded", "message": ""}]}
    arm = _FakeArm([RuntimeError("VM agent not ready"), out])
    res = _asr(arm).verify(timeout_s=60)
    for k in ("test_vm", "location", "service", "reported_region", "os", "output"):  # keys the engine relies on
        assert k in res
    assert (res["test_vm"], res["location"], res["os"]) == ("dra-vm-reports-test", "southeastasia", "Windows")
    assert res["service"] == "ok" and res["report_source"] == GENERATED_IN_DR and res["payments_count"] == 42
    body = arm.bodies[-1]
    assert body["commandId"] == "RunPowerShellScript" and any("/report/generate" in line for line in body["script"])


def test_measurement_message_mentions_the_report():
    gen = parse_verify_output(f"DRA_HEALTH={_c(HEALTH)}\nDRA_GENERATE={_c(REPORT)}")
    assert iaas_report_summary(gen) == ("the recovered server produced the daily payments report (42 payments, "
                                        "2026-10-19) in Southeast Asia, live from the database")
    err = {"status": "error", "reason": "database unreachable: timeout", "latest_cached": CACHED_REPORT}
    cached = parse_verify_output(f"DRA_HEALTH={_c(HEALTH)}\nDRA_GENERATE_ERROR={_c(err)}")
    msg = iaas_report_summary(cached)
    assert "replicated disk (42 payments, 2026-10-19, generated in eastasia)" in msg and "timeout" in msg
    assert iaas_report_summary({"report_source": None}).startswith("service verified inside the VM")
