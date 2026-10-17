# Requirements: DR Agent (Autonomous Disaster Recovery Validation)

## Introduction
The DR Agent validates an application's disaster recovery by running a complete DR exercise on Azure:
design & preparation checks, planning, a real outage + failover, validation with measured RTO/RPO, failback and a report.
The demo target is the "Contoso Payments" web app deployed in two regions. Read `.kiro/steering/*` first.

## Requirement 1: Demo workload app
**User story:** As a presenter, I want a small, good-looking app whose availability and data I can show live, so the audience can see the outage and the recovery.

Acceptance criteria:
1. WHEN `/` is requested THE app SHALL render a styled page showing: app name, **serving region** (from `REGION_NAME`), status badge, the last 10 transactions (id, amount, created_at, written_by_region), and auto-refresh every 3s.
2. WHEN `/health` is requested THE app SHALL perform a DB read and write and return JSON `{status, region, db_ok, db_server, time}` with HTTP 200 only if both succeed. Otherwise HTTP 503.
3. THE app SHALL connect only through the SQL failover group listener, so it follows a failover with no config change.
4. WHEN `POST /api/txn` is called THE app SHALL insert a transaction row and return it.
5. THE app SHALL start with no manual steps on App Service (`startup.sh`, creates the table if missing).

## Requirement 2: Infrastructure as code
**User story:** As the team, we want one-command deploy and teardown, so we can rebuild cheaply and never leave costs running.

1. `infra/scripts/deploy.sh` SHALL deploy everything in `tech.md` idempotently into two resource groups: `rg-dra-workload` and `rg-dra-control`.
2. THE SQL setup SHALL include servers in Region A and Region B, database `paymentsdb` (Basic), and a failover group with a manual failover policy.
3. Traffic Manager SHALL use Priority routing with Region A (priority 1) and Region B (priority 2) as Azure endpoints, DNS TTL 10s, health-probing `/health` every 10s. Both apps SHALL accept the TM hostname. Front Door is not allowed on Free Trial.
4. THE console App Service SHALL have a system-assigned managed identity with **Contributor on `rg-dra-workload` only**, and Storage Table/Blob Data Contributor on the control storage account.
5. `infra/scripts/budget.sh` SHALL create a subscription budget with alerts at $50, $100 and $150.
6. `infra/scripts/teardown.sh` SHALL delete both resource groups after confirmation.
7. Secrets (SQL admin password, ACS connection string, console passcode) SHALL live in Key Vault and be consumed via Key Vault references.

## Requirement 3: Phase 1 (DR design & preparation)
**User story:** As a DR manager, I want the agent to confirm the right strategy and that the DR environment is ready before any exercise.

1. THE console SHALL hold an **App Profile**: name, tier (0–3), RTO minutes, RPO minutes, monthly budget, current topology (from inventory).
2. WHEN "Recommend strategy" is clicked THE **Architect agent** SHALL return the recommended strategy from {backup-restore, pilot-light, warm-standby, active-passive, active-active}, plus a comparison table (expected RTO, expected RPO, relative cost, fit yes/no) and a rationale. It SHALL map the recommendation onto the deployed design.
3. WHEN "Check readiness" is clicked THE **Readiness agent** SHALL run these deterministic checks and summarize a go/no-go:
   - failover group replication state + lag
   - Region B app `/health` reachable directly
   - **app-settings parity** between Region A and Region B (keys and non-secret values; ignore `REGION_NAME`)
   - Traffic Manager endpoints enabled + monitor status Online
   - Key Vault reachable
4. WHEN "Review runbook" is clicked THE **Runbook agent** SHALL compare `docs/runbooks/payments-dr-runbook.md` with the live inventory and return each step as valid / stale / missing, with a suggested correction and an updated runbook.
5. EACH check result SHALL be stored and shown with a timestamp and evidence (raw values).

## Requirement 4: Exercise planning
**User story:** As a DR coordinator, I want an ordered plan with owners and approval gates, so the drill runs in the right sequence.

1. WHEN "Generate plan" is clicked THE **Planner agent** SHALL produce an `ExercisePlan` whose steps each have:
   - `id`, `phase` (pre-checks, outage, failover, validation, failback, closure), `title`, `owner_team` (app, db, network, dr-agent), `type` (human | automated)
   - `action` (an engine tool name, automated steps only) and `params`
   - `depends_on`, `success_criteria`, `abort_on_failure`
2. THE engine SHALL validate the plan:
   - the dependency graph is acyclic
   - every automated `action` is in the tool allowlist
   - the order follows the mandatory sequence: pre-checks → outage → DB failover → traffic switch → validation → failback (app start → DB failback → traffic restore) → closure
   - An invalid plan SHALL be rejected with reasons, and the agent asked to regenerate it once.
3. THE plan SHALL be shown as a table/timeline in the console before execution.

## Requirement 5: Execution with approvals
**User story:** As a DR manager, I want the agent to run automated steps itself, but stop for human steps and approvals.

1. Starting an exercise SHALL require an explicit console approval of the whole plan (recorded with name + time).
2. WHEN a step is `human` THE engine SHALL pause, raise a console notification and send an email (step, owner, link to the console) and wait for "Confirm done" in the console.
3. WHEN a step is `automated` THE engine SHALL execute the mapped tool, record start/end/result in the timeline, and evaluate `success_criteria`.
4. IF an automated step fails AND `abort_on_failure` THEN the engine SHALL stop, ask the Validator agent for a diagnosis, and offer **Rollback** (run the failback steps) or **Retry**.
5. THE console SHALL offer a **Kill switch** at any time. It stops the engine after the current step and offers rollback.
6. Tool allowlist:
   - `stop_webapp(region)` / `start_webapp(region)`
   - `inject_outage(region)` = stop the app in that region and record t0
   - `sql_failover(target_region, allow_data_loss)`
   - `tm_set_endpoint(region, enabled)`
   - `wait_healthy(url, timeout_s)`
   - `probe_tm(expect_region)`
   - `measure_rpo()`
   - `snapshot_inventory()`
   - `notify(text)`

## Requirement 6: Validation & measurement (honest RTO/RPO)
**User story:** As an auditor, I want measured, evidence-backed RTO and RPO compared with the targets.

1. A **heartbeat writer** in the engine SHALL insert a row every 2s through the failover group listener while an exercise runs, recording each acknowledged write (seq, client timestamp) in the timeline store.
2. **RTO** = time from the outage injection (t0) to the first successful `probe_tm` that returns 200 from the DR region with `db_ok=true`.
3. **RPO** = timestamp of the last heartbeat acknowledged before the DB failover was triggered (t_fo), minus the timestamp of the newest heartbeat present in the new primary DB after failover. It is 0 if nothing was lost, and never negative.
   - The disaster failover uses a **forced failover (`allow_data_loss=true`)**, as in a real regional loss, so the measured RPO reflects real replication lag.
   - Failback uses a planned failover.
4. THE console SHALL show both values against the targets with ✅/❌, plus the raw evidence (t0, recovery time, seq numbers).
5. Validation SHALL include a functional check: `POST /api/txn` via Traffic Manager succeeds after failover.

## Requirement 7: Failback & closure
1. After validation, the plan SHALL perform failback:
   - start Region A
   - wait until healthy
   - SQL failover back to Region A (planned, no data loss)
   - enable the Region A Traffic Manager endpoint
   - verify Traffic Manager serves Region A
   - stop the heartbeat
2. Failback time SHALL be measured and reported separately.
3. THE exercise SHALL end with the environment in its original state, verified by an inventory snapshot diff.

## Requirement 8: Report & remediation
1. THE **Reporter agent** SHALL produce a report (HTML, saved to Blob, viewable in the console) containing:
   - the profile and strategy
   - readiness and runbook findings
   - the plan as executed, with the timeline
   - RTO/RPO results vs targets
   - failback result
   - issues found, and remediation items (owner, priority)
2. A summary SHALL be sent as a console notification + email, with a link to the report.
3. (Stretch) For a parity gap, the agent SHALL propose the exact fix (app setting / Bicep diff) and can apply it after approval.

## Requirement 9: Console UX
1. Pages: **Overview** (live status of both regions + Traffic Manager), **Phase 1**, **Plan**, **Live exercise** (timeline that updates every 2s, current step, approvals, kill switch, live RTO timer), **Results & report**.
2. Clean, modern styling. It must look good on a projector (large type, high contrast).
3. Simple auth: a shared passcode from Key Vault. Good enough for a hackathon. (Stretch: Entra ID sign-in via App Service Authentication.)

## Non-functional
- Total Azure spend for the build week stays under $60.
- A full exercise (failover + failback) completes in under 15 minutes.
- Every Azure-changing action is logged with who/what/when.
- The demo works from a clean deploy via the scripts and the README.
