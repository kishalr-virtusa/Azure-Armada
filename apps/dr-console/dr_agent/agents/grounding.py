"""Grounding verification: what an agent tells a user or an auditor must be backed by the records (plan WS2).

Pure functions, no I/O. `extract_facts` collects the fact set from the agent's evidence (tool outputs) and the record
payload it was given: drill ids, numbers, durations (normalised to seconds, with the key path they were recorded
under), UTC clock times and dates. The `verify_*` functions extract the same kinds of claims from the agent's output
and report every claim the facts do not support:

- a duration must equal a recorded duration to the precision it is quoted at ("27.74s" ±0.005 s, "30 min" ±30 s);
- a duration bound to a metric ("RTO ... 27.74s") must match a value recorded under that metric's key (rto_*, ...),
  and one quoted as a target ("target RTO of 30 seconds") a value recorded under a target key. Free text in the
  records (event messages, AI summaries) never supports a metric figure: it is where an injected "RTO was 1s" lives;
- bare numbers, drill ids, clock times and dates must appear somewhere in the facts.

`Check` wires a verifier into run_agent as validate(out, ctx). GROUNDING_ENFORCE selects what a failed check does:
shadow (default: trace and log only), enforce (RepairableError: one repair round, then the fallback) or off.
"""
from __future__ import annotations

import json
import logging
import math
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Optional

from dr_agent import trace
from dr_agent.llm import AgentResult, RepairableError

log = logging.getLogger(__name__)

MODES = ("shadow", "enforce", "off")
MAX_LISTED = 8  # unsupported claims kept in a report (trace, UI and the repair prompt stay short)

DRILL_ID = re.compile(r"\bex-(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})(\d{2})\b")
ISO_TS = re.compile(r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?")
ISO_DATE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
CLOCK = re.compile(r"(?<![\d:.])([01]?\d|2[0-3]):([0-5]\d)(?::([0-5]\d)(?:\.\d+)?)?(?![\d:])")
MONTHS = {m: i for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1)}
_MON = r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
DATE_DM = re.compile(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+{_MON}\b(?:,?\s+(\d{{4}}))?", re.I)
DATE_MD = re.compile(rf"\b{_MON}\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?\b(?:,?\s+(\d{{4}}))?", re.I)
LINK = re.compile(r"^/(?:(?:results|exercise)/(ex-\d{8}-\d{6}))?$")

UNITS = {"ms": 0.001, "millisecond": 0.001, "milliseconds": 0.001, "s": 1, "sec": 1, "secs": 1, "second": 1, "seconds": 1,
         "m": 60, "min": 60, "mins": 60, "minute": 60, "minutes": 60, "h": 3600, "hr": 3600, "hrs": 3600, "hour": 3600,
         "hours": 3600}
QTY = re.compile(r"(?<![\w.])(\d+(?:\.\d+)?)\s?-?\s?(milliseconds?|ms|seconds?|secs?|s|minutes?|mins?|m|hours?|hrs?|h)(?![\w])",
                 re.I)
COMPOUND_GAP = re.compile(r"^\s*(?:,?\s*and\s+)?$", re.I)
NUMBER = re.compile(r"(?<![\w./])(?<!\w-)(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d+))?(?![\w]|\.\d)")
METRIC = re.compile(r"\b(rto|rpo|fail[\s-]?back|recovery time|recovery point)\b", re.I)
TARGET = re.compile(r"\b(?:target|goal)s?\b(?:\s*(?:(?:rto|rpo|failback|time|of|is|was|were|the|a|an|its|their)\b|[=:]))*\s*$",
                    re.I)
SENTENCE_END = re.compile(r"[.!?;](?=\s)|\n")
# key-path suffix -> seconds per unit; "_per_s" is a rate (writes per second), not a duration
KEY_UNITS = (("per_s", None), ("_ms", 0.001), ("_s", 1), ("_sec", 1), ("_seconds", 1), ("_min", 60), ("_mins", 60),
             ("_minutes", 60), ("_h", 3600), ("_hours", 3600))
SKIP_KEYS = {"previous_attempt_problems"}  # repair notes are not records


def mode() -> str:
    m = os.environ.get("GROUNDING_ENFORCE", "shadow").strip().lower()
    return m if m in MODES else "shadow"


def _metric(word: str) -> str:
    w = word.lower().replace(" ", "").replace("-", "")
    return {"rto": "rto", "recoverytime": "rto", "rpo": "rpo", "recoverypoint": "rpo"}.get(w, "failback")


def _tol(decimals: Optional[str]) -> float:
    """Half a unit of the last quoted digit: "27.74" is anything that rounds to it."""
    return 0.5 * 10 ** -len(decimals or "")


# ------------------------------------------------------------------ the fact set
@dataclass
class Facts:
    durations: list[tuple[float, str, bool]] = field(default_factory=list)  # (seconds, key path, from free text)
    numbers: list[float] = field(default_factory=list)  # every number in the records, exactly as recorded
    ids: set[str] = field(default_factory=set)
    clock: set[str] = field(default_factory=set)  # "10:52:07" and "10:52" in UTC
    dates: set[tuple[int, int]] = field(default_factory=set)  # (month, day)
    records: int = 0  # how many record sets (tool outputs, payload record lists) the facts came from

    def add_time(self, d: datetime) -> None:
        d = d.astimezone(timezone.utc) if d.tzinfo else d
        self.clock.update({d.strftime("%H:%M:%S"), d.strftime("%H:%M")})
        self.dates.add((d.month, d.day))
        self.numbers.append(float(d.year))


def _unit_of(path: str) -> Optional[float]:
    for seg in reversed(path.split(".")):
        for suffix, factor in KEY_UNITS:
            if seg.endswith(suffix) or seg == suffix.lstrip("_"):
                return factor
    return None


def _walk(v: Any, path: str, facts: Facts) -> None:
    if isinstance(v, dict):
        for k, x in v.items():
            if str(k) not in SKIP_KEYS:
                _walk(x, f"{path}.{k}" if path else str(k), facts)
    elif isinstance(v, (list, tuple)):
        for x in v:  # list items keep their key: rto_breakdown_s [["dns", 30.1], ...] stays in seconds
            _walk(x, path, facts)
    elif isinstance(v, bool) or v is None:
        return
    elif isinstance(v, (int, float)):
        if math.isfinite(v):
            facts.numbers.append(float(v))
            unit = _unit_of(path.lower())
            if unit:
                facts.durations.append((float(v) * unit, path.lower(), False))
    elif isinstance(v, str):
        s = v.strip()
        if s[:1] in "[{":
            try:  # nested JSON: *_json record fields, tool outputs kept as text
                return _walk(json.loads(s), path, facts)
            except ValueError:
                pass
        _text_facts(s, path.lower(), facts)


def _text_facts(text: str, path: str, facts: Facts) -> None:
    """Facts from a string: what a reader of this text would take as stated."""
    for c in scan(text):
        if c.kind == "id":
            facts.ids.add(c.raw)
            y, mo, d, hh, mm, ss = (int(x) for x in DRILL_ID.fullmatch(c.raw).groups())
            facts.clock.update({f"{hh:02d}:{mm:02d}:{ss:02d}", f"{hh:02d}:{mm:02d}"})
            facts.dates.add((mo, d))
            facts.numbers.append(float(y))
        elif c.kind == "ts":
            facts.add_time(c.value)
        elif c.kind == "clock":
            facts.clock.add(c.raw)
        elif c.kind == "date":
            facts.dates.add(c.value)
            if c.year:
                facts.numbers.append(float(c.year))
        elif c.kind == "duration":
            # a string under a duration-like key ("rto": "4m 12s") is structured; text elsewhere is free text
            key = path.rsplit(".", 1)[-1]
            structured = bool(METRIC.search(key.replace("_", " "))) or "target" in key or _unit_of(path) is not None
            facts.durations.append((c.value, path, not structured))
            facts.numbers.append(c.number)
        elif c.kind == "number":
            facts.numbers.append(c.value)


def extract_facts(*sources: Any, evidence: Iterable[dict] = ()) -> Facts:
    """Facts from record payloads (dicts, lists or JSON text) and run_agent evidence [{tool, args, output}]."""
    facts = Facts()
    for src in sources:
        if src:
            _walk(src, "", facts)
            facts.records += len(src) if isinstance(src, list) else 1
    for e in evidence or ():
        out = e.get("output") if isinstance(e, dict) else None
        if out is not None:
            _walk(out, "", facts)
            facts.records += 1
    return facts


# ------------------------------------------------------------------ claims in text
@dataclass
class Claim:
    kind: str  # id | ts | clock | date | duration | number
    raw: str
    value: Any = None
    tol: float = 0.0
    number: float = 0.0  # a duration's number as written
    metric: str = ""  # rto | rpo | failback: the metric this figure is quoted for
    target: bool = False  # quoted as a target ("target RTO of ...")
    change: bool = False  # quoted as a difference ("+8.5 s", "by 8.5 s", "8.5 s slower"), not as the metric itself
    year: Optional[int] = None


def _mask(text: str, spans: list[tuple[int, int]]) -> str:
    chars = list(text)
    for a, b in spans:
        chars[a:b] = " " * (b - a)
    return "".join(chars)


# a duration quoted as a difference between drills: "+8.5 seconds", "(+8.5 s)", "by 8.5 s", "8.5 s slower"
CHANGE_BEFORE = re.compile(r"(?:[+\u2212-]\s*|\bby\s+|\b(?:up|down)\s+|\b(?:increase|decrease|delay|change)d?\s+of\s+)$", re.I)
CHANGE_AFTER = re.compile(r"\s*(?:slower|faster|longer|shorter|more|less|later|earlier)\b", re.I)
CHANGE_PATH = re.compile(r"delta|change|diff", re.I)


def _binding(text: str, start: int, prev_end: int) -> tuple[str, bool]:
    """(metric, is_target) for a figure at `start`: read back to the previous figure or the sentence start."""
    left = max([prev_end] + [m.end() for m in SENTENCE_END.finditer(text, 0, start)])
    window = text[left:start]
    metrics = METRIC.findall(window)
    return (_metric(metrics[-1]) if metrics else ""), bool(TARGET.search(window))


def scan(text: str) -> list[Claim]:
    """Every checkable claim in `text`, in order: drill ids, timestamps, clock times, dates, durations and numbers."""
    text = text or ""
    claims: list[tuple[int, Claim]] = []
    spans: list[tuple[int, int]] = []
    for m in DRILL_ID.finditer(text):
        claims.append((m.start(), Claim("id", m.group(0))))
        spans.append(m.span())
    t = _mask(text, spans)
    for m in ISO_TS.finditer(t):
        try:
            d = datetime.fromisoformat(m.group(0).replace("Z", "+00:00").replace(" ", "T"))
        except ValueError:
            continue
        claims.append((m.start(), Claim("ts", m.group(0), d)))
        spans.append(m.span())
    t = _mask(text, spans)
    for m in ISO_DATE.finditer(t):
        claims.append((m.start(), Claim("date", m.group(0), (int(m.group(2)), int(m.group(3))), year=int(m.group(1)))))
        spans.append(m.span())
    t = _mask(text, spans)
    for m in CLOCK.finditer(t):
        hh, mm, ss = m.group(1), m.group(2), m.group(3)
        claims.append((m.start(), Claim("clock", f"{int(hh):02d}:{mm}" + (f":{ss}" if ss else ""))))
        spans.append(m.span())
    t = _mask(text, spans)
    for rx, day_g, mon_g in ((DATE_DM, 1, 2), (DATE_MD, 2, 1)):
        for m in rx.finditer(t):
            day, mon = int(m.group(day_g)), MONTHS[m.group(mon_g)[:3].lower()]
            if 1 <= day <= 31:
                year = int(m.group(3)) if m.group(3) else None
                claims.append((m.start(), Claim("date", m.group(0), (mon, day), year=year)))
                spans.append(m.span())
        t = _mask(text, spans)
    # durations, merging compound forms ("4m 12s", "1 minute and 5 seconds") into one figure
    parts = [(m.start(), m.end(), m.group(1), UNITS[m.group(2).lower()]) for m in QTY.finditer(t)]
    merged: list[list] = []
    for a, b, num, unit in parts:
        last = merged[-1] if merged else None
        if last and unit < last[4] and COMPOUND_GAP.match(t[last[1]:a]):
            last[1], last[2], last[4] = b, last[2] + float(num) * unit, unit
            last[3] = _tol(num.partition(".")[2]) * unit
        else:
            merged.append([a, b, float(num) * unit, _tol(num.partition(".")[2]) * unit, unit, float(num)])
    prev_end = 0
    for a, b, secs, tol, _, number in merged:
        metric, target = _binding(t, a, prev_end)
        change = bool(CHANGE_BEFORE.search(t[max(prev_end, a - 24):a]) or CHANGE_AFTER.match(t[b:b + 24]))
        claims.append((a, Claim("duration", text[a:b], secs, tol, number=number, metric=metric, target=target,
                                change=change)))
        spans.append((a, b))
        prev_end = b
    t = _mask(text, spans)
    for m in NUMBER.finditer(t):
        claims.append((m.start(), Claim("number", m.group(0), float(m.group(1).replace(",", "") + "." + (m.group(2) or "0")),
                                        _tol(m.group(2)))))
    return [c for _, c in sorted(claims, key=lambda x: x[0])]


# ------------------------------------------------------------------ checking claims against facts
def _supported(c: Claim, facts: Facts) -> bool:
    if c.kind == "id":
        return c.raw in facts.ids
    if c.kind == "ts":
        d = c.value.astimezone(timezone.utc) if c.value.tzinfo else c.value
        return d.strftime("%H:%M") in facts.clock and (d.month, d.day) in facts.dates
    if c.kind == "clock":
        return c.raw in facts.clock
    if c.kind == "date":
        return c.value in facts.dates and (c.year is None or float(c.year) in facts.numbers)
    if c.kind == "duration":
        for secs, path, free_text in facts.durations:
            if abs(secs - c.value) > c.tol + 1e-9:
                continue
            if c.metric and (free_text or c.metric not in path.replace("fail_back", "failback")):
                # a metric figure needs a value recorded under that metric; a difference may instead match a
                # recorded change ("+8.5 s" = history.biggest_segment_change.delta_s)
                if not (c.change and not free_text and CHANGE_PATH.search(path)):
                    continue
            if c.target and "target" not in path:
                continue  # a target needs a recorded target
            return True
        return False
    v = abs(c.value)  # a number may be quoted without its sign ("improved by 20.5%")
    return any(abs(abs(x) - v) <= c.tol + 1e-9 for x in facts.numbers)


def _describe(c: Claim) -> str:
    what = {"id": "drill id", "ts": "time", "clock": "time", "date": "date", "duration": "", "number": ""}[c.kind]
    label = " ".join(x for x in (("target" if c.target else ""), c.metric.upper() if c.metric else "") if x)
    return f'"{c.raw}"' + (f" ({label})" if label else f" ({what})" if what else "")


def check_text(text: str, facts: Facts) -> tuple[int, list[str]]:
    """(claims checked, descriptions of unsupported claims) for one piece of text."""
    claims = scan(text)
    return len(claims), [_describe(c) for c in claims if not _supported(c, facts)]


def _report(checked: int, unsupported: list[str], facts: Facts) -> dict:
    unique = list(dict.fromkeys(unsupported))
    return {"checked": checked, "supported": checked - len(unsupported), "unsupported": unique[:MAX_LISTED],
            "records": facts.records, "verified": not unique}


# ------------------------------------------------------------------ agent verifiers
def verify_blackbox(answer: Any, facts: Facts) -> dict:
    """Black box: every id, number, time and date in the answer and in each citation's detail is in the facts; each
    link points at a drill the citation names; when found_in_records is false the answer says "not in the records"
    (or is the fixed refusal "I can only answer from the DR records.")."""
    checked, bad = check_text(answer.answer, facts)
    for c in answer.citations:
        n, b = check_text(c.detail, facts)
        checked, bad = checked + n + 1, bad + b
        m = LINK.match(c.link or "")
        if not m:
            bad.append(f'citation link "{c.link}" (not a console page)')
        elif m.group(1) and (m.group(1) not in facts.ids or m.group(1) not in f"{c.source} {c.detail}"):
            bad.append(f'citation link "{c.link}" (does not match the cited drill)')
    if not answer.found_in_records:
        checked += 1
        said = answer.answer.lower()
        if "not in the records" not in said and "only answer from the dr records" not in said:  # or the fixed refusal
            bad.append('found_in_records is false but the answer does not say "That is not in the records."')
    return _report(checked, bad, facts)


VERDICT_NEG = re.compile(r"\b(?:not (?:been )?met|not meet|missed|miss(?:es|ing)|exceed(?:ed|s|ing)|breach(?:ed|es)?|"
                         r"didn't meet|failed to meet|outside (?:the |its |their )?(?:target|objective)s?|above (?:the |its )?target)\b",
                         re.I)
VERDICT_POS = re.compile(r"\b(?:met|meets|meeting|achieved|within (?:the |its |their )?(?:target|objective)s?|"
                         r"under (?:the |its )?target)\b", re.I)
CLAUSE = re.compile(r"[.!?;]\s+|\n|,?\s+\b(?:but|while|whereas|although|however)\b", re.I)
BOTH = re.compile(r"\b(?:both|all) (?:targets|objectives)\b", re.I)
OVERALL = re.compile(r"\b(?:overall|verdict|result)\b[^.;]*?\b(passed|failed|pass|fail)\b", re.I)


def verify_report(narrative: Any, measured: dict) -> dict:
    """Reporter: every figure in the narrative is a measured value (RTO/RPO/failback figures are values recorded for
    that metric) and the met/missed wording agrees with results.rto_met / rpo_met."""
    facts = extract_facts(measured)
    r = measured.get("results") or {}
    met = {"rto": r.get("rto_met"), "rpo": r.get("rpo_met")}
    texts = [narrative.executive_summary, *narrative.what_went_well, *narrative.issues,
             *(x.item for x in narrative.remediation)]
    checked, bad = 0, []
    for text in texts:
        n, b = check_text(text, facts)
        checked, bad = checked + n, bad + b
        for clause in CLAUSE.split(text or ""):
            names = {_metric(w) for w in METRIC.findall(clause)} & set(met)
            if BOTH.search(clause):
                names = set(met)
            claim = False if VERDICT_NEG.search(clause) else True if VERDICT_POS.search(clause) else None
            for name in sorted(names):
                if claim is not None and met[name] is not None:
                    checked += 1
                    if claim != bool(met[name]):
                        bad.append(f'"{clause.strip()[:80]}" says the {name.upper()} target was '
                                   f'{"met" if claim else "missed"}, the measurement says {"met" if met[name] else "missed"}')
        o = OVERALL.search(text or "")
        if o and None not in met.values():
            checked += 1
            passed = o.group(1).lower().startswith("pass")
            if passed != bool(met["rto"] and met["rpo"]):
                bad.append(f'overall verdict "{o.group(1)}" disagrees with the measured RTO/RPO results')
    return _report(checked, bad, facts)


def verify_runbook(review: Any, steps: Iterable[tuple[int, str]], inventory_names: Iterable[str]) -> dict:
    """Runbook Reviewer: each reviewed step number exists in the runbook (0 = a missing action) and every resource
    name in a suggested fix exists in the live inventory."""
    from dr_agent.agents.runbook import NAME_RE
    numbers = {no for no, _ in steps}
    names = {n.lower() for n in inventory_names if n}
    facts = Facts(records=1 + bool(names))
    checked, bad = 0, []
    for s in review.steps:
        checked += 1
        if s.step_no == 0 and s.verdict != "missing":
            bad.append(f"step 0 is only for missing actions (verdict {s.verdict})")
        elif s.step_no != 0 and s.step_no not in numbers:
            bad.append(f"step {s.step_no} (not in the runbook)")
        for ref in sorted({(a or b).lower() for a, b in NAME_RE.findall(s.suggested or "")}):
            checked += 1
            if ref not in names:
                bad.append(f'"{ref}" in the fix for step {s.step_no} (not in the inventory)')
    return _report(checked, bad, facts)


# ------------------------------------------------------------------ run_agent wiring
def problems(report: dict) -> list[str]:
    """The repair round's instructions, one per unsupported claim."""
    return [f"{u} is not supported by the records. Quote recorded values exactly, or leave the claim out."
            for u in report.get("unsupported", [])]


def summary(report: dict) -> str:
    if not report.get("checked"):
        return "Grounding check: no figures, ids or times to check"
    if report.get("verified"):
        return f"Grounding check: {report['checked']}/{report['checked']} facts found in the records"
    return (f"Grounding check: {report['supported']}/{report['checked']} facts found in the records; not supported: "
            + "; ".join(report["unsupported"]))


class Check:
    """validate(out, ctx) hook for run_agent: verifies the answer, traces the result and, in enforce mode, asks for
    one repair. `verify(out, ctx) -> report`. After run_agent, `attach(result)` stores the report on the result."""

    def __init__(self, agent: str, verify: Callable[[Any, dict], dict]):
        self.agent, self.verify, self.mode = agent, verify, mode()
        self.report: dict = {}

    def __call__(self, out: Any, ctx: dict) -> None:
        if self.mode == "off":
            return
        try:
            self.report = {**self.verify(out, ctx), "mode": self.mode}
        except Exception as e:  # noqa: BLE001 - a checker bug must never cost the user the answer
            log.exception("agent=%s grounding check failed", self.agent)
            self.report = {"checked": 0, "supported": 0, "unsupported": [], "verified": None, "mode": self.mode,
                           "error": f"{type(e).__name__}: {e}"[:200]}
            return
        ok = self.report["verified"]
        trace.emit("check", summary(self.report)[:300], "" if ok else f"{self.mode} mode",
                   status="ok" if ok else "warn", data=self.report)
        if not ok:
            log.warning("agent=%s grounding mode=%s unsupported=%s", self.agent, self.mode, self.report["unsupported"])
            if self.mode == "enforce":
                raise RepairableError(problems(self.report))

    def attach(self, res: AgentResult, ctx: dict) -> AgentResult:
        """The accepted model answer keeps the report of its last check; a fallback answer is checked here (no trace)."""
        if self.mode == "off":
            return res
        if res.source != "llm" or not self.report:
            try:
                self.report = {**self.verify(res.output, ctx), "mode": self.mode}
            except Exception:  # noqa: BLE001
                log.exception("agent=%s grounding check of the fallback failed", self.agent)
                return res
        res.grounding = self.report
        return res
