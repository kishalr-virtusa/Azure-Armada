# Implementation Plan: DR Agent

Owner tags:
- **[Kiro]** bulk implementation
- **[Claude]** agent logic, failover-critical code, review
- **[Team]** human/Azure-portal actions

Execute in order within each track. Tracks A and B can run in parallel. Reference the requirements with (R#).

## Track 0: Setup (Sat morning)
- [ ] 0.1 [Team] Use kishalr's own subscription (team is off Sat/Sun). `az login`, `az account set`. Register providers: Microsoft.Web, Microsoft.Sql, Microsoft.Cdn, Microsoft.Storage, Microsoft.KeyVault, Microsoft.CognitiveServices, Microsoft.OperationalInsights, Microsoft.Insights.
- [x] 0.2 [Team] ~~Google Chat webhook~~: blocked by the Workspace admin. Replaced by console notifications + ACS email.
- [ ] 0.3 [Kiro] Repo hygiene: `.gitignore` (Python, .env, .venv, __pycache__, *.zip), `.env.example` for both apps, root `README.md` with quickstart. (R2)

## Track A: Infrastructure
- [ ] A1 [Kiro] `infra/modules/log.bicep`, `keyvault.bicep`, `storage.bicep` per design §3. (R2.1, R2.7)
- [ ] A2 [Kiro] `infra/modules/sql.bicep`: two servers, `paymentsdb` Basic, failover group with Manual policy, firewall allow Azure. Output the listener FQDN. (R2.2)
- [ ] A3 [Kiro] `infra/modules/webapp.bicep` (reusable per region; settings per design §3, Key Vault references). (R2.1)
- [ ] A4 [Kiro] `infra/modules/trafficmanager.bicep`: Priority profile, TTL 10, HTTPS monitor `/health` every 10s, Azure endpoints A (prio 1) / B (prio 2), plus the `<tm>.trafficmanager.net` hostname binding on both web apps (after the endpoints exist; see design §3). (R2.3)
- [ ] A5b [Kiro] `infra/modules/email.bicep`: Communication Services (dataLocation `India`) + Email Communication Service + **AzureManagedDomain**, linked to the Communication Services resource. Output the sender address `DoNotReply@<domain>`. Store the ACS connection string in Key Vault as `acs-connection-string`. Console app settings: `ACS_SENDER`, `NOTIFY_EMAIL_TO` (set at deploy time from an env var, never committed).
- [ ] A5 [Kiro] `infra/modules/openai.bicep` (optional via `useExistingOpenAI`), `console.bicep`, `roles.bicep` (console MI: Contributor on the workload RG only + data roles + OpenAI User). (R2.4)
- [ ] A6 [Kiro] (budget.sh already done by Claude; do not change it) `infra/main.bicep` + `parameters.json`. Scripts: `deploy.sh`, `deploy-apps.sh`, `seed-secrets.sh`, `budget.sh`, `teardown.sh`, `smoke.sh`. (R2)
- [ ] A7 [Team+Claude] Run `budget.sh` FIRST (hard rule), then `seed-secrets.sh`, `deploy.sh`. Fix deployment errors. Confirm the Traffic Manager URL serves Region A.
- [ ] A8 [Team+Claude] **Manual failover rehearsal** with `az` (stop A → forced FOG failover to B → disable TM endpoint A → app works via Traffic Manager → failback). Note the real timings. This de-risks Sunday.

## Track B: Demo workload app
- [ ] B1 [Kiro] `apps/demo-app`: FastAPI app, `db.py` (pymssql via FOG listener, table bootstrap), `/`, `/health`, `/api/txn`, `/api/recent`. (R1)
- [ ] B2 [Kiro] Template + CSS: polished single page, big region pill, status dot, outage banner on fetch failure, "Make payment" button, recent-transactions table with fade-in. Projector-friendly. (R1.1)
- [ ] B3 [Kiro] `startup.sh` (`gunicorn -k uvicorn.workers.UvicornWorker app.main:app`), `requirements.txt`. Test locally against the deployed DB, or via a docker SQL Edge container if no DB is ready yet. (R1.5)

## Track C: DR Console: foundations
- [ ] C1 [Kiro] Skeleton `apps/dr-console`: `config.py` (env), `models.py` is ALREADY WRITTEN by Claude (do not modify; import from `dr_agent.models`), `store.py` (Tables/Blob), passcode auth, base layout + CSS (design §4.7). (R9)
- [ ] C2 [Kiro] Tools: `azure_webapp.py`, `azure_trafficmanager.py`, `probes.py`, `notify.py`, `inventory.py` per design §4.5. Each has a tiny CLI `python -m dr_agent.tools.<x> --help` for manual testing. (R3, R5.6)
- [x] C3 [Claude] `azure_sql.py` (forced/planned failover on the correct server, FOG status) + `heartbeat.py` + `engine/measurements.py` + unit tests. (R5.6, R6)
- [x] C4 [Claude] `llm.py` (AzureOpenAI with Entra token provider, structured output helper, 20s timeout, fallback hook).

## Track D: Agents & engine
- [ ] D1 [Claude] Agent prompts (`agents/prompts/*.md`) + Architect, Readiness, Runbook agents with fallbacks. (R3)
- [ ] D2 [Claude] Planner agent + `default_plan.json` + plan validator (DAG, allowlist, mandatory order) + tests. (R4)
- [ ] D3 [Claude] `engine/executor.py` + `engine/orchestrator.py`: step loop, human-step pause + notify (console + email), approvals, kill switch, retry/rollback, resume after restart, timestamps t0 / t_failover / t_recovered / t_failback_done. (R5, R7)
- [ ] D4 [Claude] Validator (diagnosis) + Reporter agent + HTML report template (Kiro may style it). (R8)
- [ ] D5 [Kiro] `docs/runbooks/payments-dr-runbook.md`: a realistic 12-step runbook with **2 deliberately stale steps**: it references a "Front Door profile fd-payments" (we actually use Traffic Manager) and an old server name `sql-payments-old`. (R3.4)

## Track E: Console UI
- [ ] E1 [Kiro] Overview page: live cards for Region A, Region B, Traffic Manager, FOG role (poll `/api/status`). (R9)
- [ ] E2 [Kiro] Phase 1 page: profile form, Strategy (comparison table + recommended badge), Readiness (check list with pass/warn/fail chips + evidence expanders), Runbook review (step verdict table + updated runbook). (R3)
- [ ] E3 [Kiro] Plan page: steps table grouped by phase (owner chip, human/automated icon, depends_on), "Approve & start" with name input. (R4.3, R5.1)
- [ ] E4 [Kiro] Live exercise page: vertical timeline (polling events), current-step pulse, "Confirm done" buttons for human steps, kill switch, live RTO stopwatch, embedded iframe or mini-status of the demo app via Traffic Manager. (R5, R9)
- [ ] E6 [Kiro] Notification center: bell icon in the header with an unread count, a dropdown list, pop-ups for new `action`/`success`/`failure` items, and an opt-in browser desktop notification (Notification API). Polls `/api/notifications?after=n`. (R5.2)
- [ ] E5 [Kiro] Results page: big RTO/RPO tiles vs targets (✅/❌), evidence table, failback time, link to the report, remediation list. (R6.4, R8)

## Track F: Integration & demo (Sun)
- [ ] F1 [Claude] Wire the end-to-end flow on Azure; fix integration bugs.
- [ ] F2 [Team] Full exercise run #1 → fix → run #2 clean. Record the screen of a clean run (backup video).
- [ ] F3 [Kiro] README: architecture, how to deploy, how to run the demo, cost notes, teardown.
- [ ] F4 [Claude] Update the deck, diagram and idea doc to the final two-phase design (Traffic Manager, console + email notifications, no Teams/Google Chat, no VMs/Site Recovery).
- [ ] F5 [Team] Run `teardown.sh` when idle; redeploy before the demo (keep FOG seeding time in mind, ~10 min).

## Stretch (only if everything above is green)
- [ ] S1 Parity-gap scenario: a missing app setting in Region B is caught by Readiness; the agent proposes the fix and applies it after approval. (R8.3)
- [ ] S2 Chaos Studio App Service stop fault as the outage injector instead of an SDK stop.
- [ ] S3 Entra ID sign-in for the console.
