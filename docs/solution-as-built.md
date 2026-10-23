# Azure Day 2026 Hackathon: Solution as Built

**Team:** Azure Armada (Kishal R, Sadugula Kumar, Arvind G, Chevvu Sushma, Manu CK)
**Use case:** **Disaster Recovery (DR) Agent: Autonomous Disaster Recovery Validation**
**Tagline:** *Don't hope your DR works. Prove it, every day.*

**In one line:** an agentic AI system, built 100% on Azure, that **continuously validates disaster recovery by running real failover drills**. It takes a region down for real, fails the application over, measures recovery time and data loss write by write, diagnoses failures mid-drill, fails back, and hands auditors the evidence. Humans approve only the risky moments.

This document describes the solution **as built and running** (21 Oct 2026). The idea we submitted on 15 Oct is in [idea-submission.md](idea-submission.md); section 6 lists what changed and why.

---

## 1. Which use case are you selecting?

**Disaster Recovery (DR) Agent: Autonomous Disaster Recovery Validation.**

Scope: **validation**, not DR design. DR comes in many patterns (backup & restore, pilot light, warm standby, active-passive, active-active). Our validator is pattern-aware; we have built and proven it on **warm standby**, the most common enterprise pattern, and adding a pattern means adding engine actions, not a redesign.

**Where it runs:** full drills in staging or isolated test networks; production gets continuous read-only validation. Enterprises do not take production down casually, and neither does DR Sentinel: the readiness checks are read-only and safe to run against production every 10 minutes; full failover drills run in a staging copy or in an isolated test network (as an Azure Site Recovery test failover does), and a production "game day" happens only when the business plans and approves it. Our demo runs a full drill on a test workload.

---

## 2. Describe the problem you are trying to solve in detail

Every enterprise has a DR plan. Very few can prove, on an ordinary Tuesday, that it still works.

- **DR is proven only in manual weekend drills, once or twice a year.** Infrastructure, database, application and network teams join a war-room call, click through a runbook, and collect screenshots as evidence. Because drills are expensive and disruptive, they are rare and often only cover part of the estate.
- **Between drills, recovery silently breaks.** Cloud environments change daily: an app setting exists only in the primary region, a Traffic Manager endpoint is left disabled, replication lags, a VM stops replicating. Nobody notices until the outage. It is like an antique car kept shiny all year that does not start on parade day.
- **Runbooks go stale.** They describe the architecture as it was at the last drill, not as it is now.
- **RTO and RPO are assumed, not measured.** The business signs off on targets, but real recovery time and data loss are rarely measured end to end, and almost never write by write.
- **Evidence for regulators is assembled by hand.** Regulators act on weak recovery, not just breaches: after repeated outages the RBI froze HDFC Bank's new digital launches for about 15 months (Dec 2020 – Mar 2022) and restricted Kotak Mahindra Bank's online onboarding for about 10 months (Apr 2024 – Feb 2025), citing business-continuity and DR gaps.
- **Downtime is expensive and the budget is small.** Downtime costs Global 2000 companies about **$15,000 a minute** (Splunk & Oxford Economics, *The Hidden Costs of Downtime*, 2026). At **99.99%** availability the whole year's downtime budget is **52.6 minutes**: one untested 30-minute manual failover uses most of it, and there is no room for a second attempt.

**The result:** recovery is assumed, not proven, and organisations discover the gaps during a real outage, when it costs the most.

---

## 3. What is your detailed proposed solution (as built)

**DR Sentinel** turns DR validation from a manual weekend drill into a **continuous, autonomous and auditable** process, in three layers:

1. **Always on: continuous readiness.** Every 10 minutes, **Autopilot** checks 7 readiness signals, one for each component the failover depends on:
   - SQL failover group health and replication lag
   - health of both regional apps
   - both Traffic Manager endpoints
   - app-settings parity between regions (configuration drift)
   - Key Vault
   - Site Recovery replication of the reports VM

   The checks are deterministic code. When something newly fails, the **Readiness agent** investigates with read-only tools, explains the root cause and proposes a fix. Depending on the autonomy level (observe / propose / auto-fix), a human approves or the safe fix is applied.
2. **On demand: real drills.** The **Planner agent** writes the drill plan. Code validates it against hard safety rules: 15 allowed actions, a mandatory order, and human gates before the outage and before failback. A human approves, and a deterministic **engine** executes it on real Azure:
   - stop the primary-region app (a real outage)
   - force the SQL failover group to the DR region
   - switch Traffic Manager
   - wait until customers are really served from the DR region (this stops the RTO clock)
   - run a real payment test
   - measure data loss
   - fail back with no data loss
   - optionally, a Site Recovery test failover of an IaaS server, with the recovered VM proving it produces the daily payments report

   A drill is **blocked unless the latest readiness result is a recent GO**. Full drills run in staging or isolated test networks; production gets continuous read-only validation (layer 1).
3. **Afterwards: evidence.** RTO and RPO are **measured by code**: every second of RTO is accounted for in segments (failover, DNS, Validator diagnosis, waiting for human approval…), and every acknowledged write is checked against the new primary. The **Reporter agent** writes the narrative, which is checked against the measurements, and the **Black box** answers any audit question about any past drill with citations.

**Design principle: agents decide, the engine acts.** AI agents plan, diagnose and explain. A deterministic engine with a fixed set of 15 allowed actions is the only thing that changes Azure, and humans approve the risky moments. The worst outcome of a wrong or manipulated model is a rejected suggestion, never a bad action in production.

### The seven agents (Azure AI Foundry Agent Service)

| Agent | Role | Tools / inputs |
|---|---|---|
| **Architect** | Checks that the DR design still meets RTO/RPO targets, scoring 5 DR patterns at real Azure prices (Retail Prices API); recommends the cheapest one that meets the targets and never upsells | Profile, pricing |
| **Readiness** | GO / NO-GO from the 7 checks; investigates failures | 5 read-only tools: probe, Traffic Manager, SQL failover group, app-settings diff, Site Recovery |
| **Runbook reviewer** | Compares the written DR runbook with the live inventory; flags stale or missing steps and writes an updated runbook | Runbook, live inventory |
| **Planner** | Writes the 14–22-step drill plan; validated by code against safety rules | Profile, strategy, readiness, inventory |
| **Validator** | Root-causes a failed step mid-drill and proposes an allowlisted fix (a human approves) | 5 read-only tools: probe, Traffic Manager, web app, SQL failover group, settings diff |
| **Reporter** | Writes the drill report and compares it with previous drills; every figure is verified against the measurements | Measurements, timeline, history |
| **Black box** | "Flight recorder" chat: answers questions about past drills only from the records, with citations | 5 exact record tools + **RAG** (`search_knowledge`, Cosmos DB vector search) |

### Production-grade agent engineering
- **Versioned and pinned agents** in Azure AI Foundry; each result records the exact agent version and model (GPT-4.1-mini; GPT-5-mini for the Planner).
- **Bounded calls:** per-agent deadlines, retry on throttling, a tool-call cap, strict JSON schemas, one repair round, then a **fallback chain** (Foundry agent → direct model → deterministic rules), so the console never breaks because AI is down.
- **Grounding verification:** code checks every number, time, drill id and citation in Black box, Reporter and Runbook answers against the records.
- **Azure AI Content Safety, Prompt Shields:** jailbreak and indirect-attack (prompt-injection) blocking on both model deployments.
- **Observability:** OpenTelemetry GenAI spans to Application Insights, with **$ cost per agent call**, a Foundry Tracing connection and an "Agent operations" workbook.
- **Evaluation harness:** golden sets built from real drills (73 cases × 3 repetitions, including 8 prompt-injection attacks), code graders plus the Azure AI Evaluation Groundedness evaluator, and pass/fail gates. Latest: Planner 100%, Validator 90%, Readiness 92%; Black box numeric exactness 86% → 97% and successful injections 6/24 → 3/24 after hardening (the gate is zero; open item, mitigated because agents cannot act).

### What we measured on our Azure environment (19–21 Oct 2026)

| | Result |
|---|---|
| Recovery time (RTO), plain drills | **28–41 s** (target 30 min) |
| Data loss (RPO) | **~1.6–2 s**, every acknowledged write checked (e.g. 10 of 119 writes lost, timestamped) |
| Chaos drill (surprise fault: DR endpoint disabled) | Validator root cause in **10 s**; recovered in **63 s**; failback 2 min 07 s; whole drill ~6 min |
| AI cost per drill | **~$0.01** in tokens (measured in Application Insights) |
| Readiness | Re-checked every **10 min** by Autopilot |

### Demo workload
**Contoso Payments**, a payments web app customers use from their phones:
- Azure App Service in **East Asia** (primary) and **Southeast Asia** (warm standby), behind **Traffic Manager**.
- **Azure SQL** in a failover group.
- A Windows **reports VM** protected by **Azure Site Recovery**.

The console's **Live map** shows real customer payments as green dots moving through the regions while the drill runs.

---

## 4. What are the business benefits for this solution?

- **From "assumed" to "proven", every day:** readiness is checked every 10 minutes and a full drill can run any day in ~6 minutes, instead of once or twice a year.
- **Measured, audit-ready evidence:** timestamped RTO/RPO, write-by-write data loss, verified reports, Azure Activity-log traceability and a Black box that answers auditors' questions with citations. This matters for regulators who act on weak recovery (RBI, SEBI, DORA).
- **Drift caught before the outage:** configuration drift, disabled endpoints and replication problems are found within minutes, explained, and fixed with one approval.
- **Less war-room effort:** a manual drill (≈2 drills a year × 5 engineers × 8 hours ≈ 80 engineer-hours per critical app per year, *estimated*) becomes ~6 minutes with 2 approvals.
- **Faster diagnosis:** the Validator finds the root cause of a failed step in seconds, with evidence, instead of a war-room triage.
- **Cost transparency:** the Architect agent validates that the DR design still meets targets at real Azure prices, and never upsells. AI cost is about a cent per drill.
- **Safe autonomy for regulated industries:** agents cannot change production directly, humans approve the risky gates, all identities are managed (no keys), secrets stay in Key Vault, and every action is logged.
- **Reusable for Virtusa:** a resilience-validation accelerator for BFSI and managed-services clients that drives Azure consumption.

---

## 5. Azure architecture (as built)

```
 Customers' phones ──► Traffic Manager (priority routing, HTTPS health probes, TTL 10 s)
                          │ priority 1                          │ priority 2 (standby)
            ┌─────────────▼───────────────┐      ┌──────────────▼───────────────┐
            │ REGION A · East Asia         │      │ REGION B · Southeast Asia     │
            │ App Service (payments app)   │      │ App Service (warm standby)    │
            │ Azure SQL (primary)  ──── failover group / geo-replication ──► SQL │
            │ Reports VM (Windows) ──── Azure Site Recovery ──────────► replica  │
            └──────────────▲───────────────┘      └──────────────▲───────────────┘
                           │  engine actions via Azure Resource Manager (managed identity,
                           │  Contributor on the workload resource group only)
 ┌─────────────────────────┴──────────────────────────────────────────────────────────┐
 │ DR CONSOLE · App Service, South India (FastAPI)                                     │
 │  Engine (15 allowed actions, human gates, checkpoints) · Autopilot (every 10 min)   │
 │  Live map · Plan · Live exercise · Results · Black box                              │
 └───────┬──────────────────────────┬───────────────────────────────┬──────────────────┘
         │ Responses API (Entra ID) │                               │
 ┌───────▼────────────────────┐ ┌───▼──────────────────────────┐ ┌──▼──────────────────────────┐
 │ Azure AI Foundry · East US 2│ │ Cosmos DB (free tier,        │ │ Key Vault · Blob (reports) │
 │ 7 versioned agents          │ │ Central India + SEA replica) │ │ App Insights + Log Analytics│
 │ GPT-4.1-mini · GPT-5-mini   │ │ records · conversations ·    │ │ (OpenTelemetry, $ per call, │
 │ Prompt Shields              │ │ knowledge vectors (RAG)      │ │  Agent operations workbook) │
 │ text-embedding-3-small      │ └──────────────────────────────┘ └─────────────────────────────┘
 └─────────────────────────────┘
 Infrastructure as code: Bicep (infra/), one-command up/down (infra/scripts/up.sh, down.sh)
```

**Architecture diagram for the form:** slide 4 of our deck *DR-Agent-Pitch-AzureDay26* ("Architecture: Agents Decide, the Engine Acts"). Share the deck link with view access.

---

## 6. Changes from the submitted idea, and why

| Idea (15 Oct) | Built | Why |
|---|---|---|
| Orchestrator **agent** directs the specialist agents | A deterministic **engine** orchestrates; agents plan, diagnose and explain | Safety: a model can be wrong or manipulated, so only fixed, tested code changes Azure ("agents decide, engine acts") |
| DR Test Executor **agent** | **Planner agent** writes the plan; the **engine** executes it | Same reason; it also makes the drill repeatable and auditable |
| Discovery agent (Resource Graph dependency map) | Dropped; a live inventory snapshot per drill | Our manager's guidance: discovery is supporting, not the core of validation |
| Azure Front Door | **Traffic Manager** | Simpler and cheaper DNS failover for two regional App Services; same validation story |
| Chaos Studio experiments | **Own surprise-fault injection** (disables the DR endpoint mid-drill) | Targets the exact failure that breaks recovery, and tests the Validator live |
| Approvals in Microsoft Teams | Approvals in the **console** | Fewer moving parts for the build window; Teams integration is on the roadmap |
| Bicep pull requests for fixes, Power BI resilience score | **Allowlisted one-click fixes** with approval; measured **RTO/RPO + PASS/FAIL** and an HTML report | Measured evidence over a synthetic score; fixes apply directly and safely |
| AI Search for runbook RAG | **Cosmos DB vector search** (RAG) for the Black box | One data store instead of two; the knowledge sits next to the records |
| — | **New:** Architect agent, Black box agent, Autopilot, readiness gate, Live map with real payments, evaluations, grounding, Prompt Shields, OpenTelemetry cost tracking | Production-grade agent engineering and a demo that judges can feel |

**Not built yet (roadmap):**
- Next 30 days: more DR patterns (pilot light, active-active); "realistic mode" drills that also time detection and the human decision; a cyber-recovery drill (point-in-time restore).
- Days 30–90: multi-application estates; scheduled drills from CI/CD; ITSM and Teams integration; knowledge-graph RAG at scale.

**Repository:** https://github.com/kishalr-virtusa/Azure-Armada
