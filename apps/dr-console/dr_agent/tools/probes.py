"""HTTP probes for health checking."""

import logging
import time
from typing import Any
from datetime import datetime, timezone

import httpx
import dns.resolver

from dr_agent.config import config

logger = logging.getLogger(__name__)

# HTTP client with short timeout
_client = httpx.Client(timeout=5.0, follow_redirects=True)


def probe(url: str) -> dict[str, Any]:
    """Probe a URL and return health status.
    
    Returns:
        dict with keys: ok, region, db_ok, latency_ms, error
    """
    start = time.monotonic()
    
    try:
        response = _client.get(url)
        latency_ms = int((time.monotonic() - start) * 1000)
        
        if response.status_code == 200:
            try:
                data = response.json()
                return {
                    "ok": True,
                    "status_code": response.status_code,
                    "region": data.get("region"),
                    "db_ok": data.get("db_ok"),
                    "db_server": data.get("db_server"),
                    "latency_ms": latency_ms,
                }
            except Exception:
                return {
                    "ok": True,
                    "status_code": response.status_code,
                    "latency_ms": latency_ms,
                }
        else:
            return {
                "ok": False,
                "status_code": response.status_code,
                "latency_ms": latency_ms,
                "error": f"HTTP {response.status_code}",
            }
    except Exception as e:
        latency_ms = int((time.monotonic() - start) * 1000)
        return {
            "ok": False,
            "latency_ms": latency_ms,
            "error": str(e),
        }


def probe_tm(expect_region: str | None = None) -> dict[str, Any]:
    """Probe the Traffic Manager endpoint with fresh DNS resolution.
    
    Args:
        expect_region: If provided, check that the response comes from this region.
    
    Returns:
        dict with probe result plus DNS resolution info.
    """
    # First resolve DNS fresh
    try:
        resolver = dns.resolver.Resolver()
        resolver.cache = dns.resolver.Cache()
        
        answers = resolver.resolve(config.tm_fqdn, "CNAME")
        cname = str(answers[0].target).rstrip(".")
    except Exception as e:
        cname = None
        logger.warning(f"DNS resolution failed: {e}")
    
    # Probe the TM URL
    tm_url = f"http://{config.tm_fqdn}/health"
    result = probe(tm_url)
    
    result["dns_cname"] = cname
    result["tm_fqdn"] = config.tm_fqdn
    result["timestamp"] = datetime.now(timezone.utc).isoformat()
    
    # Check expected region
    if expect_region and result.get("ok"):
        actual_region = result.get("region", "")
        result["expected_region"] = expect_region
        result["region_match"] = expect_region.lower() in actual_region.lower()
    
    return result


def probe_direct(region: str) -> dict[str, Any]:
    """Probe a region's app directly (not via Traffic Manager).
    
    Args:
        region: 'A' or 'B'
    """
    if region.upper() == "A":
        hostname = f"{config.webapp_a_name}.azurewebsites.net"
    elif region.upper() == "B":
        hostname = f"{config.webapp_b_name}.azurewebsites.net"
    else:
        raise ValueError(f"Unknown region: {region}. Use 'A' or 'B'.")
    
    url = f"https://{hostname}/health"
    result = probe(url)
    result["region_probed"] = region.upper()
    result["hostname"] = hostname
    result["timestamp"] = datetime.now(timezone.utc).isoformat()
    
    return result


def wait_healthy(
    url: str,
    expect_region: str | None = None,
    timeout_s: int = 900,
    interval_s: int = 5,
) -> dict[str, Any]:
    """Poll until the URL is healthy or timeout.
    
    Args:
        url: URL to probe (use 'tm' for Traffic Manager)
        expect_region: Expected region in response
        timeout_s: Maximum time to wait (default 15 minutes)
        interval_s: Polling interval
    
    Returns:
        dict with success status, duration, and last probe result
    """
    start = time.monotonic()
    deadline = start + timeout_s
    attempts = 0
    last_result = None
    
    while time.monotonic() < deadline:
        attempts += 1
        
        if url.lower() == "tm":
            last_result = probe_tm(expect_region)
        else:
            last_result = probe(url)
        
        # Check success criteria
        is_healthy = last_result.get("ok") and last_result.get("db_ok")
        region_ok = not expect_region or last_result.get("region_match", True)
        
        if is_healthy and region_ok:
            duration_s = time.monotonic() - start
            return {
                "success": True,
                "duration_s": round(duration_s, 2),
                "attempts": attempts,
                "last_result": last_result,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        
        logger.info(f"wait_healthy: attempt {attempts}, healthy={is_healthy}, region_ok={region_ok}")
        time.sleep(interval_s)
    
    duration_s = time.monotonic() - start
    return {
        "success": False,
        "duration_s": round(duration_s, 2),
        "attempts": attempts,
        "last_result": last_result,
        "error": f"Timeout after {timeout_s}s",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# ============== CLI for testing ==============

if __name__ == "__main__":
    import argparse
    import json
    
    parser = argparse.ArgumentParser(description="HTTP Probes CLI")
    parser.add_argument("--probe", metavar="URL", help="Probe a URL")
    parser.add_argument("--tm", action="store_true", help="Probe Traffic Manager")
    parser.add_argument("--direct", metavar="REGION", help="Probe region directly (A or B)")
    parser.add_argument("--wait", metavar="URL", help="Wait for URL to be healthy")
    parser.add_argument("--expect-region", metavar="REGION", help="Expected region")
    parser.add_argument("--timeout", type=int, default=60, help="Timeout in seconds")
    
    args = parser.parse_args()
    
    if args.probe:
        print(json.dumps(probe(args.probe), indent=2))
    elif args.tm:
        print(json.dumps(probe_tm(args.expect_region), indent=2))
    elif args.direct:
        print(json.dumps(probe_direct(args.direct), indent=2))
    elif args.wait:
        print(json.dumps(wait_healthy(args.wait, args.expect_region, args.timeout), indent=2))
    else:
        parser.print_help()
