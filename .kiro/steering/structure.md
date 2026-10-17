---
inclusion: always
---

# Repository structure

```
.kiro/                     Kiro steering + spec (source of truth for the build)
infra/
  main.bicep               subscription-scope entry: creates RGs, calls modules
  modules/                 log.bicep, keyvault.bicep, storage.bicep, sql.bicep, webapp.bicep,
                           trafficmanager.bicep, console.bicep, roles.bicep
  parameters.json          names, regions, SKUs (no secrets)
  scripts/                 deploy.sh, teardown.sh, budget.sh, seed-secrets.sh, deploy-apps.sh
apps/
  demo-app/                "Contoso Payments" workload (FastAPI)
    app/main.py, app/db.py, app/templates/, app/static/
    requirements.txt, startup.sh
  dr-console/              DR Agent console + engine (FastAPI)
    dr_agent/
      config.py            env settings
      llm.py               AzureOpenAI client + structured-output helper
      models.py            pydantic: AppProfile, StrategyRec, ReadinessReport, RunbookReview,
                           ExercisePlan, PlanStep, Measurement, ExerciseReport
      agents/              architect.py, readiness.py, runbook.py, planner.py, validator.py, reporter.py
                           (+ prompts/*.md for each agent's system prompt)
      tools/               azure_webapp.py, azure_sql.py, azure_trafficmanager.py, inventory.py,
                           probes.py, heartbeat.py, gchat.py, store.py (Tables/Blob)
      engine/              orchestrator.py (phase state machine), executor.py (runs plan steps),
                           measurements.py (RTO/RPO math)
    web/                   routes.py, templates/, static/
    tests/
    requirements.txt, startup.sh
docs/
  runbooks/payments-dr-runbook.md   intentionally slightly stale runbook (input for the Runbook agent)
  architecture/  deck/  idea-submission.md
tools/diagrams/            generators for the diagram and deck
```

Naming: resource names `dra-<component>-<region-short>-<suffix>`, e.g. `dra-app-cin-x7k2`. The suffix is `uniqueString(subscription().id)` truncated to 4 characters.
