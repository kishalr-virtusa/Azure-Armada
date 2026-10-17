"""Azure Traffic Manager operations."""

import logging
from typing import Any

import dns.resolver
from azure.identity import DefaultAzureCredential
from azure.mgmt.trafficmanager import TrafficManagerManagementClient

from dr_agent.config import config

logger = logging.getLogger(__name__)

# Lazy-initialized client
_client: TrafficManagerManagementClient | None = None


def _get_client() -> TrafficManagerManagementClient:
    """Get or create the Traffic Manager management client."""
    global _client
    if _client is None:
        credential = DefaultAzureCredential()
        _client = TrafficManagerManagementClient(
            credential=credential,
            subscription_id=config.azure_subscription_id,
        )
    return _client


def get_profile_status() -> dict[str, Any]:
    """Get the Traffic Manager profile status including all endpoints."""
    client = _get_client()
    
    profile = client.profiles.get(
        config.workload_rg,
        config.tm_profile_name,
    )
    
    endpoints = []
    for ep in profile.endpoints or []:
        endpoints.append({
            "name": ep.name,
            "type": ep.type,
            "target": ep.target,
            "endpoint_status": ep.endpoint_status,  # Enabled/Disabled
            "endpoint_monitor_status": ep.endpoint_monitor_status,  # Online/Degraded/Disabled/etc.
            "priority": ep.priority,
            "weight": ep.weight,
        })
    
    return {
        "name": profile.name,
        "profile_status": profile.profile_status,
        "traffic_routing_method": profile.traffic_routing_method,
        "dns_config": {
            "relative_name": profile.dns_config.relative_name if profile.dns_config else None,
            "fqdn": profile.dns_config.fqdn if profile.dns_config else None,
            "ttl": profile.dns_config.ttl if profile.dns_config else None,
        },
        "monitor_config": {
            "protocol": profile.monitor_config.protocol if profile.monitor_config else None,
            "port": profile.monitor_config.port if profile.monitor_config else None,
            "path": profile.monitor_config.path if profile.monitor_config else None,
        },
        "endpoints": endpoints,
    }


def get_endpoint(endpoint_name: str) -> dict[str, Any]:
    """Get a specific endpoint status."""
    client = _get_client()
    
    ep = client.endpoints.get(
        config.workload_rg,
        config.tm_profile_name,
        "AzureEndpoints",
        endpoint_name,
    )
    
    return {
        "name": ep.name,
        "endpoint_status": ep.endpoint_status,
        "endpoint_monitor_status": ep.endpoint_monitor_status,
        "priority": ep.priority,
        "target": ep.target,
    }


def set_endpoint_status(endpoint_name: str, enabled: bool) -> dict[str, Any]:
    """Enable or disable a Traffic Manager endpoint."""
    client = _get_client()
    
    # Get current endpoint
    ep = client.endpoints.get(
        config.workload_rg,
        config.tm_profile_name,
        "AzureEndpoints",
        endpoint_name,
    )
    
    # Update status
    new_status = "Enabled" if enabled else "Disabled"
    ep.endpoint_status = new_status
    
    logger.info(f"Setting endpoint {endpoint_name} to {new_status}")
    
    updated = client.endpoints.create_or_update(
        config.workload_rg,
        config.tm_profile_name,
        "AzureEndpoints",
        endpoint_name,
        ep,
    )
    
    return {
        "name": updated.name,
        "endpoint_status": updated.endpoint_status,
        "action": "enabled" if enabled else "disabled",
        "success": True,
    }


def set_endpoint_region(region: str, enabled: bool) -> dict[str, Any]:
    """Enable or disable an endpoint by region (A or B)."""
    if region.upper() == "A":
        endpoint_name = "endpoint-a"
    elif region.upper() == "B":
        endpoint_name = "endpoint-b"
    else:
        raise ValueError(f"Unknown region: {region}. Use 'A' or 'B'.")
    
    return set_endpoint_status(endpoint_name, enabled)


def resolve_tm_dns() -> dict[str, Any]:
    """Resolve the Traffic Manager DNS to see which endpoint is active.
    
    Uses a fresh DNS resolution (no cache) to get the current routing.
    """
    try:
        # Create a new resolver with no cache
        resolver = dns.resolver.Resolver()
        resolver.cache = dns.resolver.Cache()
        
        # Resolve the CNAME chain
        fqdn = config.tm_fqdn
        answers = resolver.resolve(fqdn, "CNAME")
        
        cname = str(answers[0].target).rstrip(".")
        
        # The CNAME points to the actual app hostname
        return {
            "fqdn": fqdn,
            "cname": cname,
            "points_to_region_a": config.webapp_a_name in cname,
            "points_to_region_b": config.webapp_b_name in cname,
        }
    except Exception as e:
        logger.error(f"DNS resolution failed: {e}")
        return {
            "fqdn": config.tm_fqdn,
            "error": str(e),
        }


# ============== CLI for testing ==============

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Azure Traffic Manager CLI")
    parser.add_argument("--status", action="store_true", help="Get profile status")
    parser.add_argument("--endpoint", metavar="NAME", help="Get endpoint status")
    parser.add_argument("--enable", metavar="NAME", help="Enable an endpoint")
    parser.add_argument("--disable", metavar="NAME", help="Disable an endpoint")
    parser.add_argument("--dns", action="store_true", help="Resolve TM DNS")
    
    args = parser.parse_args()
    
    import json
    
    if args.status:
        print(json.dumps(get_profile_status(), indent=2))
    elif args.endpoint:
        print(json.dumps(get_endpoint(args.endpoint), indent=2))
    elif args.enable:
        print(json.dumps(set_endpoint_status(args.enable, True), indent=2))
    elif args.disable:
        print(json.dumps(set_endpoint_status(args.disable, False), indent=2))
    elif args.dns:
        print(json.dumps(resolve_tm_dns(), indent=2))
    else:
        parser.print_help()
