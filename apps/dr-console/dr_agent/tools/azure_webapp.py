"""Azure Web App management operations."""

import logging
from typing import Any

from azure.identity import DefaultAzureCredential
from azure.mgmt.web import WebSiteManagementClient

from dr_agent.config import config

logger = logging.getLogger(__name__)

# Lazy-initialized client
_client: WebSiteManagementClient | None = None


def _get_client() -> WebSiteManagementClient:
    """Get or create the Web management client."""
    global _client
    if _client is None:
        credential = DefaultAzureCredential()
        _client = WebSiteManagementClient(
            credential=credential,
            subscription_id=config.azure_subscription_id,
        )
    return _client


def stop_webapp(name: str, resource_group: str | None = None) -> dict[str, Any]:
    """Stop a web app."""
    rg = resource_group or config.workload_rg
    client = _get_client()
    
    logger.info(f"Stopping web app: {name} in {rg}")
    client.web_apps.stop(rg, name)
    
    return {"action": "stop", "name": name, "resource_group": rg, "success": True}


def start_webapp(name: str, resource_group: str | None = None) -> dict[str, Any]:
    """Start a web app."""
    rg = resource_group or config.workload_rg
    client = _get_client()
    
    logger.info(f"Starting web app: {name} in {rg}")
    client.web_apps.start(rg, name)
    
    return {"action": "start", "name": name, "resource_group": rg, "success": True}


def restart_webapp(name: str, resource_group: str | None = None) -> dict[str, Any]:
    """Restart a web app."""
    rg = resource_group or config.workload_rg
    client = _get_client()
    
    logger.info(f"Restarting web app: {name} in {rg}")
    client.web_apps.restart(rg, name)
    
    return {"action": "restart", "name": name, "resource_group": rg, "success": True}


def get_webapp_state(name: str, resource_group: str | None = None) -> dict[str, Any]:
    """Get the state of a web app."""
    rg = resource_group or config.workload_rg
    client = _get_client()
    
    app = client.web_apps.get(rg, name)
    
    return {
        "name": app.name,
        "resource_group": rg,
        "state": app.state,  # Running, Stopped, etc.
        "location": app.location,
        "default_hostname": app.default_host_name,
        "enabled": app.enabled,
        "availability_state": app.availability_state,
    }


def list_app_settings(name: str, resource_group: str | None = None, mask_secrets: bool = True) -> dict[str, str]:
    """List app settings for a web app."""
    rg = resource_group or config.workload_rg
    client = _get_client()
    
    settings = client.web_apps.list_application_settings(rg, name)
    
    result = {}
    for key, value in (settings.properties or {}).items():
        if mask_secrets and ("PASSWORD" in key.upper() or "SECRET" in key.upper() or "KEY" in key.upper()):
            result[key] = "***"
        elif mask_secrets and value and value.startswith("@Microsoft.KeyVault"):
            result[key] = "[KeyVault Reference]"
        else:
            result[key] = value
    
    return result


def stop_webapp_region(region: str) -> dict[str, Any]:
    """Stop the web app in the specified region (A or B)."""
    if region.upper() == "A":
        name = config.webapp_a_name
    elif region.upper() == "B":
        name = config.webapp_b_name
    else:
        raise ValueError(f"Unknown region: {region}. Use 'A' or 'B'.")
    
    return stop_webapp(name)


def start_webapp_region(region: str) -> dict[str, Any]:
    """Start the web app in the specified region (A or B)."""
    if region.upper() == "A":
        name = config.webapp_a_name
    elif region.upper() == "B":
        name = config.webapp_b_name
    else:
        raise ValueError(f"Unknown region: {region}. Use 'A' or 'B'.")
    
    return start_webapp(name)


# ============== CLI for testing ==============

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Azure Web App management CLI")
    parser.add_argument("--stop", metavar="NAME", help="Stop a web app")
    parser.add_argument("--start", metavar="NAME", help="Start a web app")
    parser.add_argument("--status", metavar="NAME", help="Get web app status")
    parser.add_argument("--settings", metavar="NAME", help="List app settings")
    parser.add_argument("--rg", default=None, help="Resource group (default from config)")
    
    args = parser.parse_args()
    
    if args.stop:
        print(stop_webapp(args.stop, args.rg))
    elif args.start:
        print(start_webapp(args.start, args.rg))
    elif args.status:
        print(get_webapp_state(args.status, args.rg))
    elif args.settings:
        settings = list_app_settings(args.settings, args.rg)
        for k, v in settings.items():
            print(f"{k}={v}")
    else:
        parser.print_help()
