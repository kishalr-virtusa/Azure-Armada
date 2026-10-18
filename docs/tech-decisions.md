# Tech stack and why (judge Q&A prep)

Team Azure Armada · Disaster Recovery (DR) Agent: Autonomous Disaster Recovery Validation

Use this to answer "what did you use, why this, why not that". Every answer is about what we actually built.

## The one-line architecture
Six AI agents on **Azure AI Foundry Agent Service** decide and explain; a **guarded engine** in the DR console executes
only allowlisted Azure actions from a validated, human-approved plan; it fails a real workload over from
**East Asia to Southeast Asia** and measures RTO/RPO from evidence.

## Decisions

| Layer | We chose | Why | Why not the alternative |
|---|---|---|---|
| Agent platform | **Azure AI Foundry Agent Service** (6 versioned prompt agents, strict JSON output schemas) | Azure-native agent hosting: agents are visible and versioned in the Foundry portal, use Entra/managed identity, and each has a contract (output schema) we validate | **Semantic Kernel / AutoGen / LangChain**: orchestration frameworks we don't need, because our orchestration is a deterministic engine by design. **Direct Azure OpenAI calls**: kept only as the automatic fallback |
| Models | **gpt-4.1-mini** for Architect, Readiness, Runbook, Reporter; **gpt-5-mini (reasoning)** for Planner and Validator | Fast and cheap with reliable structured outputs for summarising; reasoning where it matters (multi-step plans, diagnosing failures with evidence) | **GPT-4o**: retired. **Bigger models**: slower and pricier with no quality gain on our measured tasks (Validator answers in ~7s, Planner ~30s) |
| Agent safety | **"Agents decide, the engine acts"**: allowlisted actions, mandatory safe sequence, plan validation, human approval gates, kill switch, rollback, deterministic fallback per agent | DR touches production: every action must be predictable, auditable and reversible. A model can never call an Azure write API | **Letting the LLM call tools directly (function calling)**: one hallucinated tool call could fail over production. We use the LLM for judgement, code for execution |
| Output reliability | **Strict JSON schemas** + server-side validation (e.g. the Architect's recommendation is checked against the numbers) | No free-text parsing; wrong answers are rejected and retried. We found and fixed a case where the model claimed a strategy met targets it didn't | Free-text answers: unparseable and unverifiable |
| App + console hosting | **Azure App Service (Linux, Python 3.11)** | Simplest managed PaaS with zip deploy, managed identity, health checks, start/stop APIs (used to inject the outage). The workload itself is PaaS, so DR of App Service is the realistic scenario | **AKS**: cost and ops overhead for 3 small apps. **Functions**: our engine is a long-running drill thread (minutes) with in-process state. **Container Apps**: viable, but App Service gave us start/stop and slots semantics directly |
| Web framework | **FastAPI** + server-rendered Jinja + small vanilla JS | Typed, async, fast to build; no front-end build pipeline to break before a demo; Azure SDKs are first-class in Python | **React SPA**: a build step and more moving parts for no user benefit here |
| Database | **Azure SQL (Basic) + auto-failover group** | Payments are relational; a failover group gives one listener name, forced (disaster) and planned (no data loss) failover, and replication lag we can read | **Cosmos DB multi-region**: different consistency model, higher cost, not the classic enterprise DR case judges recognise |
| Global routing | **Azure Traffic Manager** (priority routing, /health probes every 10s, TTL 10s) | DNS-level failover between regions with health probes; the engine flips endpoints and follows TM's routing decision | **Azure Front Door**: forbidden on Free Trial ("Free Trial and Student account is forbidden for Azure Frontdoor resources"). **Application Gateway**: regional, not cross-region |
| IaaS DR | **Azure Site Recovery (A2A)** with **test failover** into an isolated network | The standard Azure VM DR service; test failover proves recovery without touching production; we verify by running a command inside the recovered VM | **Azure Backup restore**: recovery in hours, not minutes; no non-disruptive drill |
| VM OS | **Windows Server 2022 Core** | The ASR Linux agent supports only specific kernel builds; current Ubuntu kernels were rejected in both regions we tried | Linux: replication failed on kernel compatibility |
| Regions | **East Asia (A) ↔ Southeast Asia (B)**, console in **South India** | An official Azure paired region; the control plane sits outside both so it survives the disaster it manages | Indian pairs: Central India throttled App Service creation on our subscription; South/West India rejected new Azure SQL servers |
| Drill state | **Azure Storage Tables + Blob** | Serverless, cheap, durable; the engine resumes a drill after a console restart | Cosmos/SQL for state: unnecessary cost and coupling to the database we fail over |
| Secrets + identity | **Key Vault + managed identities** (no keys in code), Entra ID sign-in built (needs tenant consent) | Zero secrets in the repo (scanned); least-privilege role assignments | App settings with plain secrets |
| Measurement | **Heartbeat writer** (one confirmed DB write every 2s) for RPO; RTO = outage injected until healthy via Traffic Manager | Evidence-based, not estimated: we count which confirmed writes survived the forced failover | Reading replication lag alone: it estimates, it doesn't prove |
| Notifications | Live console alerts (instant) + **Azure Monitor action group** email (audit trail) | Azure-native and works on Free Trial | Azure Communication Services email: blocked on Free Trial. Google Chat/Gmail: blocked by the tenant |
| Infrastructure as code | **Bicep** + `up.sh` / `down.sh` (one command each) | Azure-native, supports new resource types on day one (Foundry projects), no state file to manage | **Terraform**: great tool, but a state backend to manage and slower support for brand-new Azure APIs |
| Chaos | Our own **allowlisted fault** (disable the DR traffic endpoint mid-failover) | Tests the exact failure an operator could cause, and ties into the Validator + human-approved fix | **Azure Chaos Studio**: roadmap; our fault needs to be coordinated with the drill engine |
| UI design | `docs/design/DESIGN.md` from awesome-design-md (Vercel discipline + Virtusa accent; Mercury for Contoso) | A written design system keeps every screen consistent | Ad-hoc styling: looks AI-generated |

## Likely follow-up questions
- **Is the outage real?** We simulate the loss of the region by stopping the app tier and forcing the database failover; everything after that (rerouting, recovery, measurement) is real.
- **Zero downtime?** No: ~30-40s disruption, then automatic recovery with RPO 0 against a 30-minute target. Zero downtime needs active-active at much higher cost.
- **What if the AI is wrong?** It cannot act. The engine rejects plans that break the safe sequence, numbers are verified by code, humans approve risky steps, and every agent has a rules-based fallback.
- **What does it cost?** About INR 8 per hour for the whole environment; ~INR 200 for two days of building and 8 drills.
- **How do you scale this to many apps?** Each app gets a profile (RTO/RPO, tier); the agents and the allowlisted actions are generic per service type (App Service, SQL, Traffic Manager, ASR). New service types = new allowlisted actions.
