---
inclusion: always
---

# Product: Disaster Recovery (DR) Agent: Autonomous Disaster Recovery Validation

Hackathon entry by **Team Azure Armada** (Virtusa Azure Day 2026). Theme: agentic AI built **entirely on Azure**.
Build window: Sat 17 Oct to Sun 18 Oct 2026. Panel demo by 23 Oct (target 21 Oct).

## What it is
An agentic system that covers the **full DR lifecycle** for an application, in two phases:

1. **DR design & preparation:** take the app's criticality, RTO and RPO, recommend a DR strategy with a cost trade-off, check that the DR environment is ready (replication, config parity, health), and review the DR runbook against the live environment.
2. **DR exercise planning & execution:** produce an ordered exercise plan (owners, dependencies, human vs automated steps), get approvals, **inject a real outage**, **execute the failover**, **validate** the app, **measure actual RTO and RPO against the targets**, **fail back**, then report and list remediation items.

Discovery/inventory only supports the agents. It is NOT a headline feature.

## Demo story (everything must serve this)
1. "Contoso Payments" (a small, nicely styled web app) runs in Region A (Central India). It writes a heartbeat transaction every 2s.
2. The console shows Phase 1: profile (Tier 1, RTO 30 min, RPO 15 min) → recommended strategy *warm standby* with costs → readiness checks → runbook review flags a stale step.
3. Phase 2: the agent generates the exercise plan. A human step ("DB team confirms go") is approved in the console. The owner gets a console notification + an email.
4. Outage injected: the Region A app is stopped. The agent fails over the SQL failover group, switches Traffic Manager to Region B and validates.
5. The console shows **RTO 4m 12s ✅ (target 30m)** and **RPO 6s ✅ (target 15m)**. The agent fails back to Region A and publishes the report.

## Principles
- **The LLM plans and reasons; a guarded engine executes.** Every Azure-changing action is a deterministic tool call taken from an approved plan, with abort criteria.
- **Never touch anything outside the workload resource group.** No production systems exist; all of this is demo infrastructure.
- **Cost:** every member has a $200 credit, and overage is paid personally. Use the cheapest SKUs, no VMs, and keep a teardown script.
- **Comms:** notifications go to (1) the **console notification center** (bell + live pop-ups + optional browser desktop notifications) and (2) **email via Azure Communication Services** (Azure-managed sender domain). No Microsoft Teams. No Google Chat: Virtusa's Workspace admin blocks incoming webhooks. Approvals always happen in the console.
- **Measurements must be honest:** RTO and RPO are computed from recorded timestamps, never estimated by the LLM.
