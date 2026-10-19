"""OpenTelemetry export to Application Insights, and the token price table behind every agent call's $ figure.

`configure()` turns on the Azure Monitor OpenTelemetry distro once per process (gunicorn.conf.py calls it in each
worker after the fork). The spans themselves come from `dr_agent.trace`, which bridges every live-trace event to
an OpenTelemetry span with the GenAI semantic conventions (invoke_agent / execute_tool / chat).

What is exported, and what is kept out on purpose (App Insights ingest is free up to 5 GB per month):
- FastAPI request spans, except the endpoints the browser polls every 1-15 s (POLLED_PATHS) and static files.
- httpx spans only for the model endpoints (Foundry project / Azure OpenAI); they are named "chat <model>".
- No Azure SDK, requests or urllib spans: the drill engine polls Cosmos DB about once a second and the load drill
  writes ~20 heartbeats per second, which would otherwise each become a span.
- Python logs of the dr_agent package only (WARNING and above unless the app lowers the level).

It is a no-op when APPLICATIONINSIGHTS_CONNECTION_STRING is unset or TELEMETRY_ENABLED=false (an app setting, so
it can be switched off without a redeploy), and it never raises: export runs on the SDK's background batch
thread, so a failing exporter can neither break nor slow a request.
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Optional

log = logging.getLogger(__name__)

TRACER_NAME = "dr_agent"

# Endpoints the browser polls (setInterval in web/templates) plus health probes and static files. Regexes are
# matched with re.search against the request URL, for every HTTP method, so each is anchored to stay off the
# POST actions next to it (e.g. /api/exercise/{xid}/{cmd}, /api/notifications/{nid}/read).
POLLED_PATHS = (
    r"/api/trace/",
    r"/api/live-map$",
    r"/public/live$",
    r"/health$",
    r"/static/",
    r"/favicon\.ico$",
    r"/api/notifications$",
    r"/api/status$",
    r"/api/overview$",
    r"/api/exercises$",
    r"/api/exercise/[^/]+$",
    r"/api/exercise/[^/]+/events$",
    r"/api/probe/tm$",
    r"/api/autopilot$",
    r"/api/readiness/fix-progress$",
)

# httpx spans are kept only for the model hosts: every other URL is excluded (probes, ASR polling, notify).
MODEL_HOSTS = r"\.services\.ai\.azure\.com|\.openai\.azure\.com|\.cognitiveservices\.azure\.com"
HTTPX_EXCLUDED = rf"^https?://(?![^/]*({MODEL_HOSTS})(:\d+)?/)"

# Distro instrumentations we switch off (fastapi and httpx are instrumented below with our own options).
_DISTRO_DISABLED = ("azure_sdk", "requests", "urllib", "urllib3", "django", "flask", "psycopg2",
                    "fastapi", "httpx", "httpx2")

# --------------------------------------------------------------------------------------------- prices
# USD per 1M tokens, Global Standard deployments (what infra/modules/openai.bicep deploys). Source: Azure Retail
# Prices API, https://prices.azure.com/api/retail/prices, serviceName 'Foundry Models', armRegionName 'eastus2',
# read 2026-10-19:
#   gpt-4.1-mini  "gpt 4.1 mini Inp glbl" $0.0004/1K, "gpt 4.1 mini cached Inp glbl" $0.0001/1K,
#                 "gpt 4.1 mini Outp glbl" $0.0016/1K
#   gpt-5-mini    "GPT 5 Mini Inpt Glbl" $0.25/1M, "GPT 5 Mini cchd Inpt Glbl" $0.025/1M,
#                 "GPT 5 Mini outpt Glbl" $2.00/1M
# Reasoning tokens are billed as output and are already included in output_tokens.
# Override or extend with DRA_MODEL_PRICES='{"<model>": {"input": .., "cached": .., "output": ..}}'.
PRICES_USD_PER_1M: dict[str, dict[str, float]] = {
    "gpt-4.1-mini": {"input": 0.40, "cached": 0.10, "output": 1.60},
    "gpt-5-mini": {"input": 0.25, "cached": 0.025, "output": 2.00},
}


def prices() -> dict[str, dict[str, float]]:
    """The price table, with DRA_MODEL_PRICES (JSON) merged over the built-in list prices."""
    table = {k: dict(v) for k, v in PRICES_USD_PER_1M.items()}
    raw = os.environ.get("DRA_MODEL_PRICES", "").strip()
    if raw:
        try:
            for model, p in json.loads(raw).items():
                table[str(model).lower()] = {"input": float(p["input"]), "output": float(p["output"]),
                                             "cached": float(p.get("cached", p["input"]))}
        except (ValueError, TypeError, KeyError, AttributeError) as e:
            log.warning("DRA_MODEL_PRICES ignored: %s", e)
    return table


def price_for(model: str) -> Optional[dict[str, float]]:
    """Prices of a deployment or model name; dated versions ('gpt-4.1-mini-2025-04-14') match their family."""
    m = (model or "").strip().lower()
    if not m:
        return None
    table = prices()
    if m in table:
        return table[m]
    for name in sorted(table, key=len, reverse=True):  # longest first: gpt-5-mini before gpt-5
        if m.startswith(name + "-"):
            return table[name]
    return None


def cost_usd(model: str, usage: Optional[dict]) -> Optional[float]:
    """USD for one call's usage {input_tokens, output_tokens, cached_tokens}; None when the model has no price.

    cost = ((input - cached) * input_price + cached * cached_price + output * output_price) / 1e6
    """
    p = price_for(model)
    if p is None or not isinstance(usage, dict):
        return None
    inp = int(usage.get("input_tokens") or 0)
    cached = min(int(usage.get("cached_tokens") or 0), inp)
    out = int(usage.get("output_tokens") or 0)
    return round(((inp - cached) * p["input"] + cached * p["cached"] + out * p["output"]) / 1e6, 6)


# --------------------------------------------------------------------------------------------- export
_tracer: Any = None
_provider: Any = None


def tracer() -> Any:
    """The tracer dr_agent.trace uses, or None when telemetry is off (then trace creates no spans at all)."""
    return _tracer


def enabled() -> bool:
    return _tracer is not None


def use_tracer_provider(provider: Any) -> None:
    """Point dr_agent.trace at `provider` (tests use an in-memory exporter); None switches spans off again."""
    global _tracer, _provider
    _provider = provider
    _tracer = provider.get_tracer(TRACER_NAME) if provider is not None else None


def _switched_off() -> bool:
    return os.environ.get("TELEMETRY_ENABLED", "true").strip().lower() in ("false", "0", "no", "off")


def configure() -> bool:
    """Start exporting to Application Insights. Idempotent; returns whether telemetry is on. Never raises.

    Call it before web.main is imported (gunicorn post_fork), so the FastAPI class is instrumented in time."""
    global _tracer, _provider
    if _tracer is not None:
        return True
    conn = os.environ.get("APPLICATIONINSIGHTS_CONNECTION_STRING", "").strip()
    if not conn or _switched_off():
        return False
    try:
        os.environ.setdefault("OTEL_PYTHON_HTTPX_EXCLUDED_URLS", HTTPX_EXCLUDED)
        from azure.monitor.opentelemetry import configure_azure_monitor
        configure_azure_monitor(
            connection_string=conn,
            logger_name=TRACER_NAME,  # only dr_agent.* logs; azure.core logs every Cosmos request at INFO
            enable_performance_counters=False,  # ~10 counters/min of ingest nobody looks at
            # keep every span: the exclusions above keep volume low, and the distro's default rate-limited
            # sampler drops spans per span, which leaves agent traces with holes (OTEL_TRACES_SAMPLER overrides)
            sampling_ratio=1.0,
            instrumentation_options={name: {"enabled": False} for name in _DISTRO_DISABLED},
        )
        from opentelemetry import trace as ot
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
        # no "http send"/"http receive" child spans: two extra rows per request with nothing to look at
        FastAPIInstrumentor().instrument(excluded_urls=",".join(POLLED_PATHS), exclude_spans=["receive", "send"])
        HTTPXClientInstrumentor().instrument(request_hook=_httpx_request_hook)
        _provider = ot.get_tracer_provider()
        _tracer = ot.get_tracer(TRACER_NAME)
        log.info("telemetry: exporting to Application Insights")
        return True
    except Exception:  # noqa: BLE001 - telemetry must never stop the console from starting
        log.exception("telemetry: configuration failed; continuing without it")
        _tracer = None
        return False


def shutdown(timeout_ms: int = 3000) -> None:
    """Flush pending spans (gunicorn worker_exit). Bounded and never raises."""
    try:
        if _provider is not None and hasattr(_provider, "force_flush"):
            _provider.force_flush(timeout_ms)
    except Exception:  # noqa: BLE001
        log.debug("telemetry flush failed", exc_info=True)


# --------------------------------------------------------------------------------------------- chat spans
_DEPLOYMENT_IN_PATH = re.compile(r"/deployments/([^/]+)/")


def _httpx_request_hook(span: Any, request: Any) -> None:
    """Name each model round "chat <model>" with GenAI attributes (it is a child of its invoke_agent span)."""
    try:
        if span is None or not span.is_recording():
            return
        method = request.method.decode() if isinstance(request.method, bytes) else str(request.method)
        url = str(request.url)
        if method != "POST" or not (url.split("?")[0].endswith("/responses") or "/chat/completions" in url):
            return
        from dr_agent import trace
        m = _DEPLOYMENT_IN_PATH.search(url)
        model = (m.group(1) if m else "") or trace.current_agent_model()
        span.update_name(f"chat {model}".strip())
        span.set_attribute("gen_ai.operation.name", "chat")
        span.set_attribute("gen_ai.provider.name", "azure.ai.openai")
        if model:
            span.set_attribute("gen_ai.request.model", model)
    except Exception:  # noqa: BLE001
        log.debug("chat span hook failed", exc_info=True)
