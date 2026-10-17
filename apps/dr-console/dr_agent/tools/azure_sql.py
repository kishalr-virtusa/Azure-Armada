"""Azure SQL failover-group control (requirements R5.6, design §4.5).

Failover APIs must be called on the server that will BECOME primary:
  forced  (disaster, may lose data): begin_force_failover_allow_data_loss(rg, <target server>, fog)
  planned (failback, no data loss):  begin_failover(rg, <target server>, fog)

CLI for manual testing:
  python -m dr_agent.tools.azure_sql status   --rg RG --fog FOG --server-a SA --server-b SB
  python -m dr_agent.tools.azure_sql failover --rg RG --fog FOG --server-a SA --server-b SB --to B [--force]
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import time
from datetime import datetime, timezone

from azure.identity import DefaultAzureCredential
from azure.mgmt.sql import SqlManagementClient

log = logging.getLogger(__name__)


class FailoverGroupController:
    def __init__(self, subscription_id: str, resource_group: str, fog_name: str,
                 servers: dict[str, str], credential=None):
        """servers maps region key ("A"/"B") -> logical server name."""
        self.rg, self.fog, self.servers = resource_group, fog_name, servers
        self.client = SqlManagementClient(credential or DefaultAzureCredential(), subscription_id)

    def status(self) -> dict:
        """Role and replication state as seen from each server, plus the current primary region."""
        out: dict = {"fog": self.fog, "servers": {}, "primary_region": None}
        for region, server in self.servers.items():
            try:
                fg = self.client.failover_groups.get(self.rg, server, self.fog)
                role = fg.replication_role
                out["servers"][region] = {"server": server, "role": role, "state": fg.replication_state}
                if role and role.lower() == "primary":
                    out["primary_region"] = region
            except Exception as e:  # noqa: BLE001 - a region may be unreachable during the drill
                out["servers"][region] = {"server": server, "role": None, "state": None,
                                          "error": f"{type(e).__name__}: {e}"[:300]}
        return out

    def failover(self, target_region: str, allow_data_loss: bool, timeout_s: int = 900) -> dict:
        """Make `target_region` primary. Returns timing + resulting status. Raises on failure."""
        if target_region not in self.servers:
            raise ValueError(f"unknown region {target_region!r}; expected one of {list(self.servers)}")
        before = self.status()
        if before["primary_region"] == target_region:
            return {"skipped": True, "reason": f"{target_region} is already primary", "status": before}
        server = self.servers[target_region]
        started = datetime.now(timezone.utc)
        t = time.monotonic()
        ops = self.client.failover_groups
        poller = (ops.begin_force_failover_allow_data_loss(self.rg, server, self.fog) if allow_data_loss
                  else ops.begin_failover(self.rg, server, self.fog))
        poller.result(timeout=timeout_s)
        after = self.status()
        if after["primary_region"] != target_region:
            raise RuntimeError(f"failover finished but primary is {after['primary_region']!r}, expected {target_region!r}")
        return {"skipped": False, "target_region": target_region, "forced": allow_data_loss,
                "started_at": started.isoformat(), "duration_s": round(time.monotonic() - t, 1),
                "status": after}


def _cli() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("action", choices=["status", "failover"])
    p.add_argument("--rg", required=True)
    p.add_argument("--fog", required=True)
    p.add_argument("--server-a", required=True)
    p.add_argument("--server-b", required=True)
    p.add_argument("--to", choices=["A", "B"])
    p.add_argument("--force", action="store_true", help="forced failover (allow data loss)")
    p.add_argument("--subscription", default=os.environ.get("AZURE_SUBSCRIPTION_ID"))
    a = p.parse_args()
    ctl = FailoverGroupController(a.subscription, a.rg, a.fog, {"A": a.server_a, "B": a.server_b})
    if a.action == "status":
        print(json.dumps(ctl.status(), indent=2))
    else:
        if not a.to:
            p.error("--to is required for failover")
        print(json.dumps(ctl.failover(a.to, a.force), indent=2, default=str))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    _cli()
