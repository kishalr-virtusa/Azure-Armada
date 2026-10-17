# DR Agent: Autonomous Disaster Recovery Validation

**Hackathon entry by Team Azure Armada** — Virtusa Azure Day 2026

An agentic AI system that validates disaster recovery for Azure applications by running complete DR exercises: automated failover, measured RTO/RPO, and evidence-backed reporting.

## Quick Start

### Prerequisites

- Azure CLI installed and logged in (`az login`)
- Python 3.11+
- A subscription with the required resource providers registered

### 1. Set Budget Alert (Required First!)

```bash
cd infra/scripts
./budget.sh
```

### 2. Seed Secrets

Create a `.env` file with your secrets:

```bash
export SQL_ADMIN_PASSWORD="<strong-password>"
export CONSOLE_PASSCODE="<passcode>"
# ACS connection string is generated during deployment
```

Then run:

```bash
./seed-secrets.sh
```

### 3. Deploy Infrastructure

```bash
./deploy.sh
```

This creates:
- **rg-dra-workload**: Demo app (Region A + B), SQL failover group, Traffic Manager
- **rg-dra-control**: DR Console, Storage, Key Vault, Azure OpenAI

### 4. Access the Demo

- **Traffic Manager URL**: `http://dra-tm-<suffix>.trafficmanager.net` — Contoso Payments app
- **DR Console**: `https://azure-armada.azurewebsites.net` (phones: `/pay`) — Exercise control

## Architecture

```
Traffic Manager (Priority routing, TTL 10s)
  ├─ Region A (East Asia)      ─── App Service B1 ─┐
  └─ Region B (Southeast Asia) ─── App Service B1 ─┤─ SQL Failover Group
                                                  │   └─ paymentsdb (Basic)
DR Console (Control RG)                           │
  ├─ LLM Agents (Azure OpenAI gpt-4o-mini)        │
  ├─ Engine (orchestrator + executor)             │
  └─ Storage Tables (exercises, events, heartbeats)
```

## Demo Flow

1. **Phase 1 — Preparation**
   - Profile the app (Tier 1, RTO 30 min, RPO 15 min)
   - Architect agent recommends warm-standby strategy
   - Readiness checks: replication, parity, health
   - Runbook review flags stale steps

2. **Phase 2 — Exercise**
   - Generate and approve the exercise plan
   - Inject outage (stop Region A)
   - Forced SQL failover to Region B
   - Switch Traffic Manager
   - Validate and measure RTO/RPO
   - Failback to Region A
   - Generate report with remediation items

## Cost Management

- All resources use Basic/B1 SKUs
- Run `./teardown.sh` when idle
- Budget alerts at $50, $100, $150

## Teardown

```bash
cd infra/scripts
./teardown.sh
```

## Project Structure

```
apps/
  demo-app/          Contoso Payments (FastAPI)
  dr-console/        DR Agent console + engine
infra/
  main.bicep         Subscription-scope deployment
  modules/           Bicep modules for each resource type
  scripts/           Deploy, teardown, budget scripts
docs/
  runbooks/          DR runbook (intentionally stale for demo)
  architecture/      Architecture diagrams
  design/            UI reference styling
```

## Team

**Azure Armada** — Virtusa Azure Day 2026 Hackathon
