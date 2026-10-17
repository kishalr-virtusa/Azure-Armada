# Design: DR Agent

## 1. Architecture

```
                 Email (Azure Communication Services) + console notification center
                                   ▲
 Presenter ──► DR Console (App Service B1, rg-dra-control, managed identity)
               ├─ web/ (FastAPI + Jinja, polling every 2s)
               ├─ engine/orchestrator  ── phase state machine, one exercise at a time
               ├─ engine/executor      ── runs plan steps via tools (allowlist)
               ├─ agents/*             ── Azure OpenAI gpt-4o-mini (AI Foundry), structured outputs
               └─ tools/*              ── Azure SDK (web, sql, cdn/afd, resource graph), probes, heartbeat
                     │ Contributor on rg-dra-workload only
                     ▼
 Traffic Manager (Priority routing, DNS TTL 10s, HTTP, probe /health every 10s)
   endpoints:   A (prio 1) ── App Service B1 centralindia  ─┐
                 B (prio 2) ── App Service B1 southindia    ─┤─ SQL failover group listener
   SQL server A (primary) ═══ geo-replication ═══► SQL server B (secondary)   paymentsdb Basic
 Control store: Storage Tables (exercises, events, heartbeats) + Blob (reports)
```

**Design principle:** *agents decide and explain, the engine acts.* Agents return pydantic-validated JSON. Only the executor calls Azure write APIs, and only for allowlisted actions in an approved plan.

**Demo safety:** every agent has a deterministic fallback (a template output). If an LLM call fails or times out (20s), the demo continues.

## 2. Workload app (apps/demo-app)
- `app/db.py`: `pymssql.connect(server=FOG_LISTENER, user, password, database, login_timeout=5, timeout=5)`. Opens a new connection per request; there's no pool to go stale across a failover.
- Tables (created on startup if missing):
  - `transactions(id INT IDENTITY PK, amount DECIMAL(10,2), created_at DATETIME2 DEFAULT SYSUTCDATETIME(), region NVARCHAR(32))`
  - `heartbeat(seq BIGINT PK, client_ts DATETIME2, written_via NVARCHAR(32))`. Written only by the engine.
- `/health`:
  - `SELECT 1`, then `INSERT` and `DELETE` a probe row in `transactions`, inside a single transaction
  - returns `{status:"ok", region, db_ok, db_server: @@SERVERNAME, time}`
- UI: one Jinja page with a card layout.
  - Header: "Contoso Payments", a big region pill (blue for Central India, purple for South India), a live status dot.
  - Body: a "Make payment" button (POST /api/txn) and a recent-transactions table.
  - Uses `fetch` polling every 3s. If a fetch fails, it shows a red "Service unavailable" banner, which is very visible during the outage.

## 3. Infrastructure (infra/)
`main.bicep` (subscription scope) creates `rg-dra-workload` and `rg-dra-control`, then these modules:
- `log.bicep`: Log Analytics workspace + App Insights (shared).
- `keyvault.bicep`: Key Vault in the control RG (RBAC mode). Secrets: `sql-admin-password`, `console-passcode`, `acs-connection-string`. They are seeded by `seed-secrets.sh` from env vars, never stored in the repo.
- `sql.bicep`:
  - server A (centralindia) + server B (southindia), Entra admin + SQL admin
  - `paymentsdb` Basic on A
  - failover group `dra-fog-<suffix>`, with partner B, read-write policy **Manual**, containing `paymentsdb` (geo-secondary created by the FOG)
  - firewall: allow Azure services
- `webapp.bicep` (called twice): Linux plan B1 + web app Python 3.11.
  - App settings: `REGION_NAME`, `SQL_SERVER=<fog>.database.windows.net`, `SQL_DB`, `SQL_USER`, `SQL_PASSWORD=@Microsoft.KeyVault(...)`, `SCM_DO_BUILD_DURING_DEPLOYMENT=true`
  - `alwaysOn=true`, healthCheckPath `/health`, system identity + Key Vault Secrets User role.
- `trafficmanager.bicep`:
  - profile `dra-tm-<suffix>`, trafficRoutingMethod **Priority**, DNS TTL **10**
  - monitor: protocol **HTTPS**, port 443, path `/health`, intervalInSeconds **10**, timeoutInSeconds 5, toleratedNumberOfFailures **1**
  - Azure endpoints (type `azureEndpoints`, targetResourceId = web app) A priority 1, B priority 2
  - **Host header gotcha:** users reach the app at `http://<tm>.trafficmanager.net`.
    - Each web app MUST have a `hostNameBindings` entry for `<tm>.trafficmanager.net`. Create it after the TM profile + endpoints exist, because Azure validates that the app is a TM endpoint.
    - Users use plain HTTP via TM, so set `httpsOnly=false` on the workload apps. There's no TLS cert for trafficmanager.net; this is acceptable for the demo.
    - Probes and the console can also use HTTPS on each app's `*.azurewebsites.net` host directly.
  - - `storage.bicep`: control storage account, tables `exercises`, `events`, `heartbeats`, `notifications`, blob container `reports`.
- `console.bicep`: plan B1 + web app (control RG), app settings for every endpoint/name the console needs (see `config.py`), system identity.
- `roles.bicep`: console identity → Contributor on `rg-dra-workload`, Storage Table Data Contributor + Storage Blob Data Contributor on the control storage, Key Vault Secrets User, and **Cognitive Services OpenAI User** on the OpenAI resource.

**Azure OpenAI:** `openai.bicep` creates an AI Foundry/AI Services account in `OPENAI_LOCATION` (parameter, default `eastus2`) with deployment `gpt-4o-mini`, sku GlobalStandard, capacity 30. If it already exists, the parameter `useExistingOpenAI` skips it.

Scripts:
- `deploy.sh`: `az deployment sub create`, then `deploy-apps.sh`, which zip-deploys both apps via `az webapp deploy --type zip`.
- `budget.sh`, `teardown.sh`, `seed-secrets.sh`.
- Everything prints the outputs (Traffic Manager URL, console URL).

## 4. DR Console (apps/dr-console)

### 4.1 Data model (Storage Tables; PartitionKey = exercise_id)
- `exercises`: RowKey `meta`. Fields: status (draft, planned, approved, running, paused, failed, rolled_back, completed), profile_json, strategy_json, readiness_json, runbook_json, plan_json, approvals_json, t0, t_failover, t_recovered, t_failback_done, rto_s, rpo_s, report_url.
- `events`: RowKey = zero-padded sequence number. Fields: ts, step_id, kind (info, step_start, step_end, approval, error, measurement), message, data_json.
- `heartbeats`: RowKey = seq. Fields: client_ts, acked (bool), error.

### 4.2 Pydantic models (dr_agent/models.py)
- `AppProfile{name, tier:int, rto_min:int, rpo_min:int, monthly_budget_usd:int, notes}`
- `StrategyOption{name, expected_rto_min, expected_rpo_min, relative_cost:float, meets_targets:bool, note}`
- `StrategyRec{recommended, options:list[StrategyOption], rationale, design_mapping:list[str]}`
- `Check{name, status:"pass|warn|fail", evidence:dict, detail}`
- `ReadinessReport{checks:list[Check], go:bool, summary}`
- `RunbookStepReview{step_no, text, verdict:"valid|stale|missing", reason, suggested}`
- `RunbookReview{steps, updated_runbook_md}`
- `PlanStep{id, phase, title, owner_team, type:"human|automated", action:str|None, params:dict, depends_on:list[str], success_criteria, abort_on_failure:bool}`
- `ExercisePlan{steps:list[PlanStep], assumptions:list[str]}`
- `Diagnosis{likely_cause, evidence, recommended_action:"retry|rollback|continue"}`
- `Measurement{t0, t_failover, t_recovered, rto_s, rpo_s, rto_target_s, rpo_target_s, rto_met, rpo_met, evidence}`
- `ExerciseReport{html, summary_text, remediation:list[{item, owner, priority}]}`

### 4.3 Agents (dr_agent/agents/*.py, prompts in prompts/*.md)
Each agent is a function: `run(inputs) -> Model`.
- It calls `llm.structured(system_prompt, user_payload_json, Model)` with temperature 0.2.
- On exception or timeout it returns `fallback(inputs)`.

| Agent | Input | Output | Fallback |
|---|---|---|---|
| Architect | AppProfile + inventory summary | StrategyRec | Rules table: tier0 → active-active, tier1 → warm-standby, tier2 → pilot-light, tier3 → backup-restore |
| Readiness | Raw check results (computed by tools) | ReadinessReport (summary + go) | go = no `fail` |
| Runbook | Runbook markdown + inventory | RunbookReview | Mark steps that mention unknown resource names as stale |
| Planner | Profile + strategy + readiness + inventory + tool allowlist + mandatory sequence | ExercisePlan | `default_plan.json` (canonical 14-step plan, below) |
| Validator | Failed step + recent events + probe results | Diagnosis | `rollback` |
| Reporter | Exercise meta + events + measurement | ExerciseReport | Jinja report template filled with data |

Prompt rules (all agents):
- Never invent resource names; use only the inventory.
- Be concise.
- The Planner MUST use only allowlisted actions and must follow the mandatory sequence.

### 4.4 Canonical plan (default_plan.json; the Planner's output should resemble it)
1. pre-1 (automated, dr-agent): `snapshot_inventory`
2. pre-2 (automated, db): readiness re-check: FOG replication healthy (`check_fog`)
3. pre-3 (human, db): "DB team confirms go for forced failover"
4. pre-4 (automated, dr-agent): `start_heartbeat`
5. out-1 (automated, dr-agent): `inject_outage(region=A)` → records t0
6. fo-1 (automated, db): `sql_failover(target=B, allow_data_loss=true)` → records t_failover
7. fo-2 (automated, network): `tm_set_endpoint(region=A, enabled=false)`
8. val-1 (automated, app): `wait_healthy(tm, expect_region=B, timeout=900)` → records t_recovered
9. val-2 (automated, app): `functional_txn_check`
10. val-3 (automated, dr-agent): `measure_rpo`
11. fb-1 (human, app): "App team approves failback"
12. fb-2 (automated, app): `start_webapp(A)` + `wait_healthy(direct A)`
13. fb-3 (automated, db): `sql_failover(target=A, allow_data_loss=false)`
14. fb-4 (automated, network): `tm_set_endpoint(region=A, enabled=true)` + `wait_healthy(tm, expect_region=A)`
15. close-1 (automated, dr-agent): `stop_heartbeat`, `snapshot_inventory` diff, generate report, notify

### 4.5 Tools (dr_agent/tools)
- `azure_webapp.py`: `WebSiteManagementClient.web_apps.stop/start(rg, name)`, `list_application_settings`.
- `azure_sql.py`:
  - `failover_groups.get(rg, server, fog)` → `replication_role`, `replication_state`
  - **forced:** `failover_groups.begin_force_failover_allow_data_loss(rg, <target server>, fog).result()`
  - **planned:** `begin_failover(rg, <target server>, fog).result()`
  - Both are called against the **server that becomes primary**.
- `azure_trafficmanager.py`:
  - `TrafficManagerManagementClient.endpoints.get(rg, profile, "AzureEndpoints", name)`
  - set `endpoint_status = "Disabled"/"Enabled"`, then `endpoints.update(...)`
  - `profiles.get` → `endpoint_monitor_status` per endpoint (Online/Degraded/Disabled/CheckingEndpoint)
  - Probe through TM with **fresh DNS each time**: resolve the TM FQDN via `dns.resolver` (dnspython, no cache) to see which app it points at, then GET `http://<tm-fqdn>/health`.
- `inventory.py`: Resource Graph query over `rg-dra-workload` (type, name, location, sku) + app settings of both apps (secret values masked) + FOG status + Traffic Manager endpoint states (enabled + monitor status).
- `probes.py`: `httpx` GET with a 5s timeout (add `dnspython` to requirements). `probe(url)` returns (ok, region, db_ok, latency). `wait_healthy(url, expect_region, timeout)` polls every 5s.
- `heartbeat.py`:
  - Background thread, every 2s: connect via the FOG listener and `INSERT heartbeat(seq, client_ts)`.
  - On success, records `acked=true` in the Table; on failure, `acked=false` + error.
  - `newest_in_db()` = `SELECT MAX(client_ts) FROM heartbeat`.
- `notify.py`: `notify(kind, title, body, link)`, where kind is info, action, success or failure.
  - (1) Always appends to the `notifications` table, which the console bell polls.
  - (2) If `NOTIFY_EMAIL_TO` is set, sends an email via `azure.communication.email.EmailClient` (connection string from Key Vault) from `ACS_SENDER` (the managed-domain DoNotReply address). Simple HTML using the Virtusa styling, with a button link to the console.
  - Never raises (logs on failure).
  - Emails go out only for: exercise started, action needed (human step), recovered (RTO/RPO), failed/aborted, completed (report link).
- `store.py`: thin wrapper over Tables/Blob.

### 4.6 Engine
- `orchestrator.py`: one asyncio background task per running exercise. It walks the steps in topological order and persists every transition to `events`. It reacts to approval, confirm, kill-switch and rollback commands (from the web via a store flag). When resumed after an App Service restart, it continues from the last completed step.
- `executor.py`: maps `action` → tool function. It wraps each call with timing, try/except and success-criteria evaluation.
- `measurements.py` (pure functions, unit tested):
  - `rto_s = t_recovered - t0`
  - `rpo_s = max(0, last_acked_before(t_failover).client_ts - newest_in_db_after_failover)`
  - plus target comparison.

### 4.7 Web (web/)
Routes:
- `GET /` overview: polls `/api/status`, which reads Traffic Manager + direct A/B probes + FOG role.
- `GET /phase1`, `POST /api/profile`, `POST /api/strategy`, `POST /api/readiness`, `POST /api/runbook`
- `GET /plan`, `POST /api/plan`, `POST /api/plan/approve`
- `GET /exercise/{id}` live view, `GET /api/exercise/{id}/events?after=n`, `POST /api/exercise/{id}/confirm/{step}`, `/kill`, `/rollback`, `/retry`
- `GET /results/{id}`, `GET /reports/{id}` (serves the Blob HTML)

UI:
- Dark navy header, cards, big numbers for RTO/RPO with green/red, a vertical timeline with step icons, a pulsing "current step" indicator.
- Live RTO stopwatch from t0.
- Passcode login stored in a signed cookie.

## 5. Failure modes & mitigations
| Risk | Mitigation |
|---|---|
| Traffic Manager keeps routing to A while A is down (DNS caching) | The agent explicitly disables endpoint A. TTL is 10s and probes every 10s. `wait_healthy` resolves DNS fresh and checks the `region` field. Expected switch time is ~20–40s. |
| Forced failover leaves the old primary as secondary | Failback uses a planned failover after A is healthy. The FOG re-syncs automatically. |
| LLM slow or unavailable | Deterministic fallbacks (4.3). Plans are validated before use. |
| App Service restart mid-exercise | State persisted in Tables. The orchestrator resumes. |
| Cost | Basic/B1 only. `teardown.sh` runs every night if idle. Budget alerts. |
| Demo-day network issues | A recorded full run is kept as a backup video. |

## 6. Testing
- Unit:
  - `measurements.py` (RTO/RPO edge cases: no loss, gap, negative clamp)
  - plan validator (cycle, bad action, wrong order)
  - parity diff
  - agent fallbacks
- Integration (manual, scripted): `scripts/smoke.sh` runs probes against Traffic Manager and both regions, and checks FOG status.
- End-to-end: a full exercise from the console, twice, before the demo.
