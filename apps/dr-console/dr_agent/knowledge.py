"""Knowledge index for the Black box: vector search over the DR records' unstructured text (RAG).

The Black box's exact tools (list_drills, get_drill, ...) answer facts and numbers. This module answers the other kind
of question: "what does the runbook say about ...", "has this error happened before and how was it fixed", "what have
our reports recommended". It chunks that text, embeds it with Azure OpenAI and searches it with the built-in vector
search of Azure Cosmos DB for NoSQL.

Store: container `knowledge` in the console's Cosmos DB database (infra/modules/knowledge.bicep), partitioned on /pk =
source type, vector policy /embedding float32 x 1536 cosine, quantizedFlat index. One document per chunk:
  {id, pk: source, source, ref, title, text, embedding, updated_at, hash, scope, link, cite}
  source  runbook | runbook-review | report | diagnosis | event | autopilot | decision
  ref     what the chunk is about: "step 4", a drill id, "ex-... fb-4", a date, a heading
  scope   the unit re-indexed together ("runbook", a drill id, "autopilot 2026-10-19"); stale chunks of a scope are
          deleted when it is re-indexed
  hash    sha256 of (embedding deployment, title, text): an unchanged chunk is never embedded or written again
  cite    the exact citation source the Black box must use for this chunk ("knowledge: Runbook step 4")

Embeddings: deployment EMBEDDING_DEPLOYMENT (default text-embedding-3-small) on AZURE_OPENAI_ENDPOINT, Entra ID only
(DefaultAzureCredential, the same "Cognitive Services OpenAI User" role the chat calls use).

Ingestion is idempotent and incremental: `index_drill(xid)` runs after each drill's report (best effort, in a
background thread, never failing the drill) and `python -m dr_agent.knowledge reindex` rebuilds everything.
Retrieval (`search`) never raises: errors come back as {"error": ...} for the agent to read.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import logging
import os
import re
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

log = logging.getLogger(__name__)

CONTAINER = "knowledge"
DIMS = 1536
SOURCES = ("runbook", "runbook-review", "report", "diagnosis", "event", "autopilot", "decision")
MAX_K = 8
MIN_QUERY_CHARS = 3
MAX_QUERY_CHARS = 500
MIN_SCORE = float(os.environ.get("KNOWLEDGE_MIN_SCORE", "0.2"))  # cosine similarity below this is noise
SEARCH_BUDGET = 3400  # chars of tool output (tools.call cuts at 4000)
QUERY_TIMEOUT_S = 8.0
INGEST_TIMEOUT_S = 30.0
EMBED_BATCH = 16
# chunk size: ~300-600 tokens at ~4 characters per token, with overlap when a section is split
CHUNK_CHARS = 1800
OVERLAP_CHARS = 240

APP_DIR = Path(__file__).resolve().parents[1]
DECISIONS_PATH = APP_DIR.parents[1] / "docs" / "tech-decisions.md"  # in the repo, not in the deployed app
DRILL_ID = re.compile(r"^ex-\d{8}-\d{6}$")
EVENT_KINDS = ("error", "diagnosis", "remediation", "chaos")
LABELS = {"runbook": "Runbook", "runbook-review": "Runbook review", "report": "Report", "diagnosis": "Diagnosis",
          "event": "Incident timeline", "autopilot": "Autopilot", "decision": "Tech decision"}


def enabled() -> bool:
    return os.environ.get("KNOWLEDGE_ENABLED", "true").strip().lower() not in ("0", "false", "off", "no")


def deployment() -> str:
    return os.environ.get("EMBEDDING_DEPLOYMENT", "text-embedding-3-small")


# ------------------------------------------------------------------ chunks
@dataclass
class Chunk:
    source: str
    ref: str
    title: str
    text: str
    scope: str
    link: str = "/"
    part: int = 0

    @property
    def id(self) -> str:
        """Readable and Cosmos-safe (no / \\ ? #): "runbook.step-4.0"."""
        slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", f"{self.source}.{self.ref}").strip("-")[:200]
        return f"{slug}.{self.part}"

    @property
    def cite(self) -> str:
        label = f"{LABELS.get(self.source, self.source)} {self.ref}" if self.source in ("runbook", "runbook-review") \
            else f"{LABELS.get(self.source, self.source)} · {self.ref}"
        return f"knowledge: {label}" + (f" (part {self.part + 1})" if self.part else "")

    def content_hash(self, model: str) -> str:
        return hashlib.sha256(f"{model}\n{self.title}\n{self.text}".encode("utf-8")).hexdigest()[:32]

    def doc(self, embedding: list[float], model: str, now: str) -> dict:
        return {"id": self.id, "pk": self.source, "source": self.source, "ref": self.ref, "title": self.title,
                "text": self.text, "embedding": embedding, "updated_at": now, "hash": self.content_hash(model),
                "scope": self.scope, "link": self.link, "cite": self.cite}


def _tail(text: str, n: int) -> str:
    """The last ~n characters of text, starting at a word boundary."""
    if len(text) <= n:
        return text
    t = text[-n:]
    return t[t.find(" ") + 1:] if " " in t else t


def split_text(text: str, max_chars: int = CHUNK_CHARS, overlap: int = OVERLAP_CHARS) -> list[str]:
    """Paragraph-packed pieces of at most max_chars; each piece after the first starts with the last ~overlap
    characters of the one before (so a fact cut at a boundary is still found). Pure."""
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]
    body = max_chars - overlap - 1
    units: list[str] = []
    for para in re.split(r"\n\s*\n", text):
        para = para.strip()
        while len(para) > body:  # an oversized paragraph: cut at the last line or word break that fits
            cut = max(para.rfind("\n", 0, body), para.rfind(" ", 0, body))
            cut = cut if cut > body // 2 else body
            units.append(para[:cut].rstrip())
            para = para[cut:].lstrip()
        if para:
            units.append(para)
    pieces: list[str] = []
    cur = ""
    for u in units:
        if cur and len(cur) + 2 + len(u) > body:
            pieces.append(cur)
            cur = u
        else:
            cur = f"{cur}\n\n{u}" if cur else u
    if cur:
        pieces.append(cur)
    return [p if i == 0 else f"{_tail(pieces[i - 1], overlap)}\n{p}" for i, p in enumerate(pieces)]


def _chunks(source: str, ref: str, title: str, text: str, scope: str, link: str = "/") -> list[Chunk]:
    return [Chunk(source, ref, title, piece, scope, link, i) for i, piece in enumerate(split_text(text))]


HEADING = re.compile(r"^(#{2,3})\s+(.*\S)\s*$")
STEP_HEADING = re.compile(r"^step\s+(\d+)\s*[:.-]?\s*(.*)$", re.I)


def markdown_sections(md: str) -> list[tuple[str, str, str]]:
    """(ref, heading, body) for every ## / ### section with content: "### Step 4: Initiate SQL Failover" ->
    ("step 4", ...); other headings -> their slug ("failback-procedure"); text before the first heading -> "overview"."""
    out: list[tuple[str, str, str]] = []
    ref, head, lines = "overview", "Overview", []

    def flush() -> None:
        body = "\n".join(lines).strip().strip("-").strip()
        if body:
            out.append((ref, head, body))

    for line in (md or "").splitlines():
        m = HEADING.match(line)
        if m:
            flush()
            head, lines = m.group(2), []
            s = STEP_HEADING.match(head)
            ref = f"step {s.group(1)}" if s else (re.sub(r"[^a-z0-9]+", "-", head.lower()).strip("-") or "section")
        else:
            lines.append(line)
    flush()
    seen: dict[str, int] = {}
    unique = []
    for r, h, b in out:  # the same heading twice keeps both, under distinct refs
        seen[r] = seen.get(r, 0) + 1
        unique.append((r if seen[r] == 1 else f"{r}-{seen[r]}", h, b))
    return unique


def runbook_chunks(md: str, source: str = "runbook", doc_title: str = "Runbook") -> list[Chunk]:
    out: list[Chunk] = []
    for ref, head, body in markdown_sections(md):
        out += _chunks(source, ref, f"{doc_title}: {head}", f"{head}\n{body}", scope=source)
    return out


def review_chunks(review: dict) -> list[Chunk]:
    """The Runbook agent's latest review: its findings (stale / missing steps with the suggested fix) and the
    updated runbook it wrote."""
    findings = [f"Step {s.get('step_no')} ({s.get('verdict')}): {s.get('text')}. Why: {s.get('reason')}."
                + (f" Suggested fix: {s['suggested']}" if s.get("suggested") else "")
                for s in review.get("steps") or [] if s.get("verdict") in ("stale", "missing")]
    out = _chunks("runbook-review", "findings", "Runbook review findings (stale or missing steps)",
                  "\n\n".join(findings), scope="runbook-review")
    for ref, head, body in markdown_sections(review.get("updated_runbook_md") or ""):
        out += _chunks("runbook-review", ref, f"Reviewed runbook: {head}", f"{head}\n{body}", scope="runbook-review")
    return out


def _j(v: Any, default: Any) -> Any:
    try:
        return json.loads(v) if v else default
    except (TypeError, ValueError):
        return default


def _report_from_html(page: str) -> dict:
    """Executive summary, what went well and issues from a report page (drills reported before the narrative was
    stored with the drill)."""
    def section(name: str, tag: str) -> str:
        m = re.search(rf"<h2>{name}</h2>\s*<{tag}[^>]*>(.*?)</{tag}>", page or "", re.S)
        return m.group(1) if m else ""
    items = lambda s: [html.unescape(re.sub(r"<[^>]+>", "", x)).strip() for x in re.findall(r"<li>(.*?)</li>", s, re.S)]  # noqa: E731
    summary = html.unescape(re.sub(r"<[^>]+>", "", section("Executive summary", "p"))).strip()
    return {"executive_summary": summary, "what_went_well": [x for x in items(section("What went well", "ul")) if x != "-"],
            "issues": [x for x in items(section("Issues", "ul")) if x != "None observed"]}


def drill_chunks(xid: str, m: dict, events: list[dict], report_html: Optional[Callable[[], Optional[str]]] = None) -> list[Chunk]:
    """Report narrative, Validator diagnoses and the incident timeline (error / diagnosis / remediation / chaos
    events) of one drill. Pure apart from the optional report_html() fallback."""
    link = f"/results/{xid}"
    out: list[Chunk] = []
    narrative = _j(m.get("report_narrative_json"), None)
    if narrative is None and m.get("report_url") and report_html:
        narrative = _report_from_html(report_html() or "")
    narrative = narrative or {}
    remediation = narrative.get("remediation") or _j(m.get("remediation_json"), [])
    parts = []
    if narrative.get("executive_summary"):
        parts.append(f"Executive summary: {narrative['executive_summary']}")
    if narrative.get("what_went_well"):
        parts.append("What went well:\n" + "\n".join(f"- {x}" for x in narrative["what_went_well"]))
    if narrative.get("issues"):
        parts.append("Issues:\n" + "\n".join(f"- {x}" for x in narrative["issues"]))
    if remediation:
        parts.append("Remediation:\n" + "\n".join(f"- [{r.get('priority')}] {r.get('item')} (owner {r.get('owner')})"
                                                  for r in remediation if isinstance(r, dict)))
    if parts:
        out += _chunks("report", xid, f"Drill report {xid} ({m.get('status') or 'unknown status'})",
                       "\n\n".join(parts), scope=xid, link=link)
    diagnoses = [e for e in events if e.get("kind") == "diagnosis" and (e.get("data") or _j(e.get("data_json"), None))]
    if not diagnoses and m.get("diagnosis_json"):  # a pending diagnosis not yet in the timeline
        diagnoses = [{"step_id": m.get("failed_step"), "ts": "", "data": _j(m["diagnosis_json"], {})}]
    for n, e in enumerate(diagnoses):
        d = e.get("data") or _j(e.get("data_json"), {})
        step = e.get("step_id") or "step"
        fix = d.get("fix_action") or "none"
        params = ", ".join(f"{p.get('key')}={p.get('value')}" for p in d.get("fix_params") or [] if isinstance(p, dict))
        text = (f"Drill {xid}, step {step} failed{' at ' + str(e['ts'])[:19] + ' UTC' if e.get('ts') else ''}.\n"
                f"Likely cause: {d.get('likely_cause')}\n"
                + "".join(f"Evidence: {x}\n" for x in (d.get("evidence") or [])[:6])
                + f"Recommended action: {d.get('recommended_action')}. Fix: {fix}" + (f" ({params})" if params else ""))
        ref = f"{xid} {step}" + (f" #{n + 1}" if len(diagnoses) > 1 else "")
        out += _chunks("diagnosis", ref, f"Validator diagnosis of {xid} step {step}", text, scope=xid, link=link)
    timeline = [f"{str(e.get('ts') or '')[11:19]} {e.get('kind')} {e.get('step_id') or ''}: {e.get('message')}"
                for e in events if e.get("kind") in EVENT_KINDS and e.get("message")]
    if timeline:
        out += _chunks("event", xid, f"Incident timeline of drill {xid} (errors, diagnoses, fixes, surprise faults)",
                       "\n".join(timeline), scope=xid, link=link)
    return out


def autopilot_chunks(events: list[dict]) -> list[Chunk]:
    """Autopilot findings, one chunk group per UTC day; a finding repeated run after run is listed once with its
    first and last time. Runs where everything passed are summarised by a count."""
    days: dict[str, dict[str, Any]] = {}
    for e in events:
        ts = str(e.get("ts") or "")
        d = e.get("data") or _j(e.get("data_json"), {}) or {}
        day = days.setdefault(ts[:10] or "unknown", {"ok": 0, "findings": {}})
        failing = d.get("failing") or {}
        summary = ((d.get("ai") or {}).get("summary") or "").strip()
        if d.get("go") and not failing:
            day["ok"] += 1
            continue
        detail = "; ".join(f"{k}: {v}" for k, v in sorted(failing.items())) if isinstance(failing, dict) \
            else ", ".join(map(str, failing))
        key = f"{e.get('message')}\n{detail}\n{summary}"
        f = day["findings"].setdefault(key, {"first": ts[11:19], "last": ts[11:19], "runs": 0})
        f["last"], f["runs"] = ts[11:19], f["runs"] + 1
    out: list[Chunk] = []
    for day, v in sorted(days.items()):
        lines = [f"{f['first']}{'-' + f['last'] if f['last'] != f['first'] else ''} UTC ({f['runs']} run(s)): "
                 f"{key.replace(chr(10), ' | ').strip(' |')}" for key, f in v["findings"].items()]
        if v["ok"]:
            lines.append(f"{v['ok']} run(s) found all checks passing.")
        out += _chunks("autopilot", day, f"Autopilot findings on {day}", "\n\n".join(lines), scope=f"autopilot {day}")
    return out


def decision_chunks(md: str) -> list[Chunk]:
    return runbook_chunks(md, source="decision", doc_title="Tech decisions")


# ------------------------------------------------------------------ embeddings
_client = None
_client_lock = threading.Lock()


def _openai():
    global _client
    with _client_lock:
        if _client is None:
            from azure.identity import DefaultAzureCredential, get_bearer_token_provider
            from openai import AzureOpenAI

            from dr_agent.llm import COGNITIVE_SCOPE, DEFAULT_API_VERSION
            endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT")
            if not endpoint:
                raise RuntimeError("AZURE_OPENAI_ENDPOINT is not configured")
            _client = AzureOpenAI(azure_endpoint=endpoint,
                                  api_version=os.environ.get("AZURE_OPENAI_API_VERSION", DEFAULT_API_VERSION),
                                  azure_ad_token_provider=get_bearer_token_provider(DefaultAzureCredential(), COGNITIVE_SCOPE),
                                  max_retries=1)
    return _client


def embed(texts: list[str], timeout_s: float = INGEST_TIMEOUT_S) -> list[list[float]]:
    """One embedding per text (batched). Raises on failure."""
    out: list[list[float]] = []
    for i in range(0, len(texts), EMBED_BATCH):
        resp = _openai().embeddings.create(model=deployment(), input=texts[i:i + EMBED_BATCH], timeout=timeout_s)
        vecs = [d.embedding for d in sorted(resp.data, key=lambda d: d.index)]
        if any(len(v) != DIMS for v in vecs):
            raise RuntimeError(f"embedding deployment {deployment()} returned vectors that are not {DIMS}-dimensional")
        out += vecs
    return out


# ------------------------------------------------------------------ store (Cosmos DB container 'knowledge')
def _container():
    from dr_agent.tools import store
    return store._container(CONTAINER)


# projections use the bracket form: field names such as "ref", "source" or "text" may be reserved words
SCOPE_QUERY = 'SELECT c["id"], c["hash"] FROM c WHERE c["scope"] = @scope'


def search_query(k: int) -> str:
    """Top-k by cosine similarity (Cosmos orders VectorDistance most-similar first); the embedding is never returned."""
    return (f'SELECT TOP {int(k)} c["id"], c["source"], c["ref"], c["title"], c["text"], c["link"], c["cite"], '
            'VectorDistance(c["embedding"], @v) AS score FROM c ORDER BY VectorDistance(c["embedding"], @v)')


def upsert_chunks(chunks: list[Chunk], scope: str, source: str,
                  embedder: Optional[Callable[[list[str]], list[list[float]]]] = None) -> dict:
    """Write one scope's chunks: unchanged ones (same hash) are skipped, changed ones embedded and upserted, chunks of
    the scope that no longer exist deleted. Returns counts."""
    c = _container()
    model = deployment()
    existing = {r["id"]: r.get("hash") for r in c.query_items(query=SCOPE_QUERY, parameters=[{"name": "@scope", "value": scope}],
                                                             partition_key=source)}
    todo = [ch for ch in chunks if existing.get(ch.id) != ch.content_hash(model)]
    if todo:
        vecs = (embedder or embed)([f"{ch.title}\n{ch.text}" for ch in todo])
        now = datetime.now(timezone.utc).isoformat()
        for ch, v in zip(todo, vecs):
            c.upsert_item(ch.doc(v, model, now))
    keep = {ch.id for ch in chunks}
    stale = [i for i in existing if i not in keep]
    for i in stale:
        c.delete_item(i, partition_key=source)
    return {"embedded": len(todo), "unchanged": len(chunks) - len(todo), "deleted": len(stale)}


def _index(groups: Iterable[tuple[str, str, list[Chunk]]], embedder=None) -> dict:
    """groups: (source, scope, chunks). Counts summed over all groups; a failing group is logged and skipped."""
    total = {"embedded": 0, "unchanged": 0, "deleted": 0, "failed": 0}
    for source, scope, chunks in groups:
        try:
            for k, v in upsert_chunks(chunks, scope, source, embedder).items():
                total[k] += v
        except Exception as e:  # noqa: BLE001 - one bad group must not stop the rest
            total["failed"] += 1
            log.warning("knowledge: indexing %s/%s failed: %s", source, scope, e)
    return total


def _by_scope(source: str, chunks: list[Chunk]) -> list[tuple[str, str, list[Chunk]]]:
    scopes: dict[str, list[Chunk]] = {}
    for ch in chunks:
        if ch.source == source:
            scopes.setdefault(ch.scope, []).append(ch)
    return [(source, s, cs) for s, cs in scopes.items()]


def _drill_groups(xid: str) -> list[tuple[str, str, list[Chunk]]]:
    from dr_agent.tools import store
    m = store.get_exercise(xid) or {}
    chunks = drill_chunks(xid, m, store.get_events(xid), lambda: store.get_report(xid))
    # every source is listed, also when it has no chunks now: a scope's stale chunks are deleted
    return [(s, xid, [c for c in chunks if c.source == s]) for s in ("report", "diagnosis", "event")]


def _shared_groups(autopilot_days: Optional[int] = None) -> list[tuple[str, str, list[Chunk]]]:
    """Runbook, the latest runbook review, Autopilot findings (the last N days, all if None) and tech decisions."""
    from dr_agent import autopilot, service
    from dr_agent.tools import store
    groups: list[tuple[str, str, list[Chunk]]] = []
    if service.RUNBOOK_PATH.exists():
        groups.append(("runbook", "runbook", runbook_chunks(service.RUNBOOK_PATH.read_text(encoding="utf-8"))))
    review = _j((store.get_exercise(service.PHASE1) or {}).get("runbook_json"), None)
    if review:
        groups.append(("runbook-review", "runbook-review", review_chunks(review)))
    ap = autopilot_chunks(store.get_events(autopilot.FEED))
    if autopilot_days is not None:
        days = sorted({c.ref for c in ap})[-autopilot_days:]
        ap = [c for c in ap if c.ref in days]
    groups += _by_scope("autopilot", ap)
    if DECISIONS_PATH.exists():
        groups.append(("decision", "decision", decision_chunks(DECISIONS_PATH.read_text(encoding="utf-8"))))
    return groups


def index_drill(xid: str, embedder=None) -> dict:
    """After a drill: its report, diagnoses and incident timeline, plus the shared documents (all hash-skipped)."""
    if not DRILL_ID.match(xid or ""):
        raise ValueError("not a drill id")
    return _index([*_drill_groups(xid), *_shared_groups(autopilot_days=2)], embedder)


def index_shared(embedder=None) -> dict:
    """The runbook, its latest review, recent Autopilot findings and tech decisions (after a runbook review)."""
    return _index(_shared_groups(autopilot_days=2), embedder)


def _background(what: str, fn: Callable[[], dict]) -> None:
    """Best effort, off the caller's thread: never delays or fails a drill or a request."""
    if not enabled():
        return

    def work() -> None:
        try:
            log.info("knowledge: indexed %s: %s", what, fn())
        except Exception as e:  # noqa: BLE001
            log.warning("knowledge: indexing %s failed: %s", what, e)
    threading.Thread(target=work, name=f"knowledge-{what}", daemon=True).start()


def index_drill_async(xid: str) -> None:
    _background(xid, lambda: index_drill(xid))


def index_shared_async() -> None:
    _background("shared documents", index_shared)


def reindex(embedder=None) -> dict:
    """Full rebuild: every drill and every shared document. Unchanged chunks are skipped, so re-running is cheap."""
    from dr_agent import service
    groups = _shared_groups()
    for r in service.list_exercises():
        groups += _drill_groups(r["PartitionKey"])
    return _index(groups, embedder)


# ------------------------------------------------------------------ retrieval
def search(query: Any, k: Any = 5, source: Any = "any", embedder: Optional[Callable[..., list[list[float]]]] = None) -> dict:
    """Top-k chunks for a question, as tool output: {"hits": [{source, ref, title, snippet, score, link, cite}]}.
    Never raises; bad input and failures come back as {"error": ...}."""
    if not enabled():
        return {"error": "the knowledge index is switched off (KNOWLEDGE_ENABLED=false)"}
    if not isinstance(query, str) or len(query.strip()) < MIN_QUERY_CHARS:
        return {"error": f"query must be a question or keywords of at least {MIN_QUERY_CHARS} characters"}
    query = " ".join(query.split())[:MAX_QUERY_CHARS]
    try:
        k = max(1, min(MAX_K, int(k)))
    except (TypeError, ValueError):
        k = 5
    if source not in ("any", *SOURCES):
        return {"error": f"source must be one of any, {', '.join(SOURCES)}"}
    try:
        vec = (embedder or embed)([query], timeout_s=QUERY_TIMEOUT_S)[0]
    except Exception as e:  # noqa: BLE001
        log.warning("knowledge: query embedding failed: %s", e)
        return {"error": f"the knowledge index is unavailable (embedding failed: {type(e).__name__})"}
    try:
        kw: dict[str, Any] = {"partition_key": source} if source != "any" else {"enable_cross_partition_query": True}
        rows = list(_container().query_items(query=search_query(k), parameters=[{"name": "@v", "value": vec}],
                                             timeout=QUERY_TIMEOUT_S, **kw))
    except Exception as e:  # noqa: BLE001
        log.warning("knowledge: vector query failed: %s", e)
        return {"error": f"the knowledge index is unavailable ({type(e).__name__})"}
    rows = [r for r in rows if isinstance(r.get("score"), (int, float)) and r["score"] >= MIN_SCORE]
    rows.sort(key=lambda r: r["score"], reverse=True)
    rows = rows[:k]
    if not rows:
        return {"hits": [], "note": "nothing in the knowledge index matches this question"}
    per_hit = max(200, SEARCH_BUDGET // len(rows) - 220)
    hits = [{"source": r.get("source"), "ref": r.get("ref"), "title": (r.get("title") or "")[:120],
             "snippet": _snippet(r.get("text") or "", per_hit), "score": round(float(r["score"]), 3),
             "link": r.get("link") or "/", "cite": r.get("cite")} for r in rows]
    while len(hits) > 1 and len(json.dumps(hits, default=str)) > SEARCH_BUDGET:
        hits = hits[:-1]  # drop the least similar
    return {"hits": hits}


def _snippet(text: str, n: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= n else text[:n].rsplit(" ", 1)[0] + " …"


def cites(evidence: Iterable[dict]) -> set[str]:
    """Every citation source a search_knowledge call returned in this turn (what a knowledge citation must match)."""
    out: set[str] = set()
    for e in evidence or ():
        if isinstance(e, dict) and e.get("tool") == "search_knowledge" and isinstance(e.get("output"), dict):
            out.update(h.get("cite") for h in e["output"].get("hits") or [] if isinstance(h, dict) and h.get("cite"))
    return out


# ------------------------------------------------------------------ CLI
def main(argv: Optional[list[str]] = None) -> None:
    p = argparse.ArgumentParser(prog="python -m dr_agent.knowledge", description="Black box knowledge index")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("reindex", help="index the runbook, its review, every drill, Autopilot and tech decisions")
    d = sub.add_parser("drill", help="index one drill (and the shared documents)")
    d.add_argument("xid")
    s = sub.add_parser("search", help="try a query")
    s.add_argument("query")
    s.add_argument("-k", type=int, default=5)
    s.add_argument("--source", default="any", choices=("any", *SOURCES))
    a = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    if a.cmd == "reindex":
        out = reindex()
    elif a.cmd == "drill":
        out = index_drill(a.xid)
    else:
        out = search(a.query, a.k, a.source)
    print(json.dumps(out, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
