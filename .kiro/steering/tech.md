---
inclusion: always
---

# Tech stack & conventions

## Azure (all resources tagged `project=dr-agent`)
| Purpose | Service | SKU (cost-capped) |
|---|---|---|
| Workload web app, Region A + B | App Service (Linux, Python 3.11) | B1 plan per region |
| Workload DB + replication | Azure SQL logical server per region + **failover group** | Basic DTU database |
| Global entry + traffic switch | **Azure Traffic Manager** (DNS failover, Priority routing). **Front Door is FORBIDDEN on Free Trial subscriptions (verified 2026-10-17).** | Standard (10s fast probing, TTL 10s) |
| DR Agent console + engine | App Service (Linux, Python 3.11) in control-plane RG | B1 |
| LLM | Azure OpenAI in an Azure AI Foundry project | `gpt-4o-mini` deployment, **Standard** sku in `eastus2` (Free Trial quota: 200K TPM; GlobalStandard and gpt-4.1 quotas are 0) |
| Exercise state / timeline | Azure Storage **Tables**; reports in **Blob** | Standard LRS |
| Secrets | Key Vault (App Service Key Vault references) | Standard |
| Logs | Log Analytics + Application Insights | Pay-as-you-go |
| Email notifications | Azure Communication Services + Email Communication Service (**Azure-managed domain**, sender `DoNotReply@<id>.azurecomm.net`) | Pay-per-email (fractions of a cent) |
| Outage injection (optional) | Chaos Studio App Service "stop" fault; otherwise `az`/SDK stop | - |

Regions: Region A = `centralindia`, Region B = `southindia` (an Azure paired region). Traffic Manager is global. Azure OpenAI region = `eastus2`.

**Not allowed (cost):** VMs, Site Recovery, AKS, Firewall, App Gateway, VPN/ExpressRoute, Bastion, Premium SKUs, Defender paid plans.

## Code
- **Python 3.11**, **FastAPI**, **Jinja2** templates, vanilla JS + hand-written CSS. No frontend build step.
- Azure SDKs: `azure-identity` (DefaultAzureCredential), `azure-mgmt-web`, `azure-mgmt-sql`, `azure-mgmt-trafficmanager`, `azure-mgmt-resourcegraph`, `azure-data-tables`, `azure-storage-blob`, `azure-communication-email`.
- LLM: the `openai` package's `AzureOpenAI` client, authenticated via Entra ID token (`get_bearer_token_provider`). Use structured outputs: pydantic models + JSON schema `response_format`.
- SQL from Python: `pymssql` (bundled FreeTDS, no ODBC driver needed). Connection goes to the **failover group listener** `<fog>.database.windows.net`, so it follows failover automatically.
- Config only via environment variables (App Service app settings). Provide `.env.example`. Never commit secrets.
- Infra: **Bicep** modules + bash scripts using the `az` CLI. Must be idempotent.
- Logging: `logging` with JSON-ish one-line records. Every engine step is also written to the exercise timeline table.
- Tests: `pytest` for pure logic (RTO/RPO math, plan validation, parity diff). Mock Azure calls.
- Style: type hints, small modules, no frameworks beyond the above. Keep dependencies minimal.
