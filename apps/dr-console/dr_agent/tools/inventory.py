"""Inventory and resource discovery via Azure Resource Graph."""

import logging
from typing import Any

from azure.identity import DefaultAzureCredential
from azure.mgmt.resourcegraph import ResourceGraphClient
from azure.mgmt.resourcegraph.models import QueryRequest

from dr_agent.config import config
from dr_agent.tools import azure_webapp, azure_trafficmanager

logger = logging.getLogger(__name__)

# Lazy-initialized client
_client: ResourceGraphClient | None = None


def _get_client() -> ResourceGraphClient:
    """Get or create the Resource Graph client."""
    global _client
    if _client is None:
        credential = DefaultAzureCredential()
        _client = ResourceGraphClient(credential=credential)
    return _client


def query_resources(query: str) -> list[dict[str, Any]]:
    """Run a Resource Graph query."""
    client = _get_client()
    
    request = QueryRequest(
        subscriptions=[config.azure_subscription_id],
        query=query,
    )
    
    response = client.resources(request)
    return response.data


def get_workload_inventory() -> dict[str, Any]:
    """Get a complete inventory of the workload resources."""
    inventory = {
        "resource_group": config.workload_rg,
        "resources": [],
        "web_apps": {},
        "traffic_manager": None,
        "sql_failover_group": None,
    }
    
    # Query all resources in the workload RG
    query = f"""
    Resources
    | where resourceGroup =~ '{config.workload_rg}'
    | project name, type, location, sku=properties.sku, kind
    | order by type, name
    """
    
    try:
        resources = query_resources(query)
        inventory["resources"] = resources
    except Exception as e:
        logger.error(f"Resource Graph query failed: {e}")
        inventory["resources_error"] = str(e)
    
    # Get web app details
    for region, name in [("A", config.webapp_a_name), ("B", config.webapp_b_name)]:
        if name:
            try:
                state = azure_webapp.get_webapp_state(name)
                settings = azure_webapp.list_app_settings(name, mask_secrets=True)
                inventory["web_apps"][f"region_{region.lower()}"] = {
                    **state,
                    "settings": settings,
                }
            except Exception as e:
                logger.error(f"Failed to get web app {name}: {e}")
                inventory["web_apps"][f"region_{region.lower()}"] = {"error": str(e)}
    
    # Get Traffic Manager status
    if config.tm_profile_name:
        try:
            inventory["traffic_manager"] = azure_trafficmanager.get_profile_status()
        except Exception as e:
            logger.error(f"Failed to get TM status: {e}")
            inventory["traffic_manager"] = {"error": str(e)}
    
    # Get SQL FOG status (import here to avoid circular imports)
    if config.sql_fog_name:
        try:
            from dr_agent.engine.runner import _fog
            # failover groups are child resources of the SQL server, so Resource Graph does not list them
            inventory["sql_failover_group"] = {"name": config.sql_fog_name, "database": "paymentsdb",
                                               "servers": {"A": config.sql_server_a, "B": config.sql_server_b},
                                               **_fog().status()}
        except Exception as e:
            logger.error(f"Failed to get FOG status: {e}")
            inventory["sql_failover_group"] = {"error": str(e)}
    
    return inventory


def check_app_settings_parity() -> dict[str, Any]:
    """Check that app settings match between Region A and Region B.
    
    Returns a parity report with matched, missing, and differing keys.
    """
    result = {
        "parity": True,
        "matched": [],
        "missing_in_a": [],
        "missing_in_b": [],
        "different": [],
        "ignored": ["REGION_NAME"],  # Expected to differ
    }
    
    try:
        settings_a = azure_webapp.list_app_settings(config.webapp_a_name, mask_secrets=True)
        settings_b = azure_webapp.list_app_settings(config.webapp_b_name, mask_secrets=True)
    except Exception as e:
        return {"parity": False, "error": str(e)}
    
    all_keys = set(settings_a.keys()) | set(settings_b.keys())
    
    for key in all_keys:
        if key in result["ignored"]:
            continue
        
        in_a = key in settings_a
        in_b = key in settings_b
        
        if in_a and in_b:
            if settings_a[key] == settings_b[key]:
                result["matched"].append(key)
            else:
                result["different"].append({
                    "key": key,
                    "value_a": settings_a[key],
                    "value_b": settings_b[key],
                })
                result["parity"] = False
        elif in_a and not in_b:
            result["missing_in_b"].append(key)
            result["parity"] = False
        elif in_b and not in_a:
            result["missing_in_a"].append(key)
            result["parity"] = False
    
    return result


def snapshot_inventory() -> dict[str, Any]:
    """Take a snapshot of the current inventory state.
    
    Used before and after exercises to detect changes.
    """
    from datetime import datetime, timezone
    
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "inventory": get_workload_inventory(),
        "parity": check_app_settings_parity(),
    }


# ============== CLI for testing ==============

if __name__ == "__main__":
    import argparse
    import json
    
    parser = argparse.ArgumentParser(description="Inventory CLI")
    parser.add_argument("--inventory", action="store_true", help="Get full inventory")
    parser.add_argument("--parity", action="store_true", help="Check app settings parity")
    parser.add_argument("--snapshot", action="store_true", help="Take inventory snapshot")
    parser.add_argument("--query", metavar="QUERY", help="Run a Resource Graph query")
    
    args = parser.parse_args()
    
    if args.inventory:
        print(json.dumps(get_workload_inventory(), indent=2, default=str))
    elif args.parity:
        print(json.dumps(check_app_settings_parity(), indent=2))
    elif args.snapshot:
        print(json.dumps(snapshot_inventory(), indent=2, default=str))
    elif args.query:
        print(json.dumps(query_resources(args.query), indent=2, default=str))
    else:
        parser.print_help()
