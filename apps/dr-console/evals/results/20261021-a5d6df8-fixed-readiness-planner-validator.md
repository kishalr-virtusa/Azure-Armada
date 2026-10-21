# DR Agent evals: fixed: per-agent request caps, multi-message parse, validator no-op fix guard

Run `20261021-0c4fe35-partial` · git `0c4fe35` · 2026-10-21 04:32 UTC · agents `dra-eval-*` (gpt-4.1-mini, gpt-5-mini) · fixtures: records.json (dra-cosmos-e23x/dra (read-only snapshot), captured 2026-10-19T14:16:20+00:00) · total **$0.23**

| Agent | Cases × reps | Pass | Fallback | p50 / p95 s | Groundedness (1-5) | Key metric | $ |
|---|---|---|---|---|---|---|---|
| readiness | 8 × 3 | 92% | 0% | 4.1 / 5.1 | – | model go/no-go 100%, risks named 92% | 0.016 |
| planner | 8 × 3 | 100% | 0% | 25.7 / 38.9 | – | first-pass 100%, final valid 100% | 0.183 |
| validator | 10 × 3 | 90% | 0% | 5.2 / 7.3 | – | action 90%, fix_action 100% | 0.032 |

## Gates

| Agent | Gate | Met |
|---|---|---|
| readiness | schema 100% | ✅ |
| readiness | fallback <= 5% | ✅ |
| planner | schema 100% | ✅ |
| planner | fallback <= 5% | ✅ |
| planner | first-pass >= 80% | ✅ |
| planner | final 100% | ✅ |
| validator | schema 100% | ✅ |
| validator | fallback <= 5% | ✅ |
| validator | fix_action >= 90% | ✅ |
| validator | p95 <= 20 s | ✅ |

## Failed cases

| Case | Why |
|---|---|
| readiness-recorded-region-b-down #0 | risks_named, pass |
| readiness-recorded-region-b-down #2 | risks_named, pass |
| validator-recorded-asr-core-quota #0 | action_ok, pass |
| validator-recorded-asr-core-quota #1 | action_ok, pass |
| validator-recorded-asr-core-quota #2 | action_ok, pass |

Re-run: `python -m evals.run --agents readiness,planner,validator --reps 3 --max-usd 0.60 --no-judge --label fixed: per-agent request caps, multi-message parse, validator no-op fix guard`
