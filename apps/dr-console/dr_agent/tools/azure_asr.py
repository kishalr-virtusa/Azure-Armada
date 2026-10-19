"""Azure Site Recovery (A2A) test failover for the IaaS tier (requirements: IaaS DR validation).

A *test* failover boots a copy of the replicated VM in Region B inside an isolated network (no impact on
production), so the drill can prove the VM really recovers: the engine then runs a command inside the
recovered VM (Run Command, no network access needed) and checks that the reports service answers from Region B
and produces the daily payments report (infra/vm/reports/reports-service.ps1): live from the database when the
test network can reach Azure SQL, otherwise the latest report replicated on the VM's disk.
Cleanup deletes the test VM. Names follow infra/scripts/asr.sh.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import Any, Optional

import httpx

log = logging.getLogger(__name__)

ARM = "https://management.azure.com"
ASR_API = "api-version=2024-04-01"
COMPUTE_API = "api-version=2024-07-01"

# Run inside the recovered Windows VM. One marker per section (DRA_<NAME>=<body>) so the output can be parsed
# even when Run Command joins or wraps lines. /report/generate proves live database access from the DR region;
# when it fails (isolated network, DPAPI, old service) /report/latest proves the replicated server holds its
# reports.
VERIFY_PS = [
    "$ProgressPreference = 'SilentlyContinue'",
    "$h = $null",
    "for ($i = 0; $i -lt 24 -and -not $h; $i++) { try { $h = Invoke-WebRequest -UseBasicParsing -TimeoutSec 5 "
    "-Uri 'http://localhost:8080/health' } catch { Start-Sleep 5 } }",
    "if (-not $h) { 'DRA_HEALTH_FAIL'; exit 1 }",
    "'DRA_HEALTH=' + ($h.Content -replace '\\s+', ' ')",
    "try { 'DRA_GENERATE=' + ((Invoke-WebRequest -UseBasicParsing -Method Post -TimeoutSec 120 "
    "-Uri 'http://localhost:8080/report/generate').Content -replace '\\s+', ' ') }",
    "catch { $b = $_.ErrorDetails.Message; if (-not $b) { $b = $_.Exception.Message }; "
    "'DRA_GENERATE_ERROR=' + ($b -replace '\\s+', ' ')",
    "  try { 'DRA_LATEST=' + ((Invoke-WebRequest -UseBasicParsing -TimeoutSec 15 "
    "-Uri 'http://localhost:8080/report/latest').Content -replace '\\s+', ' ') }",
    "  catch { $b = $_.ErrorDetails.Message; if (-not $b) { $b = $_.Exception.Message }; "
    "'DRA_LATEST_ERROR=' + ($b -replace '\\s+', ' ') } }",
    "exit 0",
]

_MARKER = re.compile(r"DRA_(HEALTH|GENERATE_ERROR|GENERATE|LATEST_ERROR|LATEST)=")
GENERATED_IN_DR = "generated in DR"
CACHED = "cached on replicated disk"


def _segments(msg: str) -> dict[str, Any]:
    """Split Run Command output into DRA_<NAME>= sections and decode JSON bodies. Line breaks are removed first
    (the Windows console may wrap long lines; compact JSON never contains a raw newline)."""
    msg = msg or ""
    hits = list(_MARKER.finditer(msg))
    out: dict[str, Any] = {}
    for i, m in enumerate(hits):
        end = hits[i + 1].start() if i + 1 < len(hits) else len(msg)
        text = msg[m.end():end].replace("\r", "").replace("\n", "").strip()
        val: Any = text
        if text.startswith("{"):
            try:
                val, _ = json.JSONDecoder().raw_decode(text)
            except ValueError:
                pass
        out[m.group(1)] = val
    return out


def _is_report(o: Any) -> bool:
    return isinstance(o, dict) and "payments_count" in o and "date" in o


def _region_label(r: Optional[str]) -> str:
    r = (r or "").strip()
    return "SoutheastAsia" if r.lower() == "southeastasia" else (r or "unknown")


def parse_verify_output(msg: str) -> dict[str, Any]:
    """Interpret the output of VERIFY_PS (or of the old /health-only check, for an un-updated VM)."""
    seg = _segments(msg)
    health = seg.get("HEALTH")
    if isinstance(health, dict):
        ok = str(health.get("status", "")).lower() == "ok"
        region = _region_label(health.get("region"))
    else:  # old check or unexpected output: same rule as before
        ok = bool(re.search(r'"status"\s*:\s*"ok"', msg or ""))
        region = "SoutheastAsia" if "southeastasia" in (msg or "").lower() else "unknown"
    res: dict[str, Any] = {"service": "ok" if ok else "down", "reported_region": region, "report_source": None,
                           "report_date": None, "payments_count": None, "total_amount": None,
                           "generated_in_region": None, "generated_at": None, "report_db_server": None,
                           "report_error": None}
    gen, err, latest = seg.get("GENERATE"), seg.get("GENERATE_ERROR"), seg.get("LATEST")
    report, source = None, None
    if _is_report(gen):
        report, source = gen, GENERATED_IN_DR
    else:
        if gen is not None:
            res["report_error"] = "the reports service on the VM does not support /report/generate (old version)"
        elif isinstance(err, dict):
            res["report_error"] = str(err.get("reason") or err)[:300]
        elif err:
            res["report_error"] = str(err)[:300]
        cached = latest if _is_report(latest) else (err.get("latest_cached") if isinstance(err, dict) else None)
        if _is_report(cached):
            report, source = cached, CACHED
        elif res["report_error"] is None and seg.get("LATEST_ERROR"):
            res["report_error"] = str(seg["LATEST_ERROR"])[:300]
    if report:
        db = report.get("database") if isinstance(report.get("database"), dict) else {}
        try:
            count: Optional[int] = int(report.get("payments_count"))
        except (TypeError, ValueError):
            count = None
        res.update(report_source=source, report_date=report.get("date"), payments_count=count,
                   total_amount=report.get("total_amount"), generated_in_region=report.get("generated_in_region"),
                   generated_at=report.get("generated_at"), report_db_server=db.get("server"))
    return res


class Arm:
    """Minimal ARM client: bearer token from DefaultAzureCredential, long-running operation polling."""

    def __init__(self):
        from azure.identity import DefaultAzureCredential
        self._cred = DefaultAzureCredential()
        self._http = httpx.Client(timeout=60)

    def _h(self) -> dict:
        return {"Authorization": f"Bearer {self._cred.get_token(ARM + '/.default').token}"}

    def get(self, path: str, api: str) -> Optional[dict]:
        r = self._http.get(f"{ARM}{path}?{api}", headers=self._h())
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json()

    def post(self, path: str, api: str, body: dict) -> httpx.Response:
        r = self._http.post(f"{ARM}{path}?{api}", headers=self._h(), json=body)
        if r.status_code >= 300:
            raise RuntimeError(f"POST {path.rsplit('/', 1)[-1]} failed: HTTP {r.status_code} {r.text[:300]}")
        return r

    def wait(self, resp: httpx.Response, timeout_s: int, poll_s: int = 15) -> dict:
        """Follow Azure-AsyncOperation / Location until the operation finishes; returns the final body."""
        url = resp.headers.get("Azure-AsyncOperation") or resp.headers.get("Location")
        if not url:
            return resp.json() if resp.content else {}
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            r = self._http.get(url, headers=self._h())
            body = r.json() if r.content else {}
            status = (body.get("status") or body.get("properties", {}).get("state") or "").lower()
            if r.status_code == 200 and status in ("", "succeeded"):
                return body
            if status in ("failed", "canceled", "cancelled"):
                raise RuntimeError(f"operation {status}: {str(body.get('error') or body)[:400]}")
            time.sleep(poll_s)
        raise TimeoutError(f"operation not finished after {timeout_s}s")


class AsrTestFailover:
    def __init__(self, sub: str, rg: str, suffix: str, recovery_rg: str = "rg-dra-recovery",
                 vm: str = "dra-vm-reports", loc_a: str = "eastasia", loc_b: str = "southeastasia"):
        self.arm = Arm()
        self.sub, self.recovery_rg, self.vm, self.loc_b = sub, recovery_rg, vm, loc_b
        vault = f"/subscriptions/{sub}/resourceGroups/{rg}/providers/Microsoft.RecoveryServices/vaults/dra-rsv-{suffix}"
        fab = f"asr-a2a-{loc_a}"
        self.item = f"{vault}/replicationFabrics/{fab}/replicationProtectionContainers/{fab}-container/replicationProtectedItems/{vm}"
        self.test_vnet = f"/subscriptions/{sub}/resourceGroups/{rg}/providers/Microsoft.Network/virtualNetworks/dra-vnet-sea-test-{suffix}"

    @classmethod
    def from_env(cls) -> "AsrTestFailover":
        from dr_agent.config import config
        suffix = os.environ.get("RESOURCE_SUFFIX") or config.webapp_a_name.rsplit("-", 1)[-1]
        return cls(config.azure_subscription_id, config.workload_rg, suffix)

    # ---------------------------------------------------------------- state
    def status(self) -> dict[str, Any]:
        it = self.arm.get(self.item, ASR_API)
        if not it:
            return {"protected": False, "state": "not configured"}
        p, d = it["properties"], it["properties"].get("providerSpecificDetails", {})
        return {"protected": p.get("protectionState") == "Protected", "state": p.get("protectionStateDescription"),
                "health": p.get("replicationHealth"), "test_failover": p.get("testFailoverStateDescription"),
                "rpo_s": d.get("rpoInSeconds"), "initial_replication_pct": d.get("monitoringPercentageCompletion"),
                "allowed": p.get("allowedOperations") or []}

    def latest_recovery_point(self) -> Optional[str]:
        pts = (self.arm.get(f"{self.item}/recoveryPoints", ASR_API) or {}).get("value", [])
        pts = [x for x in pts if x.get("properties", {}).get("recoveryPointTime")]
        return max(pts, key=lambda x: x["properties"]["recoveryPointTime"])["id"] if pts else None

    # ---------------------------------------------------------------- actions
    def start(self) -> dict:
        """Kick off the test failover into the isolated network; returns immediately with the operation URL."""
        st = self.status()
        if not st["protected"]:
            raise RuntimeError(f"VM is not protected yet ({st.get('state')}, initial replication "
                               f"{st.get('initial_replication_pct')}%)")
        rp = self.latest_recovery_point()
        details: dict[str, Any] = {"instanceType": "A2A"}
        if rp:
            details["recoveryPointId"] = rp
        r = self.arm.post(f"{self.item}/testFailover", ASR_API, {"properties": {
            "failoverDirection": "PrimaryToRecovery", "networkType": "VmNetworkAsInput",
            "networkId": self.test_vnet, "providerSpecificDetails": details}})
        return {"operation": r.headers.get("Azure-AsyncOperation") or r.headers.get("Location"),
                "recovery_point": rp.rsplit("/", 1)[-1] if rp else "latest"}

    def wait_done(self, operation_url: Optional[str], timeout_s: int = 1500, poll_s: int = 15) -> None:
        if operation_url:
            fake = httpx.Response(202, headers={"Azure-AsyncOperation": operation_url})
            self.arm.wait(fake, timeout_s, poll_s=poll_s)
            return
        deadline = time.monotonic() + timeout_s  # fallback: watch the item until cleanup becomes possible
        while time.monotonic() < deadline:
            if "TestFailoverCleanup" in self.status()["allowed"]:
                return
            time.sleep(20)
        raise TimeoutError("test failover did not complete")

    def test_vm(self) -> Optional[dict]:
        vms = (self.arm.get(f"/subscriptions/{self.sub}/resourceGroups/{self.recovery_rg}/providers/"
                            f"Microsoft.Compute/virtualMachines", COMPUTE_API) or {}).get("value", [])
        cands = [v for v in vms if v["name"].startswith(self.vm)]
        return cands[0] if cands else None

    def verify(self, timeout_s: int = 600) -> dict:
        """Run a command inside the recovered VM: the reports service must answer, from Region B, and produce the
        daily payments report - live from the database if the isolated network can reach it ("generated in DR"),
        otherwise the latest report on the replicated disk ("cached on replicated disk")."""
        vm = self.test_vm()
        if not vm:
            raise RuntimeError(f"no test VM found in {self.recovery_rg}")
        windows = (vm.get("properties", {}).get("storageProfile", {}).get("osDisk", {}).get("osType") == "Windows")
        body = ({"commandId": "RunPowerShellScript", "script": VERIFY_PS}
                if windows else
                {"commandId": "RunShellScript", "script": [
                    "for i in $(seq 1 12); do curl -s -m 3 localhost:8080/health && exit 0; sleep 5; done; exit 1"]})
        deadline = time.monotonic() + timeout_s
        last = ""
        while time.monotonic() < deadline:
            try:
                r = self.arm.post(f"{vm['id']}/runCommand", COMPUTE_API, body)
                out = self.arm.wait(r, 600, poll_s=10)
                msg = " ".join(v.get("message", "") for v in (out.get("value") or out.get("properties", {}).get("output", {}).get("value", [])))
                last = msg
                res = parse_verify_output(msg)
                if res["service"] == "ok":
                    return {"test_vm": vm["name"], "location": vm["location"], "os": "Windows" if windows else "Linux",
                            **res, "output": msg[-300:]}
            except Exception as e:  # noqa: BLE001 - the VM agent may still be starting
                last = f"{type(e).__name__}: {e}"
            time.sleep(15)
        raise RuntimeError(f"recovered VM did not serve /health: {last[-300:]}")

    def cleanup(self, timeout_s: int = 1200) -> dict:
        if "TestFailoverCleanup" not in self.status()["allowed"]:
            return {"cleanup": "nothing to clean"}
        r = self.arm.post(f"{self.item}/testFailoverCleanup", ASR_API,
                          {"properties": {"comments": "DR Agent drill: validated, cleaning up"}})
        self.arm.wait(r, timeout_s)
        return {"cleanup": "done"}
