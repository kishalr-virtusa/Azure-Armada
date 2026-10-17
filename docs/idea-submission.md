# Azure Day 2026 Hackathon: Idea Submission

**Team:** Azure Armada
**Use case / agent name:** **Disaster Recovery (DR) Agent: Autonomous Disaster Recovery Validation**

**In one line:** a team of AI agents built entirely on Azure that **validates disaster recovery by executing real DR tests on its own**: it fails workloads over, measures how fast they come back and how much data is lost, fails back, and proves the enterprise can recover.

---

## 1. Describe the problem you are trying to solve in detail

Most enterprises have a disaster recovery plan. Very few can prove it works today.

- **DR is tested rarely and manually.** Drills usually happen once or twice a year. They take weeks of planning, need people from infrastructure, database, application, network and business teams, and often run over weekends. Because they are expensive and disruptive, teams test as little as possible, often only a "tick-box" subset of the systems.
- **Drift silently breaks recovery between drills.** Cloud environments change daily. New resources go live without geo-replication. Storage accounts are created as LRS. Secrets, certificates and app settings exist only in the primary region. DNS or Front Door routing isn't updated. Quota or SKU capacity is missing in the secondary region. Firewall rules and private endpoints aren't mirrored. Any one of these can fail a real failover, and nobody notices until the outage.
- **Runbooks go stale.** DR runbooks live in Word documents and wikis. They describe the architecture as it was at the last drill, not as it is now. During a real incident, engineers find steps that no longer apply.
- **RTO/RPO are assumed, not measured.** The business signs off on recovery objectives (e.g. RTO 1 hr, RPO 15 min), but the actual recovery time and data loss are rarely measured end-to-end. A plan can be marked "compliant" on paper while its real RTO is unknown.
- **Compliance evidence is painful.** Regulators and auditors (RBI/SEBI in India, DORA in the EU, SOX, ISO 22301, HIPAA) increasingly require *demonstrated* operational resilience with evidence. Today that evidence is put together by hand from screenshots, spreadsheets and email trails.
- **Dependencies are invisible.** An application's recovery depends on databases, identity, Key Vault, messaging, networking and third-party endpoints. These dependencies are rarely fully mapped. A failover of one tier often breaks another.

**The result:** organisations carry hidden recovery risk. They find the gaps during a real outage, when downtime costs the most in revenue, customer trust and regulatory penalties.

---

## 2. What is your detailed proposed solution

The **Disaster Recovery (DR) Agent** is a multi-agent system built entirely on Azure. It **performs DR testing end-to-end on its own**. It does not stop at reviewing a DR design or checking settings. It actually **executes failover drills**, checks that the recovered application works, **measures real RTO/RPO**, **fails back**, cleans up, and turns the results into fixes and audit evidence. DR changes from an annual manual exercise into **continuous, autonomous and auditable recovery testing**. A human approves every step that could affect production.

### DR tests the agent executes

| Test type | What the agent does | Azure capability |
|---|---|---|
| **VM / IaaS failover test** | Fails replicated VMs over into an isolated test VNet, boots them, health-checks them, then cleans up | Azure Site Recovery *Test Failover* |
| **Database failover test** | Triggers a planned failover of the Azure SQL failover group or Cosmos DB region and measures data lag (RPO) and switch-over time (RTO) | SQL Failover Groups, Cosmos DB multi-region |
| **Storage failover test** | Starts a storage account failover (non-prod) and checks the data is readable in the secondary region | Storage customer-managed failover (GRS/GZRS) |
| **Traffic / region switch test** | Moves user traffic to the secondary region and runs synthetic user journeys | Azure Front Door / Traffic Manager |
| **Fault-injection (chaos) test** | Simulates a zone outage, network block, Key Vault denial or AKS pod failure to prove the application survives | Azure Chaos Studio |
| **Failback test** | Returns everything to the primary region and checks that nothing broke | ASR re-protect / failback, SQL failover |

**Three testing modes**, chosen per environment:
1. **Isolated test (production-safe):** recovers into an isolated network with zero impact on live users. The agent can run this weekly, unattended.
2. **Full drill (non-prod / staging):** a real region failover and failback of the complete application stack.
3. **Game day (production):** a full failover in production, only with explicit human approval, a kill switch and automatic rollback.

### Agent team (orchestrated on Azure AI Foundry Agent Service)

| Agent | What it does | Azure services used |
|---|---|---|
| **Orchestrator Agent** | Takes a goal (e.g. *"Validate DR readiness of the Payments workload"*), plans the work, hands it to specialist agents, and enforces guardrails and approvals | Azure AI Foundry Agent Service, Azure OpenAI (GPT-4.1 / o-series reasoning), Microsoft Agent Framework / Semantic Kernel |
| **Discovery Agent** | Builds a live dependency graph of the workload from resources, tags, network links and telemetry, and classifies each component by criticality | Azure Resource Graph, Azure Monitor / Application Insights app map, Azure Cosmos DB (graph/state store) |
| **Readiness Assessor Agent** | Checks the DR posture of every component against a resilience rulebook: geo-replication and failover groups (Azure SQL, Cosmos DB), GRS/GZRS storage, Azure Site Recovery replication health, backup vault status, Key Vault/secrets/certs in the secondary region, paired-region quota and SKU availability, Front Door / Traffic Manager configuration, mirrored NSGs and private endpoints | Azure Policy, Azure Business Continuity Center, Azure Site Recovery & Backup APIs, Defender for Cloud, Azure Advisor |
| **Runbook Intelligence Agent** | Reads existing DR runbooks and documents (RAG), compares them to the live architecture, flags outdated steps, and generates an up-to-date executable runbook | Azure AI Search (vector index), Azure Blob Storage, Azure AI Document Intelligence |
| **DR Test Executor Agent** *(core)* | **Runs the DR tests above:** puts the failover steps in the correct dependency order (data tier → app tier → traffic), triggers each step, watches its progress, decides whether to continue, pause or roll back, then fails back and cleans up automatically | Azure Site Recovery, Azure Chaos Studio, Azure Automation / Durable Functions, Logic Apps |
| **Validation Agent** | Checks that the recovered environment really works: synthetic transactions, health probes, data-consistency checks. Measures **actual RTO and RPO** and compares them with business targets | Azure Monitor, Log Analytics (KQL), Application Insights availability tests, Azure Load Testing |
| **Remediation & Reporting Agent** | Produces an audit-ready DR evidence report with a resilience score. Creates fix-it work items and **Bicep/Terraform pull requests** for each gap (e.g. switch storage to GZRS, add a SQL failover group). Notifies stakeholders | Azure DevOps / GitHub, Power BI, Microsoft Teams (Adaptive Cards), Azure Communication Services |

### How it works (end-to-end flow)

1. **Trigger:** on a schedule (e.g. weekly), on change (an infra deployment detected via Event Grid), or on demand from a Teams chat or the portal.
2. **Discover:** map the workload and its dependencies.
3. **Assess:** check static readiness and produce a gap list with a risk rating for each gap.
4. **Plan:** generate a drill plan from the live topology plus runbook knowledge; estimate impact and cost.
5. **Approve:** human-in-the-loop sign-off in Teams for any step beyond read-only.
6. **Execute DR test:** run the failover drill (isolated test, full drill or game day) step by step in dependency order, with a kill switch and automatic rollback.
7. **Validate and fail back:** run synthetic transactions on the recovered application, measure real RTO/RPO, verify data integrity, then fail back to the primary region and clean up.
8. **Remediate and report:** create PRs and work items for fixes, update the runbook, and publish an evidence pack and resilience score to a Power BI dashboard.
9. **Learn:** store outcomes in memory so that future drills focus on the riskiest areas.

### Responsible AI, security & governance

- **Least privilege:** each agent runs under its own Managed Identity with scoped RBAC. Write actions use just-in-time elevation (Entra PIM).
- **Human-in-the-loop:** read-only actions are autonomous. Any change or failover needs explicit approval.
- **Safe by design:** drills run in isolated networks or test failover modes. Every action is reversible, with automatic cleanup.
- **Full traceability:** every agent decision, tool call and approval is logged to Log Analytics and Application Insights (agent tracing in AI Foundry). This gives an immutable audit trail.
- **Content Safety and prompt-injection protection** on all LLM inputs. Secrets stay in Key Vault and never reach prompts.

### Hackathon MVP scope (build phase, 16–23 Oct)

We will demonstrate the agent on a sample 3-tier workload (App Service + Azure SQL + Storage + Key Vault, behind Azure Front Door) deployed across a primary and a secondary region with **deliberately planted DR gaps**. Live in the demo, the DR Agent will:

1. Discover the workload and draw its dependency map.
2. Detect the planted gaps (e.g. LRS storage, secret missing in the secondary Key Vault, stale runbook step).
3. Ask for approval in Teams, then **run a real DR test live:** fail over the SQL failover group, switch traffic to the secondary region via Front Door, and run a Chaos Studio zone-down experiment.
4. **Measure actual RTO/RPO** with synthetic transactions, and show the test *catching* a planted gap (the app fails in the secondary region because of the missing secret).
5. **Fail back** to the primary region, raise a Bicep PR that fixes the gap, re-run the test to show it now passes, and publish the DR test evidence report with a resilience score.

---

## 3. What are the business benefits for this solution?

- **From annual to continuous DR testing:** real failover tests run every week, or after every change, instead of once a year. Gaps are found within days, not during an outage.
- **Large reduction in drill effort and cost:** the planning, execution, validation and reporting that today take weeks of multi-team effort become largely automated. Engineers only review and approve.
- **Lower downtime risk and business impact:** real failover blockers (configuration drift, missing replication, stale runbooks) are fixed in advance. This directly protects revenue, SLAs and customer trust during regional outages.
- **Measured, not assumed, RTO/RPO:** leadership gets factual recovery metrics per application and can make informed decisions on resilience investment.
- **Audit-ready compliance:** evidence packs are generated automatically for regulators and auditors (RBI, SEBI, DORA, SOX, ISO 22301), which saves significant effort in audit preparation.
- **Self-healing posture:** gaps turn into ready-to-merge infrastructure-as-code fixes, which shortens the time from finding a problem to fixing it.
- **Up-to-date runbooks and knowledge:** runbooks always match the live architecture, which reduces dependence on a few key experts.
- **Safe, controlled autonomy:** human approval gates, least privilege and full traceability make it acceptable to risk-averse industries such as BFSI and healthcare.
- **Strategic value for Virtusa:** a reusable accelerator for Virtusa's cloud migration, managed services and BFSI clients. It can be packaged as a "Resilience-as-a-Service" offering and drives more Azure consumption (ASR, Chaos Studio, AI Foundry).

---

## 4. Proposed Azure Architecture (summary for diagram)

```
                         ┌──────────────────────────────────────────────┐
  Users / SRE / Auditor  │  Microsoft Teams bot  ·  Web portal (App Svc) │
  ─────────────────────► │  Power BI resilience dashboard                │
                         └───────────────┬──────────────────────────────┘
                                         │ (Entra ID auth)
                         ┌───────────────▼──────────────────────────────┐
                         │   Azure AI Foundry Agent Service              │
  Triggers:              │   ┌────────────────────────────────────────┐  │
  • Schedule (Logic App) │   │        Orchestrator Agent              │  │
  • Event Grid (deploys) ├──►│  (Azure OpenAI GPT-4.1 / o-series)     │  │
  • On-demand            │   └──┬─────┬──────┬───────┬───────┬──────┬─┘  │
                         │  Discovery Readiness Runbook Drill  Valid- Remediate│
                         │   Agent    Agent     Agent  Exec   ation  & Report │
                         └─────┬────────┬────────┬──────┬──────┬───────┬─────┘
                               │        │        │      │      │       │
       Azure Resource Graph ◄──┘        │        │      │      │       └─► Azure DevOps/GitHub (Bicep PRs)
       Azure Policy / Defender / BCC ◄──┘        │      │      │           Teams / ACS notifications
       Azure AI Search + Blob (runbooks RAG) ◄───┘      │      │           Power BI evidence report
       Azure Site Recovery · Chaos Studio ·  ◄──────────┘      │
       Automation / Durable Functions                         │
       Azure Monitor · Log Analytics · App Insights ◄─────────┘
       · Azure Load Testing

  Cross-cutting: Managed Identities + RBAC + Entra PIM · Key Vault · Cosmos DB (agent state & memory)
                 AI Content Safety · Agent tracing to App Insights · Isolated test VNet

  Target workload under test:  Region A (primary) ──replication──► Region B (secondary)
                               App Service · Azure SQL · Storage · Key Vault · Front Door
```
