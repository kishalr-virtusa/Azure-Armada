"""Azure Site Recovery (A2A) test failover for the IaaS tier (requirements: IaaS DR validation).

A *test* failover boots a copy of the replicated VM in Region B inside an isolated network (no impact on
production), so the drill can prove the VM really recovers: the engine then runs a command inside the
recovered VM (Run Command, no network access needed) and checks that the service answers from Region B.
Cleanup deletes the test VM. Names follow infra/scripts/asr.sh.
"""
from __future__ import annotations

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
        """Run a command inside the recovered VM: the reports service must answer, from Region B."""
        vm = self.test_vm()
        if not vm:
            raise RuntimeError(f"no test VM found in {self.recovery_rg}")
        windows = (vm.get("properties", {}).get("storageProfile", {}).get("osDisk", {}).get("osType") == "Windows")
        body = ({"commandId": "RunPowerShellScript", "script": [
                    "for ($i=0; $i -lt 12; $i++) { try { (Invoke-RestMethod -TimeoutSec 3 http://localhost:8080/health) "
                    "| ConvertTo-Json -Compress; exit 0 } catch { Start-Sleep 5 } }; exit 1"]}
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
                if re.search(r'"status"\s*:\s*"ok"', msg):
                    region = "SoutheastAsia" if "southeastasia" in msg.lower() else "unknown"
                    return {"test_vm": vm["name"], "location": vm["location"], "service": "ok", "reported_region": region,
                            "os": "Windows" if windows else "Linux", "output": msg[-300:]}
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
