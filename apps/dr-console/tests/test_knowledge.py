"""Black box knowledge index (RAG): chunking, idempotent ingestion, the vector query, tool guardrails and citations."""
import copy
import hashlib
import json
import math
import re

import pytest

from dr_agent import knowledge, service
from dr_agent.agents import blackbox, tools
from dr_agent.models import BlackboxAnswer, Citation
from dr_agent.tools import store

from conftest import _RESERVED

XID = "ex-20261017-134920"
DIAG = {"likely_cause": "Traffic Manager's Region B endpoint (endpoint-b) is Disabled (surprise chaos injection).",
        "evidence": ["Recent chaos event: Region B Traffic Manager endpoint disabled"], "recommended_action": "retry",
        "fix_action": "tm_set_endpoint", "fix_params": [{"key": "region", "value": "b"}, {"key": "enabled", "value": "true"}]}
EVENTS = [
    {"RowKey": "000001", "ts": "2026-10-17T13:49:20+00:00", "kind": "info", "step_id": None, "message": "Drill started"},
    {"RowKey": "000002", "ts": "2026-10-17T13:50:01+00:00", "kind": "chaos", "step_id": None,
     "message": "Surprise fault injected: Region B Traffic Manager endpoint disabled"},
    {"RowKey": "000003", "ts": "2026-10-17T13:51:10+00:00", "kind": "diagnosis", "step_id": "val-1",
     "message": DIAG["likely_cause"], "data": DIAG, "data_json": json.dumps(DIAG)},
    {"RowKey": "000004", "ts": "2026-10-17T13:52:00+00:00", "kind": "remediation", "step_id": "val-1",
     "message": "Approved fix applied: tm_set_endpoint {'region': 'b', 'enabled': 'true'}"},
]
NARRATIVE = {"executive_summary": "The drill recovered in Southeast Asia after a surprise Traffic Manager fault.",
             "what_went_well": ["The Validator found the disabled endpoint."],
             "issues": ["Traffic Manager endpoint-b was disabled by the surprise fault."],
             "remediation": [{"item": "Alert when a Traffic Manager endpoint is disabled", "owner": "Platform",
                              "priority": "high"}]}


# ------------------------------------------------------------------ fakes
def fake_embed(texts, timeout_s=None):
    """Deterministic bag-of-words vectors: texts sharing words are similar, as with a real embedding model."""
    out = []
    for t in texts:
        v = [0.0] * knowledge.DIMS
        for w in re.findall(r"[a-z0-9]+", t.lower()):
            v[int(hashlib.md5(w.encode()).hexdigest(), 16) % knowledge.DIMS] += 1.0
        n = math.sqrt(sum(x * x for x in v)) or 1.0
        out.append([x / n for x in v])
    return out


SEARCH = re.compile(r'SELECT TOP (?P<k>\d+) (?P<proj>(?:c\["\w+"\], )+)VectorDistance\(c\["embedding"\], @v\) AS score '
                    r'FROM c ORDER BY VectorDistance\(c\["embedding"\], @v\)')


class FakeKnowledge:
    """The knowledge container: the two query shapes knowledge.py issues, cosine similarity like the service."""

    def __init__(self):
        self.docs: dict[tuple[str, str], dict] = {}
        self.queries: list[tuple[str, dict]] = []
        self.upserts = 0

    def upsert_item(self, body):
        assert set(body) == {"id", "pk", "source", "ref", "title", "text", "embedding", "updated_at", "hash", "scope",
                             "link", "cite"}
        assert not re.search(r"[/\\?#]", body["id"]) and body["pk"] == body["source"] in knowledge.SOURCES
        assert len(body["embedding"]) == knowledge.DIMS
        self.upserts += 1
        self.docs[(body["pk"], body["id"])] = copy.deepcopy(body)

    def delete_item(self, item, partition_key):
        del self.docs[(partition_key, item)]

    def query_items(self, query, parameters=None, partition_key=None, enable_cross_partition_query=False, **kw):
        self.queries.append((query, {"partition_key": partition_key, **kw}))
        assert partition_key is not None or enable_cross_partition_query
        for f in re.findall(r"\bc\.(\w+)", query):  # the real service rejects reserved words written as c.<name>
            assert f.upper() not in _RESERVED, query
        params = {p["name"]: p["value"] for p in parameters or []}
        rows = [d for (pk, _), d in sorted(self.docs.items()) if partition_key is None or pk == partition_key]
        if query == knowledge.SCOPE_QUERY:
            return [{"id": d["id"], "hash": d["hash"]} for d in rows if d["scope"] == params["@scope"]]
        m = SEARCH.fullmatch(query)
        assert m, f"query shape not supported by the fake: {query}"
        fields = re.findall(r'c\["(\w+)"\]', m["proj"])
        assert "embedding" not in fields  # vectors are never sent back
        v = params["@v"]
        scored = [({f: d[f] for f in fields if f in d}, sum(a * b for a, b in zip(d["embedding"], v))) for d in rows]
        scored.sort(key=lambda x: x[1], reverse=True)
        return [{**r, "score": s} for r, s in scored[:int(m["k"])]]


@pytest.fixture
def kn(monkeypatch):
    monkeypatch.setenv("KNOWLEDGE_ENABLED", "true")
    box = FakeKnowledge()
    monkeypatch.setattr(knowledge, "_container", lambda: box)
    monkeypatch.setattr(knowledge, "embed", fake_embed)
    return box


# ------------------------------------------------------------------ chunking
def test_split_text_keeps_short_text_whole_and_splits_long_text_with_overlap():
    assert knowledge.split_text("") == [] and knowledge.split_text("one paragraph") == ["one paragraph"]
    paras = [f"Paragraph {i}: " + " ".join(f"word{i}-{j}" for j in range(40)) for i in range(20)]
    text = "\n\n".join(paras)
    pieces = knowledge.split_text(text)
    assert len(pieces) > 2 and all(len(p) <= knowledge.CHUNK_CHARS for p in pieces)
    for i, p in enumerate(paras):  # nothing is lost
        assert any(p in piece for piece in pieces), i
    for a, b in zip(pieces, pieces[1:]):  # each piece starts with the tail of the one before
        assert b.split("\n")[0] in a
    long_line = "x" * 5000  # no break at all: still cut to size
    assert all(len(p) <= knowledge.CHUNK_CHARS for p in knowledge.split_text(long_line))


def test_runbook_is_chunked_by_step_with_stable_ids_and_citations():
    md = service.RUNBOOK_PATH.read_text(encoding="utf-8")
    chunks = knowledge.runbook_chunks(md)
    refs = [c.ref for c in chunks]
    assert [f"step {i}" for i in range(1, 13)] == [r for r in refs if r.startswith("step ")]
    assert {"overview", "pre-requisites", "contacts"} <= set(refs)
    assert len({c.id for c in chunks}) == len(chunks)
    step10 = next(c for c in chunks if c.ref == "step 10")
    assert step10.id == "runbook.step-10.0" and step10.cite == "knowledge: Runbook step 10"
    assert "set-primary" in step10.text and step10.title == "Runbook: Step 10: Initiate Planned Failback"
    assert all(len(c.text) <= knowledge.CHUNK_CHARS for c in chunks)
    assert knowledge.runbook_chunks(md) == chunks  # deterministic


def test_review_chunks_index_findings_and_the_updated_runbook():
    review = {"steps": [{"step_no": 2, "text": "Check fd-payments", "verdict": "stale", "reason": "no Front Door",
                         "suggested": "Use Traffic Manager tm-dra"},
                        {"step_no": 1, "text": "ok", "verdict": "valid", "reason": "", "suggested": ""}],
              "updated_runbook_md": "# Runbook\n\n### Step 1: Confirm\n\nCheck tm-dra.\n"}
    chunks = knowledge.review_chunks(review)
    findings = next(c for c in chunks if c.ref == "findings")
    assert "Step 2 (stale)" in findings.text and "tm-dra" in findings.text and "Step 1" not in findings.text
    assert [c.cite for c in chunks if c.ref == "step 1"] == ["knowledge: Runbook review step 1"]


def test_drill_chunks_cover_report_diagnosis_and_incident_timeline():
    m = {"status": "completed", "report_narrative_json": json.dumps(NARRATIVE)}
    chunks = knowledge.drill_chunks(XID, m, EVENTS)
    by = {c.source: c for c in chunks}
    assert set(by) == {"report", "diagnosis", "event"} and all(c.link == f"/results/{XID}" for c in chunks)
    assert "Alert when a Traffic Manager endpoint is disabled" in by["report"].text
    assert "endpoint-b" in by["diagnosis"].text and "tm_set_endpoint (region=b, enabled=true)" in by["diagnosis"].text
    assert by["diagnosis"].cite == f"knowledge: Diagnosis · {XID} val-1"
    assert "Drill started" not in by["event"].text and "13:50:01 chaos" in by["event"].text


def test_older_reports_fall_back_to_the_report_page():
    page = ("<h2>Executive summary</h2><p style='x'>Recovered &amp; verified.</p><h2>What went well</h2><ul><li>-</li></ul>"
            "<h2>Issues</h2><ul><li>DNS TTL was 300 s</li></ul>")
    m = {"report_url": "https://blob/x", "remediation_json": json.dumps(NARRATIVE["remediation"])}
    report = [c for c in knowledge.drill_chunks(XID, m, [], lambda: page) if c.source == "report"][0]
    assert "Recovered & verified." in report.text and "DNS TTL was 300 s" in report.text
    assert "What went well" not in report.text and "[high] Alert when" in report.text


def test_autopilot_findings_are_grouped_per_day_and_deduplicated():
    fail = {"go": False, "failing": {"Traffic Manager endpoints": "endpoint B Degraded"}, "ai": {"summary": "B is degraded"}}
    evs = [{"ts": f"2026-10-19T10:{m:02d}:00+00:00", "message": "Autopilot (schedule): 1 check(s) failing", "data": fail}
           for m in (5, 15, 25)]
    evs.append({"ts": "2026-10-19T11:00:00+00:00", "message": "Autopilot (schedule): all checks pass",
                "data": {"go": True, "failing": {}}})
    (c,) = knowledge.autopilot_chunks(evs)
    assert c.ref == "2026-10-19" and "10:05:00-10:25:00 UTC (3 run(s))" in c.text and c.text.count("Degraded") == 1
    assert "1 run(s) found all checks passing." in c.text


# ------------------------------------------------------------------ ingestion
def test_ingestion_is_idempotent_and_removes_stale_chunks(kn):
    calls = []

    def counting(texts):
        calls.append(len(texts))
        return fake_embed(texts)

    chunks = knowledge.runbook_chunks(service.RUNBOOK_PATH.read_text(encoding="utf-8"))
    first = knowledge.upsert_chunks(chunks, "runbook", "runbook", counting)
    assert first == {"embedded": len(chunks), "unchanged": 0, "deleted": 0} and kn.upserts == len(chunks)
    again = knowledge.upsert_chunks(chunks, "runbook", "runbook", counting)
    assert again == {"embedded": 0, "unchanged": len(chunks), "deleted": 0} and calls == [len(chunks)]  # no embedding
    changed = [*chunks[:-2], knowledge.Chunk(**{**chunks[-2].__dict__, "text": chunks[-2].text + " Updated."})]
    third = knowledge.upsert_chunks(changed, "runbook", "runbook", counting)
    assert third == {"embedded": 1, "unchanged": len(chunks) - 2, "deleted": 1} and len(kn.docs) == len(chunks) - 1
    assert all(q == knowledge.SCOPE_QUERY and o["partition_key"] == "runbook" for q, o in kn.queries)


def test_reindex_builds_the_whole_index_from_the_records(kn, cosmos, monkeypatch):
    store.upsert_exercise(XID, {"status": "completed", "t0": "2026-10-17T13:49:20+00:00",
                                "report_narrative_json": json.dumps(NARRATIVE)})
    store.upsert_exercise("phase1", {"runbook_json": json.dumps({"steps": [], "updated_runbook_md": ""})})
    for e in EVENTS:
        store.add_event(XID, e["kind"], e["message"], e["step_id"], e.get("data"))
    store.add_event("autopilot", "error", "Autopilot (schedule): 1 check(s) failing",
                    data={"go": False, "failing": {"Traffic Manager endpoints": "B Degraded"}})
    monkeypatch.setattr(store, "get_report", lambda xid: None)
    out = knowledge.reindex()
    assert out["failed"] == 0 and out["embedded"] == len(kn.docs) > 15
    assert {d["source"] for d in kn.docs.values()} >= {"runbook", "report", "diagnosis", "event", "autopilot"}
    assert knowledge.reindex() == {"embedded": 0, "unchanged": out["embedded"], "deleted": 0, "failed": 0}
    hits = knowledge.search("Has the Traffic Manager endpoint-b disabled problem happened before? How was it fixed?")["hits"]
    assert XID in hits[0]["ref"] and hits[0]["link"] == f"/results/{XID}"
    top = knowledge.search("failing back the database planned failover set-primary", 3, "runbook")["hits"][0]
    assert top["cite"] == "knowledge: Runbook step 10"


def test_a_failing_group_does_not_stop_the_rest(kn):
    def flaky(texts):
        if any("boom" in t for t in texts):
            raise RuntimeError("429")
        return fake_embed(texts)
    good = knowledge.Chunk("runbook", "step 1", "t", "fine", "runbook")
    bad = knowledge.Chunk("decision", "x", "t", "boom", "decision")
    out = knowledge._index([("decision", "decision", [bad]), ("runbook", "runbook", [good])], flaky)
    assert out == {"embedded": 1, "unchanged": 0, "deleted": 0, "failed": 1}


def test_report_hook_is_best_effort(monkeypatch):
    from dr_agent.engine import reporting
    started = []
    monkeypatch.setattr(knowledge, "index_drill_async", started.append)
    reporting._index_knowledge(XID)
    assert started == [XID]
    monkeypatch.setattr(knowledge, "index_drill_async", lambda xid: 1 / 0)
    reporting._index_knowledge(XID)  # never raises into the drill
    monkeypatch.setenv("KNOWLEDGE_ENABLED", "false")
    monkeypatch.setattr(knowledge, "index_drill", lambda xid: pytest.fail("indexing is switched off"))
    knowledge._background(XID, lambda: knowledge.index_drill(XID))


# ------------------------------------------------------------------ retrieval guardrails
def _seed(kn, n=12):
    chunks = [knowledge.Chunk("runbook", f"step {i}", f"Runbook: Step {i}", f"step {i} " + "failback database " * 90,
                              "runbook") for i in range(n)]
    knowledge.upsert_chunks(chunks, "runbook", "runbook")


def test_search_query_shape_limits_and_output_budget(kn):
    _seed(kn)
    raw = tools.call("blackbox", "search_knowledge", json.dumps({"query": "failback database", "k": 50, "source": "any"}))
    out = json.loads(raw)
    q, opts = kn.queries[-1]
    assert q.startswith(f"SELECT TOP {knowledge.MAX_K} ") and "VectorDistance" in q and 'c["ref"]' in q
    assert opts["timeout"] == knowledge.QUERY_TIMEOUT_S and opts["partition_key"] is None
    assert len(raw) <= 4000 and "truncated" not in out and 1 <= len(out["hits"]) <= knowledge.MAX_K
    h = out["hits"][0]
    assert set(h) == {"source", "ref", "title", "snippet", "score", "link", "cite"} and h["snippet"].endswith("…")
    assert [x["score"] for x in out["hits"]] == sorted((x["score"] for x in out["hits"]), reverse=True)
    knowledge.search("failback", 2, "runbook")
    assert kn.queries[-1][0].startswith("SELECT TOP 2 ") and kn.queries[-1][1]["partition_key"] == "runbook"


@pytest.mark.parametrize("args, error", [
    ({"query": "", "k": 3, "source": "any"}, "at least"),
    ({"query": "  a ", "k": 3, "source": "any"}, "at least"),
    ({"query": 42, "k": 3, "source": "any"}, "at least"),
    ({"query": "failback", "k": 3, "source": "secrets"}, "source must be one of"),
])
def test_bad_input_comes_back_as_an_error(kn, args, error):
    assert error in json.loads(tools.call("blackbox", "search_knowledge", json.dumps(args)))["error"]
    assert not kn.queries


def test_long_queries_are_cut_and_failures_never_raise(kn, monkeypatch):
    seen = []
    monkeypatch.setattr(knowledge, "embed", lambda texts, timeout_s=None: seen.extend(texts) or fake_embed(texts))
    assert knowledge.search("why " * 400)["hits"] == [] and len(seen[0]) == knowledge.MAX_QUERY_CHARS
    monkeypatch.setattr(knowledge, "embed", lambda *a, **k: (_ for _ in ()).throw(TimeoutError("slow")))
    assert "embedding failed: TimeoutError" in knowledge.search("failback")["error"]
    monkeypatch.setattr(knowledge, "embed", fake_embed)
    monkeypatch.setattr(kn, "query_items", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("vector search off")))
    assert "unavailable (RuntimeError)" in knowledge.search("failback")["error"]
    monkeypatch.setenv("KNOWLEDGE_ENABLED", "false")
    assert "switched off" in knowledge.search("failback")["error"]


def test_low_similarity_hits_are_dropped(kn):
    _seed(kn, 2)
    assert knowledge.search("completely unrelated words quantum banana")["hits"] == []


def test_search_tool_is_a_strict_blackbox_tool():
    d = next(x for x in tools.definitions("blackbox") if x["name"] == "search_knowledge")
    p = d["parameters"]
    assert d["strict"] and p["additionalProperties"] is False and sorted(p["required"]) == ["k", "query", "source"]
    assert p["properties"]["source"]["enum"] == ["any", *knowledge.SOURCES]
    for agent in ("validator", "readiness"):
        assert "search_knowledge" not in tools.TOOLSETS[agent]


# ------------------------------------------------------------------ citations
HIT = {"source": "runbook", "ref": "step 10", "title": "Runbook: Step 10: Initiate Planned Failback",
       "snippet": "Perform a planned failover back to primary region", "score": 0.61, "link": "/",
       "cite": "knowledge: Runbook step 10"}


def _answer(source):
    return BlackboxAnswer(answer="The runbook says to perform a planned failover back to the primary region.",
                          citations=[Citation(source=source, detail="Perform a planned failover back to primary region",
                                              link="/")],
                          confidence="high", found_in_records=True)


def test_knowledge_citation_must_be_among_this_turns_search_results():
    ev = [{"tool": "search_knowledge", "args": {"query": "failback"}, "output": {"hits": [HIT]}}]
    ok = blackbox.verify(_answer("knowledge: Runbook step 10"), {"payload": {}, "evidence": ev})
    assert ok["verified"] and ok["knowledge_citations"] == 1
    bad = blackbox.verify(_answer("knowledge: Runbook step 3"), {"payload": {}, "evidence": ev})
    assert not bad["verified"] and "not among this turn's search results" in bad["unsupported"][0]
    # cited from an earlier turn: the history is not evidence
    payload = {"history": [{"question": "q", "answer": "knowledge: Runbook step 10"}]}
    assert not blackbox.verify(_answer("knowledge: Runbook step 10"), {"payload": payload, "evidence": []})["verified"]
    # exact-tool citations are unchanged
    plain = blackbox.verify(_answer("current state"), {"payload": {}, "evidence": []})
    assert plain["verified"] and "knowledge_citations" not in plain


def test_numbers_from_knowledge_text_never_support_a_metric():
    """A runbook "RTO Target: 30 minutes" snippet is free text: it cannot back a metric figure in the answer."""
    hit = {**HIT, "snippet": "RTO Target: 30 minutes. The last test measured RTO 12.5s."}
    ev = [{"tool": "search_knowledge", "args": {}, "output": {"hits": [hit]}}]
    out = BlackboxAnswer(answer="The latest drill measured an RTO of 12.5s.",
                         citations=[Citation(source="knowledge: Runbook step 10", detail="the last test", link="/")],
                         confidence="high", found_in_records=True)
    r = blackbox.verify(out, {"payload": {}, "evidence": ev})
    assert not r["verified"] and '"12.5s" (RTO)' in r["unsupported"]
