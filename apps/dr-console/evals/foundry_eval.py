"""Azure AI Foundry evaluations (azure-ai-evaluation): the Groundedness judge and logging runs to the Foundry project.

- groundedness(): GroundednessEvaluator (model-graded, 1-5, pass at >= 3) over Black box answers (context = the
  records the agent saw: recent drills + every tool output) and Reporter narratives (context = the measured facts).
  The judge is the gpt-4.1-mini deployment, keyless (DefaultAzureCredential).
- log_deterministic(): the deterministic grades of every case as a code-evaluator run, so the whole golden-set
  result shows in the Foundry portal's Evaluations tab next to the agent versions.

Both go through azure.ai.evaluation.evaluate(); with upload the run is logged to FOUNDRY_PROJECT_ENDPOINT.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Optional

JUDGE_API_VERSION = "2024-10-21"


def _model_config() -> dict:
    from azure.ai.evaluation import AzureOpenAIModelConfiguration
    return AzureOpenAIModelConfiguration(azure_endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
                                         azure_deployment=os.environ.get("EVAL_JUDGE_DEPLOYMENT", "gpt-4.1-mini"),
                                         api_version=JUDGE_API_VERSION)


def _project(upload: bool) -> Optional[str]:
    return os.environ.get("FOUNDRY_PROJECT_ENDPOINT") if upload else None


def _narrative(out: dict) -> str:
    rem = "; ".join(f"{r.get('item')} ({r.get('owner')}, {r.get('priority')})" for r in out.get("remediation") or [])
    return "\n".join([out.get("executive_summary") or "", "Went well: " + "; ".join(out.get("what_went_well") or []),
                      "Issues: " + "; ".join(out.get("issues") or []), "Remediation: " + rem])


def judge_inputs(rows: list[dict], cases: list[dict]) -> list[dict]:
    """One judge row per graded run: {row_id, query, response, context}."""
    by_id = {c["id"]: c for c in cases}
    out = []
    for r in rows:
        case, o = by_id.get(r["id"]), r.get("output") or {}
        if not case or not o:
            continue
        if r["agent"] == "blackbox":
            q, resp, ctx = case["input"]["question"], o.get("answer") or "", r.get("context") or ""
        else:
            q = "Write the DR exercise report narrative from the measured facts."
            resp, ctx = _narrative(o), json.dumps(case["input"]["facts"], default=str)[:14000]
        out.append({"row_id": f"{r['id']}#{r.get('rep', 0)}", "agent": r["agent"], "query": q, "response": resp,
                    "context": ctx or "(no records were returned)"})
    return out


def _tokens(row: dict, prefix: str) -> tuple[int, int]:
    """Judge tokens of one row: flat outputs.<evaluator>.*_tokens columns or the <evaluator>_properties dict."""
    flat = {k: v for k, v in row.items() if k.startswith(prefix)}
    for k, v in list(flat.items()):
        if k.endswith("_properties") and isinstance(v, dict):
            flat.update({f"{k}.{kk}": vv for kk, vv in v.items()})
    inp = out = 0
    for k, v in flat.items():
        if not isinstance(v, (int, float)) or isinstance(v, bool):
            continue
        if k.endswith(("prompt_tokens", "input_tokens", "input_token_count")):
            inp += int(v)
        elif k.endswith(("completion_tokens", "output_tokens", "output_token_count")):
            out += int(v)
    return inp, out


def groundedness(rows: list[dict], cases: list[dict], upload: bool = False,
                 name: str = "dra-groundedness") -> tuple[dict[str, dict], float, str]:
    """({row_id: {score, result, reason}}, judge $ cost, Foundry studio URL or '')."""
    from azure.ai.evaluation import GroundednessEvaluator, evaluate
    from azure.identity import DefaultAzureCredential

    from evals.run import cost_usd
    data = judge_inputs(rows, cases)
    if not data:
        return {}, 0.0, ""
    work = Path(tempfile.mkdtemp(prefix="dra-eval-"))
    path = work / "groundedness.jsonl"
    path.write_text("".join(json.dumps(d, ensure_ascii=False) + "\n" for d in data), encoding="utf-8")
    judge = GroundednessEvaluator(_model_config(), credential=DefaultAzureCredential())
    res = evaluate(data=str(path), evaluators={"groundedness": judge}, evaluation_name=name,
                   evaluator_config={"groundedness": {"column_mapping": {
                       "query": "${data.query}", "response": "${data.response}", "context": "${data.context}"}}},
                   azure_ai_project=_project(upload), output_path=str(work / "out.json"))
    judged: dict[str, dict] = {}
    tin = tout = 0
    for row in res.get("rows") or []:
        rid = row.get("inputs.row_id")
        score = row.get("outputs.groundedness.groundedness")
        i, o = _tokens(row, "outputs.groundedness.")
        tin, tout = tin + i, tout + o
        judged[rid] = {"score": float(score) if isinstance(score, (int, float)) else None,
                       "result": row.get("outputs.groundedness.groundedness_result"),
                       "reason": (row.get("outputs.groundedness.groundedness_reason") or "")[:400]}
    judge_cost = cost_usd({"input_tokens": tin, "output_tokens": tout}, os.environ.get("EVAL_JUDGE_DEPLOYMENT", "gpt-4.1-mini"))
    return judged, judge_cost, res.get("studio_url") or ""


class DeterministicGrades:
    """Code evaluator: echoes the harness's deterministic grades so they are logged with the Foundry run."""

    def __init__(self) -> None:
        pass

    def __call__(self, *, grades: str) -> dict:
        g = json.loads(grades or "{}")
        out = {"pass": float(bool(g.get("pass")))}
        for k in ("llm", "numeric_exact", "citations_valid", "injection_resisted", "fix_action_ok", "action_ok",
                  "first_pass", "recommended_ok", "model_go_ok", "verdict_ok", "final_valid"):
            if isinstance(g.get(k), bool):
                out[k] = float(g[k])
        return out


def log_deterministic(result: dict, name: str) -> str:
    """Log every graded case to the Foundry project as one evaluation run; returns the studio URL ('' on failure)."""
    from azure.ai.evaluation import evaluate
    rows = [r for r in result["cases"] if not r.get("skipped")]
    work = Path(tempfile.mkdtemp(prefix="dra-eval-"))
    path = work / "grades.jsonl"
    with path.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps({"row_id": f"{r['id']}#{r.get('rep', 0)}", "agent": r["agent"], "source": r.get("source"),
                                "latency_ms": r.get("latency_ms"), "cost_usd": r.get("cost_usd"),
                                "agent_version": r.get("agent_version"), "grades": json.dumps(r.get("grade") or {}, default=str)})
                    + "\n")
    try:
        res = evaluate(data=str(path), evaluators={"dra": DeterministicGrades()}, evaluation_name=name,
                       evaluator_config={"dra": {"column_mapping": {"grades": "${data.grades}"}}},
                       azure_ai_project=_project(True), output_path=str(work / "out.json"),
                       tags={"git_sha": result["run"]["git_sha"], "prefix": result["run"]["prefix"]})
        return res.get("studio_url") or ""
    except Exception as e:  # noqa: BLE001 - the local results are still written
        print(f"Foundry upload of the deterministic grades failed: {type(e).__name__}: {str(e)[:300]}")
        return ""
