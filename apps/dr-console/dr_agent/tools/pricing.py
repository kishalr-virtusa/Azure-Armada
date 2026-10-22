"""Monthly cost of one region of the workload, from the public Azure Retail Prices API (no auth needed).

Used to turn the strategy guide's cost multipliers into dollars the Architect can compare with the budget.
Prices are list (pay-as-you-go) prices for the SKUs we actually deploy; a fixed fallback is used if the API
is unreachable, and the result says which one was used.
"""
from __future__ import annotations

import time
from typing import Optional

import httpx

API = "https://prices.azure.com/api/retail/prices"
HOURS_PER_MONTH = 730          # Azure's own monthly convention
DAYS_PER_MONTH = 730 / 24      # 30.42
# (label, OData filter, unit -> monthly factor) for one region of the workload
COMPONENTS = [
    ("App Service B1 (Linux)",
     "serviceName eq 'Azure App Service' and armRegionName eq '{region}' and skuName eq 'B1' "
     "and productName eq 'Azure App Service Basic Plan - Linux' and type eq 'Consumption'", HOURS_PER_MONTH),
    ("Azure SQL Database Basic",
     "serviceName eq 'SQL Database' and armRegionName eq '{region}' and skuName eq 'B' "
     "and productName eq 'SQL Database Single Basic' and type eq 'Consumption'", DAYS_PER_MONTH),
]
FALLBACK = {"App Service B1 (Linux)": 14.60, "Azure SQL Database Basic": 4.90}  # East Asia list prices, Oct 2026
_cache: dict[str, tuple[float, dict]] = {}


def region_monthly_usd(region: str = "eastasia", timeout_s: float = 8.0) -> dict:
    """{"total_usd", "components": [{name, unit_price, unit, monthly_usd}], "source": "azure-retail-prices"|"fallback"}"""
    hit: Optional[tuple[float, dict]] = _cache.get(region)
    if hit and time.monotonic() - hit[0] < 6 * 3600:
        return hit[1]
    parts, source = [], "azure-retail-prices"
    try:
        with httpx.Client(timeout=timeout_s) as c:
            for name, flt, factor in COMPONENTS:
                items = c.get(API, params={"$filter": flt.format(region=region)}).raise_for_status().json().get("Items", [])
                if not items:
                    raise LookupError(f"no price for {name} in {region}")
                it = items[0]
                parts.append({"name": name, "unit_price": it["retailPrice"], "unit": it["unitOfMeasure"],
                              "monthly_usd": round(it["retailPrice"] * factor, 2)})
    except Exception:  # noqa: BLE001
        source = "fallback"
        parts = [{"name": k, "unit_price": None, "unit": "month", "monthly_usd": v} for k, v in FALLBACK.items()]
    out = {"region": region, "total_usd": round(sum(p["monthly_usd"] for p in parts), 2), "components": parts,
           "source": source}
    _cache[region] = (time.monotonic(), out)
    return out


TM_ENDPOINT_CHECK_USD = 0.36  # per Azure endpoint health check per month (Azure Retail Prices, Traffic Manager)


def deployed_monthly_usd(region_a: str = "eastasia", region_b: str = "southeastasia") -> dict:
    """What the deployed warm standby costs at list prices: both regions at full size + 2 Traffic Manager endpoints."""
    a, b = region_monthly_usd(region_a), region_monthly_usd(region_b)
    tm = round(2 * TM_ENDPOINT_CHECK_USD, 2)
    total = round(a["total_usd"] + b["total_usd"] + tm, 2)
    return {"total_usd": total, "region_a_usd": a["total_usd"], "region_b_usd": b["total_usd"], "traffic_manager_usd": tm,
            "multiplier": round(total / a["total_usd"], 2) if a["total_usd"] else None,
            "source": "fallback" if "fallback" in (a["source"], b["source"]) else "azure-retail-prices"}
