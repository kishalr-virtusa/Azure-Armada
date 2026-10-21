# DR Agent evals: repro: readiness+planner timeouts

Run `20261021-116be6a-partial` · git `116be6a` · 2026-10-21 04:27 UTC · agents `dra-eval-*` (gpt-4.1-mini, gpt-5-mini) · fixtures: records.json (dra-cosmos-e23x/dra (read-only snapshot), captured 2026-10-19T14:16:20+00:00) · total **$0.02**

| Agent | Cases × reps | Pass | Fallback | p50 / p95 s | Groundedness (1-5) | Key metric | $ |
|---|---|---|---|---|---|---|---|
| readiness | 2 × 2 | 100% | 0% | 4.8 / 6.3 | – | model go/no-go 100%, risks named 100% | 0.003 |
| planner | 2 × 2 | 50% | 50% | 29.6 / 61.5 | – | first-pass 50%, final valid 50% | 0.015 |

## Gates

| Agent | Gate | Met |
|---|---|---|
| readiness | schema 100% | ✅ |
| readiness | fallback <= 5% | ✅ |
| planner | schema 100% | ✅ |
| planner | fallback <= 5% | ❌ |
| planner | first-pass >= 80% | ❌ |
| planner | final 100% | ❌ |

## Failed cases

| Case | Why |
|---|---|
| planner-no-risks #1 | llm, first_pass, final_valid, pass · fallback |
| planner-notes #1 | llm, first_pass, final_valid, pass · fallback |

Re-run: `python -m evals.run --agents readiness,planner --only asr-critical,keyvault,planner-notes,planner-no-risks --reps 2 --max-usd 0.08 --no-judge --label repro: readiness+planner timeouts`
