---
inclusion: always
---

# Tech stack & conventions

## Azure (all resources tagged `project=dr-agent`)
| Purpose | Service | SKU (cost-capped) |
|---|---|---|
| Workload web app, Region A + B | App Service (Linux, Python 3.11) | B1 plan per region |
| Workload DB + replication | Azure SQL logical server per region + **failover group** | Basic DTU database |
| Global entry + traffic switch | Azure Front Door | Standard |
| DR Agent console + engine | App Service (Linux, Python 3.11) in control-plane RG | B1 |
| LLM | Azure OpenAI in an Azure AI Foundry project | `gpt-4.1-mini` deployment (Global Standard) |
| Exercise state / timeline | Azure Storage **Tables**; reports in **Blob** | Standard LRS |
| Secrets | Key Vault (App Service Key Vault references) | Standard |
| Logs | Log Analytics + Application Insights | Pay-as-you-go |
| Outage injection (optional) | Chaos Studio App Service "stop" fault; otherwise `az`/SDK stop | - |

Regions: Region A = `centralindia`, Region B = `southindia` (an Azure paired region). Front Door is global. Azure OpenAI region is a parameter, because model availability varies.

**Not allowed (cost):** VMs, Site Recovery, AKS, Firewall, App Gateway, VPN/ExpressRoute, Bastion, Premium SKUs, Defender paid plans.

## Code
- **Python 3.11**, **FastAPI**, **Jinja2** templates, vanilla JS + hand-written CSS. No frontend build step.
- Azure SDKs: `azure-identity` (DefaultAzureCredential), `azure-mgmt-web`, `azure-mgmt-sql`, `azure-mgmt-cdn` (Front Door Standard/Premium lives in the CDN RP), `azure-mgmt-resourcegraph`, `azure-data-tables`, `azure-storage-blob`.
- LLM: the `openai` package's `AzureOpenAI` client, authenticated via Entra ID token (`get_bearer_token_provider`). Use structured outputs: pydantic models + JSON schema `response_format`.
- SQL from Python: `pymssql` (bundled FreeTDS, no ODBC driver needed). Connection goes to the **failover group listener** `<fog>.database.windows.net`, so it follows failover automatically.
- Config only via environment variables (App Service app settings). Provide `.env.example`. Never commit secrets.
- Infra: **Bicep** modules + bash scripts using the `az` CLI. Must be idempotent.
- Logging: `logging` with JSON-ish one-line records. Every engine step is also written to the exercise timeline table.
- Tests: `pytest` for pure logic (RTO/RPO math, plan validation, parity diff). Mock Azure calls.
- Style: type hints, small modules, no frameworks beyond the above. Keep dependencies minimal.
