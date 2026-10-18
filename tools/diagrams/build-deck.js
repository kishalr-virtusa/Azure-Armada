const pptxgen = require("pptxgenjs");
const React = require("react");
const ReactDOMServer = require("react-dom/server");
const sharp = require("sharp");
const fa = require("react-icons/fa");

// Palette (docs/design/DESIGN.md): ink-violet surfaces, Virtusa purple as the single accent, amber/red for risk
const C = {
  navy: "171721", navy2: "2A2340", teal: "5E2CFE", tealLight: "EFE9FF",
  amber: "F2A541", amberLight: "FDF0DB", red: "D64545",
  ink: "1B2635", muted: "5B6B7F", line: "C9D3DE", card: "F1F5F9", white: "FFFFFF",
};
const HF = "Calibri", BF = "Calibri";

async function icon(name, color, size = 256) {
  const svg = ReactDOMServer.renderToStaticMarkup(
    React.createElement(fa[name], { color: "#" + color, size: String(size) }));
  const buf = await sharp(Buffer.from(svg)).png().toBuffer();
  return "image/png;base64," + buf.toString("base64");
}
const shadow = () => ({ type: "outer", color: "000000", blur: 6, offset: 2, angle: 90, opacity: 0.12 });

(async () => {
  const pres = new pptxgen();
  pres.layout = "LAYOUT_WIDE"; // 13.33 x 7.5
  pres.title = "Disaster Recovery (DR) Agent: Autonomous Disaster Recovery Validation";
  pres.author = "Team Azure Armada";
  let slideNo = 0;
  const add = () => { slideNo++; return pres.addSlide(); };

  const I = {};
  const need = {
    shield: "FaShieldAlt", cal: "FaCalendarTimes", drift: "FaRandom", book: "FaBook", clock: "FaStopwatch",
    audit: "FaClipboardCheck", graph: "FaProjectDiagram", search: "FaSearch", check: "FaCheckDouble",
    file: "FaFileAlt", play: "FaPlay", heart: "FaHeartbeat", tools: "FaTools", robot: "FaRobot",
    user: "FaUserCheck", key: "FaKey", lock: "FaLock", history: "FaHistory", chart: "FaChartLine",
    cloud: "FaCloud", db: "FaDatabase", bolt: "FaBolt", gavel: "FaGavel", sync: "FaSyncAlt",
    hand: "FaHandshake", warn: "FaExclamationTriangle", brain: "FaBrain", branch: "FaCodeBranch",
    route: "FaRoute", eye: "FaEye", trophy: "FaTrophy", server: "FaServer", undo: "FaUndo",
    globe: "FaGlobe", hdd: "FaHdd", flask: "FaFlask", power: "FaPowerOff",
  };
  for (const [k, n] of Object.entries(need)) {
    I[k] = await icon(n, C.white);
    I[k + "T"] = await icon(n, C.teal);
    I[k + "A"] = await icon(n, C.amber);
    I[k + "N"] = await icon(n, C.navy);
  }

  const title = (s, t, sub, dark = false) => {
    s.addText(t, { x: 0.6, y: 0.35, w: 12.1, h: 0.75, fontFace: HF, fontSize: 32, bold: true,
      color: dark ? C.white : C.navy, margin: 0, isTextBox: true });
    if (sub) s.addText(sub, { x: 0.6, y: 1.08, w: 12.1, h: 0.4, fontFace: BF, fontSize: 15,
      color: dark ? "C9C3E6" : C.muted, italic: true, margin: 0, isTextBox: true });
  };
  const circ = (s, img, x, y, d, fill) => {
    s.addShape(pres.shapes.OVAL, { x, y, w: d, h: d, fill: { color: fill }, line: { color: fill } });
    const p = d * 0.25;
    s.addImage({ data: img, x: x + p, y: y + p, w: d - 2 * p, h: d - 2 * p });
  };
  const card = (s, x, y, w, h, fill = C.card, sh = true) =>
    s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x, y, w, h, rectRadius: 0.08,
      fill: { color: fill }, line: { color: fill }, ...(sh ? { shadow: shadow() } : {}) });
  const footer = (s, _n, dark = false) =>
    s.addText(`DR Agent: Autonomous DR Validation  ·  Team Azure Armada  ·  Azure Day 2026 Hackathon   ${slideNo}`, {
      x: 0.6, y: 7.08, w: 12.1, h: 0.3, fontFace: BF, fontSize: 9, color: dark ? "9C95BD" : "8C99AA",
      align: "right", margin: 0, isTextBox: true });

  // ---------- 1. Title ----------
  let s = add(); s.background = { color: C.navy };
  circ(s, I.shield, 0.6, 0.7, 1.0, C.teal);
  s.addText("AZURE DAY 2026 HACKATHON  ·  DISASTER RECOVERY AGENT", { x: 1.85, y: 0.95, w: 9, h: 0.5,
    fontFace: BF, fontSize: 13, bold: true, color: "B9A8FF", charSpacing: 2, margin: 0, isTextBox: true });
  s.addText("Disaster Recovery (DR) Agent", { x: 0.6, y: 2.1, w: 12, h: 1.3, fontFace: HF, fontSize: 54, bold: true,
    color: C.white, margin: 0, isTextBox: true });
  s.addText("Autonomous Disaster Recovery Validation", { x: 0.6, y: 3.35, w: 12, h: 0.7,
    fontFace: HF, fontSize: 28, color: "E4DFF7", margin: 0, isTextBox: true });
  s.addText("Built 100% on Azure. AI agents that validate DR by executing real tests: fail over, measure RTO/RPO, fail back, and prove you can recover, on any day instead of once a year.",
    { x: 0.6, y: 4.3, w: 9.5, h: 0.9, fontFace: BF, fontSize: 17, color: "C9C3E6", italic: true, margin: 0, isTextBox: true });
  const steps1 = [["Fail over", I.sync], ["Measure", I.clock], ["Fail back", I.undo], ["Prove", I.audit]];
  steps1.forEach(([t, ic], i) => {
    const x = 0.6 + i * 2.2;
    circ(s, ic, x, 5.65, 0.6, C.navy2);
    s.addText(t, { x: x + 0.72, y: 5.65, w: 1.4, h: 0.6, fontFace: BF, fontSize: 15, bold: true,
      color: C.white, valign: "middle", margin: 0, isTextBox: true });
  });
  s.addText("Team Azure Armada  ·  Virtusa", { x: 8.7, y: 6.75, w: 4.0, h: 0.4, fontFace: BF, fontSize: 13,
    color: "9C95BD", align: "right", margin: 0, isTextBox: true });
  s.addNotes("The Disaster Recovery (DR) Agent is a team of AI agents built entirely on Azure that actually performs disaster recovery testing: it fails workloads over, measures real recovery time and data loss, fails back, and produces audit-ready proof.");

  // ---------- 2. Problem: what downtime costs ----------
  s = add(); s.background = { color: C.white };
  title(s, "Downtime now costs about $900,000 an hour",
    "The latest independent research (2026) and a 2025 cloud outage: the cost of not recovering on time.");
  const stats = [
    ["$15,000", "per minute of downtime", "is the average for Global 2000 companies: about $900,000 an hour and $600B a year in total. Regulatory fines alone average $51M per company a year.", "Splunk & Oxford Economics, The Hidden Costs of Downtime, May 2026 (2,000 executives) [1]"],
    ["57%", "had a last major outage over $100K", "and 1 in 5 operators said their last major outage cost more than $1 million, for the second year running.", "Uptime Institute, Annual Outage Analysis 2026 [2]"],
    ["$500–650M", "from one cloud-region outage", "lost by US companies when AWS us-east-1 failed on 20 October 2025. Even hyperscalers fail; what matters is whether your recovery works.", "Parametrix estimate, Oct 2025 [3]"],
  ];
  stats.forEach(([big, unit, body, src], i) => {
    const x = 0.6 + i * 4.1, y = 1.75;
    card(s, x, y, 3.85, 4.15);
    s.addText(big, { x: x + 0.3, y: y + 0.3, w: 3.3, h: 1.0, fontFace: HF, fontSize: 42, bold: true, color: C.teal, margin: 0, isTextBox: true });
    s.addText(unit, { x: x + 0.3, y: y + 1.3, w: 3.3, h: 0.45, fontFace: BF, fontSize: 16, bold: true, color: C.navy, margin: 0, isTextBox: true });
    s.addText(body, { x: x + 0.3, y: y + 1.85, w: 3.3, h: 1.45, fontFace: BF, fontSize: 13, color: C.ink, valign: "top", margin: 0, isTextBox: true });
    s.addText(src, { x: x + 0.3, y: y + 3.35, w: 3.3, h: 0.6, fontFace: BF, fontSize: 9.5, color: C.muted, italic: true, valign: "bottom", margin: 0, isTextBox: true });
  });
  card(s, 0.6, 6.12, 12.1, 0.72, C.navy, false);
  s.addText([
    { text: "At $15,000 a minute, one hour of downtime costs about $900,000.", options: { bold: true, color: C.white } },
    { text: "   Recovery that is assumed, not proven, is an unpriced risk.", options: { color: "C9C3E6" } },
  ], { x: 0.85, y: 6.12, w: 11.7, h: 0.72, fontFace: BF, fontSize: 16, valign: "middle", margin: 0, isTextBox: true });
  footer(s);
  s.addNotes("Start with money, using the latest research. Splunk with Oxford Economics, May 2026, 2,000 executives: downtime now costs Global 2000 companies $15,000 a minute on average, about $900,000 an hour and $600 billion a year, including $51 million a year in regulatory fines per company. Uptime Institute's 2026 analysis: 57% say their last major outage cost over $100,000, and one in five over $1 million. And in October 2025 a single AWS region outage cost US companies an estimated $500 to $650 million. Even hyperscalers fail; what matters is whether your recovery works.");

  // ---------- 3. Problem: India, consequences ----------
  s = add(); s.background = { color: C.white };
  title(s, "In India, failing to recover is punished by freezing growth",
    "Regulators act on outages and weak disaster recovery, not just on breaches.");
  const cases = [
    ["HDFC Bank", "Dec 2020", "After repeated outages, the latest caused by a power failure at a primary data centre, the RBI stopped all new digital launches and new credit-card sourcing.", "≈15 months", "until digital launches were allowed again (Mar 2022); cards resumed Aug 2021", "[4]"],
    ["NSE", "Feb 2021", "Trading was halted for nearly four hours on 24 February 2021. NSE and NSE Clearing settled with SEBI; executives were ordered to do community service and training.", "≈ $8.8M", "paid by NSE and NSE Clearing to settle with SEBI (2023)", "[5]"],
    ["Kotak Mahindra Bank", "Apr 2024", "The RBI cited deficiencies including business continuity and disaster recovery, and frequent outages, and barred online customer onboarding and new credit cards.", "≈10 months", "until the restrictions were lifted (Feb 2025)", "[6]"],
  ];
  cases.forEach(([who, when, body, big, unit, ref], i) => {
    const x = 0.6 + i * 4.1, y = 1.75;
    card(s, x, y, 3.85, 4.85);
    s.addText(who, { x: x + 0.3, y: y + 0.28, w: 2.4, h: 0.45, fontFace: HF, fontSize: 19, bold: true, color: C.navy, margin: 0, isTextBox: true });
    s.addText(when, { x: x + 2.55, y: y + 0.3, w: 1.0, h: 0.4, fontFace: BF, fontSize: 12, bold: true, color: C.teal, align: "right", margin: 0, isTextBox: true });
    s.addText(body, { x: x + 0.3, y: y + 0.85, w: 3.3, h: 1.95, fontFace: BF, fontSize: 13, color: C.ink, valign: "top", margin: 0, isTextBox: true });
    s.addShape(pres.shapes.LINE, { x: x + 0.3, y: y + 2.95, w: 3.25, h: 0, line: { color: C.line, width: 1 } });
    s.addText(big, { x: x + 0.3, y: y + 3.1, w: 3.3, h: 0.8, fontFace: HF, fontSize: 34, bold: true, color: C.red, margin: 0, isTextBox: true });
    s.addText(unit, { x: x + 0.3, y: y + 3.9, w: 3.3, h: 0.6, fontFace: BF, fontSize: 12, color: C.muted, valign: "top", margin: 0, isTextBox: true });
    s.addText(ref, { x: x + 3.1, y: y + 4.45, w: 0.5, h: 0.3, fontFace: BF, fontSize: 9.5, color: C.muted, align: "right", margin: 0, isTextBox: true });
  });
  s.addText("For a bank, a 10–15 month ban on new digital customers costs far more than any fine.", { x: 0.6, y: 6.72, w: 12.1, h: 0.35, fontFace: BF, fontSize: 14, bold: true, color: C.navy, margin: 0, isTextBox: true });
  footer(s);
  s.addNotes("Closer to home, the penalty is a growth freeze. HDFC Bank: after repeated outages, the most recent a power failure at a primary data centre, the RBI stopped new digital launches and new credit cards in December 2020; cards resumed in August 2021, digital launches only in March 2022. NSE: a nearly four-hour trading halt in February 2021 ended in a settlement of about $8.8 million with SEBI. Kotak Mahindra Bank: in April 2024 the RBI cited deficiencies including business continuity and disaster recovery, plus frequent outages, and stopped online onboarding and new cards for about ten months.");

  // ---------- 4. Problem: regulators require proof ----------
  s = add(); s.background = { color: C.white };
  title(s, "Regulators now require proof of recovery, on a schedule",
    "Having a DR plan is not enough. It must be tested, the gaps fixed, and the test repeated.");
  const hdr = (t) => ({ text: t, options: { bold: true, color: C.white, fill: { color: C.navy }, fontSize: 13 } });
  const cell = (t, o = {}) => ({ text: t, options: { fontSize: 12, color: C.ink, valign: "top", ...o } });
  s.addTable([
    [hdr("Regulator"), hdr("What it requires"), hdr("If you fall short")],
    [cell("RBI\nCybersecurity, Technology: Risk, Resilience and Assurance Framework Directions, 31 Jul 2026", { bold: true }),
     cell("DR drills for critical information systems at least half-yearly. Any major issue found must be fixed and re-tested successfully before the next cycle. [7]"),
     cell("Supervisory restrictions on business, as with HDFC Bank and Kotak Mahindra Bank. [4][6]")],
    [cell("SEBI\nCybersecurity & Cyber Resilience Framework", { bold: true }),
     cell("Resume critical operations within 2 hours (RTO) with a 15-minute RPO. Market infrastructure: run from the DR site within 45 minutes of declaring a disaster, drills every quarter, unannounced live trading from the DR site every six months. [8][9]"),
     cell("Enforcement and settlements, as with NSE: about $8.8M. [5]")],
    [cell("EU DORA\nRegulation (EU) 2022/2554, applies from 17 Jan 2025", { bold: true }),
     cell("Test ICT business continuity and response and recovery plans at least yearly and after major changes (Art. 11(6)); test backup restoration periodically (Art. 12). [10]"),
     cell("Penalties set by each EU state (Art. 50). Critical ICT providers: up to 1% of average daily worldwide turnover, every day, for up to six months (Art. 35). [10]")],
  ], { x: 0.6, y: 1.75, w: 12.1, colW: [3.0, 5.5, 3.6], border: { type: "solid", color: C.line, pt: 1 },
       fontFace: BF, margin: [6, 8, 6, 8], rowH: [0.42, 1.2, 1.35, 1.25] });
  card(s, 0.6, 6.05, 12.1, 0.8, C.tealLight, false);
  s.addText([
    { text: "Our demo uses the same yardstick:  ", options: { bold: true, color: C.navy } },
    { text: "target RTO 30 min and RPO 15 min, stricter than SEBI's 2 hours, and every drill produces timestamped, audit-ready evidence.", options: { color: C.ink } },
  ], { x: 0.85, y: 6.05, w: 11.7, h: 0.8, fontFace: BF, fontSize: 14, valign: "middle", margin: 0, isTextBox: true });
  footer(s);
  s.addNotes("Regulators have moved from 'have a plan' to 'prove it works, on a schedule'. RBI's July 2026 directions: DR drills for critical systems at least half-yearly, and every major issue fixed and re-tested before the next cycle. SEBI: resume critical operations within two hours with a 15-minute RPO; exchanges must be running from DR within 45 minutes and drill every quarter. DORA in Europe: test continuity and recovery plans at least yearly. A common myth is that DORA fixes a 2%-of-turnover fine for banks; it does not. Penalties for financial entities are set by each member state; the fixed figure applies to critical ICT providers: up to 1% of average daily worldwide turnover, daily, for up to six months.");

  // ---------- 5. Problem: the proof gap ----------
  s = add(); s.background = { color: C.white };
  title(s, "Yet most organisations cannot prove their recovery works today",
    "Plans exist on paper. Tests are rare, manual and partial, so the gaps surface during the real disaster.");
  const gap = [
    ["< 1 in 3", "organisations perform regular failover testing", "Cockroach Labs, State of Resilience 2025 (1,000 executives) [11]"],
    ["20%", "of executives believe their organisation is fully prepared to prevent or respond to outages", "Cockroach Labs, State of Resilience 2025 [11]"],
    ["#1", "cause of human-error outages: staff not following established procedures", "Uptime Institute, Annual Outage Analysis 2026 [2]"],
  ];
  gap.forEach(([big, body, src], i) => {
    const y = 1.75 + i * 1.6;
    card(s, 0.6, y, 6.2, 1.4);
    s.addText(big, { x: 0.85, y: y + 0.2, w: 2.0, h: 1.0, fontFace: HF, fontSize: 38, bold: true, color: C.teal, valign: "middle", margin: 0, isTextBox: true });
    s.addText(body, { x: 2.9, y: y + 0.18, w: 3.7, h: 0.75, fontFace: BF, fontSize: 13.5, color: C.ink, valign: "top", margin: 0, isTextBox: true });
    s.addText(src, { x: 2.9, y: y + 0.95, w: 3.7, h: 0.35, fontFace: BF, fontSize: 9.5, color: C.muted, italic: true, margin: 0, isTextBox: true });
  });
  s.addText("Why the gap persists", { x: 7.2, y: 1.75, w: 5.5, h: 0.45, fontFace: HF, fontSize: 18, bold: true, color: C.navy, margin: 0, isTextBox: true });
  const why = [
    [I.cal, "Tests are disruptive and manual", "A real failover needs several teams, a change window and hand-written timings, so it happens rarely."],
    [I.drift, "Environments drift between tests", "A setting added in one region but not the other only shows up when you fail over."],
    [I.book, "Runbooks go stale", "Documents name servers and services that no longer exist; engineers find out mid-incident."],
    [I.clock, "RTO and RPO are assumed", "Targets are signed off on paper; actual recovery time and data loss are rarely measured end to end."],
  ];
  why.forEach(([ic, h, b], i) => {
    const y = 2.3 + i * 1.12;
    circ(s, ic, 7.2, y, 0.55, C.navy);
    s.addText(h, { x: 7.95, y: y - 0.04, w: 4.8, h: 0.35, fontFace: BF, fontSize: 14, bold: true, color: C.navy, margin: 0, isTextBox: true });
    s.addText(b, { x: 7.95, y: y + 0.32, w: 4.8, h: 0.65, fontFace: BF, fontSize: 12, color: C.ink, valign: "top", margin: 0, isTextBox: true });
  });
  footer(s);
  s.addNotes("So why don't companies just test? Cockroach Labs surveyed 1,000 executives: fewer than one in three do regular failover testing, and only 20% feel fully prepared. Uptime Institute's 2026 analysis names failure to follow established procedures as the leading driver of human-error outages, which is exactly what a stale runbook causes. The reasons are structural: tests are disruptive and manual, environments drift between tests, runbooks go stale, and RTO/RPO are signed off on paper instead of measured.");

  // ---------- 6. Problem: worked example ----------
  s = add(); s.background = { color: C.white };
  title(s, "What an unproven DR plan costs: one bad morning",
    "Illustration for a tier-1 payments service, using the cited figures. Not a forecast; the inputs are shown.");
  const rows = [
    ["Recovery target (RTO)", "30 minutes", "Signed off on paper; never measured end to end"],
    ["Actual recovery", "4 hours", "Comparable to the NSE trading halt of Feb 2021 [5]"],
    ["Hourly cost of downtime", "$900,000", "$15,000 a minute, the Global 2000 average (Splunk & Oxford Economics, 2026) [1]"],
    ["Direct loss", "$3.6M", "4 h × $900K; $3.15M of it beyond the 30-minute target"],
    ["Regulatory precedent", "≈ $8.8M", "What NSE paid to settle its 2021 outage case [5]; banks have faced 10–15 month business restrictions [4][6]"],
  ];
  rows.forEach(([k, v, n], i) => {
    const y = 1.75 + i * 0.86;
    const last = i >= 3;
    card(s, 0.6, y, 12.1, 0.74, last ? C.amberLight : C.card, false);
    s.addText(k, { x: 0.85, y, w: 3.4, h: 0.74, fontFace: BF, fontSize: 14, bold: true, color: C.navy, valign: "middle", margin: 0, isTextBox: true });
    s.addText(v, { x: 4.3, y, w: 2.6, h: 0.74, fontFace: HF, fontSize: 22, bold: true, color: last ? C.red : C.ink, valign: "middle", margin: 0, isTextBox: true });
    s.addText(n, { x: 7.0, y, w: 5.5, h: 0.74, fontFace: BF, fontSize: 12, color: C.muted, valign: "middle", margin: 0, isTextBox: true });
  });
  card(s, 0.6, 6.15, 12.1, 0.72, C.navy, false);
  s.addText([
    { text: "The DR Agent measured 34.35 s", options: { bold: true, color: C.white } },
    { text: " to recover the same kind of service in our live Azure drill (18 Oct 2026), with zero payments lost, and proves it again on every run.", options: { color: "C9C3E6" } },
  ], { x: 0.85, y: 6.15, w: 11.7, h: 0.72, fontFace: BF, fontSize: 14.5, valign: "middle", margin: 0, isTextBox: true });
  footer(s);
  s.addNotes("Put it together with transparent inputs. A tier-1 payments service with a 30-minute target that has never really been tested. On the day, recovery takes four hours, like the NSE halt. At the 2026 average of $15,000 a minute, that is $3.6 million of direct loss, $3.15 million of it beyond the target, before any regulatory action: NSE paid about $8.8 million to settle, and the RBI froze HDFC's and Kotak's digital growth for 10 to 15 months. Our agent measured 34.35 seconds for the same kind of service, with zero payments lost, and it can prove it again on demand.");

  // ---------- 7. Solution ----------
  s = add(); s.background = { color: C.white };
  title(s, "The DR Agent runs the real drill and proves the result",
    "Six AI agents on Azure AI Foundry plan and judge; a guarded engine executes on live Azure resources; humans approve.");
  const phases = [
    ["1", "Design & prepare", [["Architect", "picks the cheapest strategy that truly meets RTO/RPO, with real Azure prices"], ["Readiness", "7 live checks; finds drift before the disaster; one-click approved fix"], ["Runbook", "flags stale steps and writes the corrected runbook"]]],
    ["2", "Plan, execute, fail back, report", [["Planner", "builds a 21-step drill from an allowlist of safe actions"], ["Engine + Validator", "injects the outage, fails over, measures; diagnoses any failure live"], ["Reporter", "audit-ready report with every second of RTO accounted for"]]],
  ];
  phases.forEach(([n, h, items], i) => {
    const x = 0.6 + i * 6.15, y = 1.75;
    card(s, x, y, 5.95, 3.6);
    s.addText(`Phase ${n}`, { x: x + 0.3, y: y + 0.25, w: 2, h: 0.3, fontFace: BF, fontSize: 11, bold: true, color: C.teal, charSpacing: 1, margin: 0, isTextBox: true });
    s.addText(h, { x: x + 0.3, y: y + 0.55, w: 5.4, h: 0.45, fontFace: HF, fontSize: 19, bold: true, color: C.navy, margin: 0, isTextBox: true });
    items.forEach(([a, b], j) => {
      const yy = y + 1.2 + j * 0.78;
      s.addText(a, { x: x + 0.3, y: yy, w: 1.75, h: 0.6, fontFace: BF, fontSize: 13, bold: true, color: C.navy, valign: "top", margin: 0, isTextBox: true });
      s.addText(b, { x: x + 2.05, y: yy, w: 3.65, h: 0.65, fontFace: BF, fontSize: 12.5, color: C.ink, valign: "top", margin: 0, isTextBox: true });
    });
  });
  const cmp = [["How often DR is proven", "Once or twice a year", "On demand, any day"], ["RTO and RPO", "Assumed on paper", "Measured on every drill, to the millisecond"],
    ["Configuration drift", "Found during the real outage", "Found by readiness checks, fixed with approval"], ["Audit evidence", "Screenshots and spreadsheets", "Generated report with timestamps and approvals"]];
  s.addTable([[{ text: "", options: { fill: { color: C.white } } }, { text: "Today", options: { bold: true, color: C.red } }, { text: "With the DR Agent", options: { bold: true, color: C.teal } }],
    ...cmp.map(([a, b, c]) => [{ text: a, options: { bold: true, color: C.navy } }, { text: b, options: { color: C.ink } }, { text: c, options: { color: C.ink } }])],
    { x: 0.6, y: 5.5, w: 12.1, colW: [3.3, 3.9, 4.9], fontFace: BF, fontSize: 12, rowH: 0.27, margin: [2, 6, 2, 6],
      border: { type: "solid", color: C.line, pt: 0.75 } });
  footer(s);
  s.addNotes("Our answer follows the DR lifecycle. Phase 1, design and prepare: the Architect picks the cheapest strategy that genuinely meets the targets using live Azure prices; Readiness runs seven live checks and catches drift; the Runbook agent finds stale steps. Phase 2: the Planner builds the drill from an allowlist, the engine executes it on real Azure resources and measures, the Validator diagnoses failures live, and the Reporter writes an audit-ready report. Humans approve every risky step.");

  // ---------- 8. Agents ----------
  s = add(); s.background = { color: C.white };
  title(s, "Six specialist agents, each with one job and a contract",
    "Versioned prompt agents in Azure AI Foundry Agent Service. Every answer must match a strict JSON schema and pass code checks.");
  const agents = [
    ["Architect", "gpt-4.1-mini", "Profile, inventory, live Azure prices", "Strategy + all 5 options", "Cheapest option that truly meets targets"],
    ["Readiness", "gpt-4.1-mini", "7 deterministic checks with evidence", "GO / NO-GO, risks", "Any failed check forces NO-GO"],
    ["Runbook", "gpt-4.1-mini", "Runbook markdown, inventory", "Stale / valid per step, corrected runbook", "Schema only (advisory)"],
    ["Planner", "gpt-5-mini", "Strategy, risks, allowlist, safe sequence", "21-step drill plan", "Allowlisted actions, mandatory sequence, approvals"],
    ["Validator", "gpt-5-mini", "Failed step, error, events, live probes", "Cause, evidence, fix", "Fix allowlisted; never Region A mid-outage; human approves"],
    ["Reporter", "gpt-4.1-mini", "Measured RTO/RPO, timeline, findings", "Summary, remediation items", "Numbers rendered by code, not retyped"],
  ];
  const th = (t) => ({ text: t, options: { bold: true, color: C.white, fill: { color: C.navy } } });
  s.addTable([[th("Agent"), th("Model"), th("Gets (facts from code)"), th("Returns"), th("Checked by code")],
    ...agents.map((r) => r.map((t, j) => ({ text: t, options: { bold: j === 0, color: j === 0 ? C.navy : C.ink, fontFace: j === 1 ? "Consolas" : BF } })))],
    { x: 0.6, y: 1.7, w: 12.1, colW: [1.5, 1.55, 3.15, 2.75, 3.15], fontFace: BF, fontSize: 12, rowH: 0.5, margin: [4, 6, 4, 6],
      border: { type: "solid", color: C.line, pt: 0.75 } });
  card(s, 0.6, 5.55, 12.1, 1.3, C.tealLight, false);
  s.addText([
    { text: "Watch them think. ", options: { bold: true, color: C.navy } },
    { text: "A live activity panel streams every Azure read, the Foundry call (agent version, latency, tokens) and the code check, as it happens. It caught a real defect: the Architect compared a cost multiplier with a dollar budget; we now give it Azure list prices and it is right first time (5 of 5 runs).", options: { color: C.ink } },
  ], { x: 0.85, y: 5.55, w: 11.6, h: 1.3, fontFace: BF, fontSize: 13.5, valign: "middle", margin: 0, isTextBox: true });
  footer(s);
  s.addNotes("Each agent is a versioned prompt agent in Azure AI Foundry with a strict JSON schema. Code gathers the facts, the agent decides, code checks the answer, and every agent has a deterministic fallback so the demo never depends on one model call. The live activity panel shows exactly what each agent did. It also exposed a real defect: we were giving the Architect a cost multiplier and a dollar budget; it guessed wrong. We fixed the input, not the model, and it is now right first time.");

  // ---------- 9. Safety ----------
  s = add(); s.background = { color: C.white };
  title(s, "Agents decide. The engine acts. Humans approve.",
    "DR touches production, so no model can call an Azure write API. Every action is allowlisted, ordered, approved and reversible.");
  const guards = [
    [I.lock, "Allowlisted actions", "The engine runs only 15 known actions (stop/start app, SQL failover, endpoint on/off, ASR test). Anything else is rejected."],
    [I.route, "Mandatory safe sequence", "Every plan must contain heartbeat → outage → SQL failover → reroute → verify → measure → fail back, in that order."],
    [I.user, "Human approval gates", "The plan, the forced database failover, the failback and any Validator fix wait for a named approver."],
    [I.power, "Kill switch and rollback", "One click stops the drill; rollback restores the primary region and the database role."],
    [I.history, "Fallback for every agent", "Foundry agent → model deployment → deterministic rules. The screen shows which one answered."],
    [I.audit, "Full audit trail", "Every event, approval and measurement is timestamped and stored, then written into the report."],
  ];
  guards.forEach(([ic, h, b], i) => {
    const col = i % 3, row = Math.floor(i / 3);
    const x = 0.6 + col * 4.1, y = 1.75 + row * 2.0;
    card(s, x, y, 3.85, 1.8);
    circ(s, ic, x + 0.25, y + 0.25, 0.55, C.navy);
    s.addText(h, { x: x + 0.95, y: y + 0.25, w: 2.75, h: 0.55, fontFace: BF, fontSize: 15, bold: true, color: C.navy, valign: "middle", margin: 0, isTextBox: true });
    s.addText(b, { x: x + 0.25, y: y + 0.9, w: 3.4, h: 0.85, fontFace: BF, fontSize: 12, color: C.ink, valign: "top", margin: 0, isTextBox: true });
  });
  card(s, 0.6, 5.85, 12.1, 1.0, C.navy, false);
  s.addText([
    { text: "Proven in a chaos drill: ", options: { bold: true, color: C.white } },
    { text: "we disabled the DR traffic endpoint mid-failover. The Validator diagnosed it from live probes, proposed the allowlisted fix, a human approved, and recovery completed in 57.4 s.", options: { color: "C9C3E6" } },
  ], { x: 0.85, y: 5.85, w: 11.6, h: 1.0, fontFace: BF, fontSize: 14, valign: "middle", margin: 0, isTextBox: true });
  footer(s);
  s.addNotes("This is why it is safe for a bank. Models never touch Azure directly. The engine runs only allowlisted actions, in a mandatory order, with named human approvals at the risky points, a kill switch, rollback, and a fallback for every agent. We proved it with a surprise fault mid-drill: the Validator diagnosed it, proposed an allowlisted fix, a human approved, and the service recovered in 57.4 seconds.");

  // ---------- 10. Architecture ----------
  s = add(); s.background = { color: C.white };
  title(s, "Architecture: 100% Azure, across an Azure paired region",
    "East Asia (primary) and Southeast Asia (DR). The control plane runs outside both, in South India, so it survives the disaster it manages.");
  s.addImage({ path: "docs/deck/architecture-deck.png", x: 0.6, y: 1.65, w: 12.1, h: 12.1 * 1264 / 3072 });
  const chips = ["App Service ×2", "Azure SQL failover group", "Traffic Manager", "Site Recovery", "AI Foundry Agent Service", "Key Vault + managed identity", "Storage", "Monitor + App Insights", "Bicep: one-click up / down"];
  chips.forEach((t, i) => {
    const w = 1.28, x = 0.6 + i * (w + 0.075);
    s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x, y: 6.72, w, h: 0.32, rectRadius: 0.06, fill: { color: C.tealLight }, line: { color: C.tealLight } });
    s.addText(t, { x, y: 6.72, w, h: 0.32, fontFace: BF, fontSize: 8.5, bold: true, color: C.navy, align: "center", valign: "middle", margin: 0, isTextBox: true });
  });
  s.addNotes("Everything runs on Azure. Users reach Contoso Payments through Traffic Manager with priority routing. App Service runs in both regions; Azure SQL replicates through a failover group; the reports VM is protected by Azure Site Recovery. The DR console and engine sit in South India, outside both regions, and call the six agents in Azure AI Foundry. Secrets live in Key Vault with managed identities; the whole environment is Bicep with one-click up and down.");

  // ---------- 11. Results ----------
  s = add(); s.background = { color: C.white };
  title(s, "Measured on live Azure, not assumed",
    "Real failovers of the payments service, East Asia → Southeast Asia, 17–18 Oct 2026. Target: RTO 30 min, RPO 15 min.");
  const kpi = [["34.35 s", "RTO, latest drill", "29.6–40.4 s across 8 drills (median 33.5 s)"], ["0 s", "RPO, every drill", "no confirmed payment lost (1 s heartbeat proof)"],
    ["3 min 14 s", "Failback to primary", "planned, no data loss"], ["5 min 45 s", "VM recovered by Site Recovery", "proven by a command inside the VM"]];
  kpi.forEach(([v, l, x0], i) => {
    const x = 0.6 + i * 3.06;
    card(s, x, 1.75, 2.88, 1.85);
    s.addText(v, { x: x + 0.2, y: 1.9, w: 2.5, h: 0.75, fontFace: HF, fontSize: 30, bold: true, color: i < 2 ? C.teal : C.navy, margin: 0, isTextBox: true });
    s.addText(l, { x: x + 0.2, y: 2.65, w: 2.5, h: 0.35, fontFace: BF, fontSize: 13, bold: true, color: C.navy, margin: 0, isTextBox: true });
    s.addText(x0, { x: x + 0.2, y: 3.0, w: 2.55, h: 0.5, fontFace: BF, fontSize: 11, color: C.muted, valign: "top", margin: 0, isTextBox: true });
  });
  s.addText("Where the 34.35 s went (latest drill, one clock, segments add up exactly)", { x: 0.6, y: 3.9, w: 12.1, h: 0.35, fontFace: BF, fontSize: 14, bold: true, color: C.navy, margin: 0, isTextBox: true });
  const segs = [["Outage injected", 0.519, "9F86FF"], ["SQL forced failover", 15.355, C.teal], ["Traffic Manager reroute", 5.916, "C105B2"], ["DNS + health checks reach DR", 11.619, "7C5CFF"], ["Engine bookkeeping", 0.944, "BDBDC7"]];
  const total = 34.353; let bx = 0.6;
  segs.forEach(([l, v, col]) => {
    const w = 12.1 * v / total;
    s.addShape(pres.shapes.RECTANGLE, { x: bx, y: 4.35, w, h: 0.5, fill: { color: col }, line: { color: C.white, width: 1 } });
    if (w > 1.0) s.addText(`${v.toFixed(3)} s`, { x: bx, y: 4.35, w, h: 0.5, fontFace: BF, fontSize: 12, bold: true, color: C.white, align: "center", valign: "middle", margin: 0, isTextBox: true });
    bx += w;
  });
  segs.forEach(([l, v, col], i) => {
    const x = 0.6 + i * 2.42;
    s.addShape(pres.shapes.RECTANGLE, { x, y: 5.05, w: 0.18, h: 0.18, fill: { color: col }, line: { color: col } });
    s.addText(`${l}  ${v.toFixed(3)} s`, { x: x + 0.25, y: 4.97, w: 2.15, h: 0.4, fontFace: BF, fontSize: 10.5, color: C.ink, valign: "middle", margin: 0, isTextBox: true });
  });
  card(s, 0.6, 5.6, 12.1, 1.25, C.tealLight, false);
  s.addText([
    { text: "Why judges can trust the numbers: ", options: { bold: true, color: C.navy } },
    { text: "RTO runs from the injected outage to the first healthy check through Traffic Manager (1 s probes; the previous failed check is shown as the precision window). RPO compares every confirmed heartbeat write with the rows that survived in the new primary. Chaos drill with a surprise fault: 57.4 s.", options: { color: C.ink } },
  ], { x: 0.85, y: 5.6, w: 11.6, h: 1.25, fontFace: BF, fontSize: 12.5, valign: "middle", margin: 0, isTextBox: true });
  footer(s);
  s.addNotes("These are measured, not estimated. Eight drills without chaos: RTO between 29.6 and 40.4 seconds, median 33.5, against a 30-minute target; RPO zero every time. Failback about three minutes with no data loss. The reports VM was recovered by Site Recovery in under six minutes and proven by running a command inside it. And every second of RTO is accounted for: the latest 34.35 seconds split into 15.4 s SQL failover, 5.9 s Traffic Manager, 11.6 s DNS and health checks, and under a second each for the injection and our own bookkeeping.");

  // ---------- 12. Cost ----------
  s = add(); s.background = { color: C.white };
  title(s, "What it costs to prove recovery",
    "Actual Azure bills and list prices for what we built, compared with the cost of downtime.");
  const cost = [["$0.09", "all AI agent calls", "every Foundry agent run across 2 days of building and 9 drills: $0.087 (Azure Cost Management) [12]"],
    ["$2.44", "entire environment so far", "two regions, database, VM, console, monitoring: everything, 17–18 Oct 2026 (Azure Cost Management) [12]"],
    ["$38.26", "per month for the DR set-up", "both regions + Traffic Manager at Azure list prices (Retail Prices API) [13]"]];
  cost.forEach(([v, l, n], i) => {
    const x = 0.6 + i * 4.1;
    card(s, x, 1.75, 3.85, 2.55);
    s.addText(v, { x: x + 0.3, y: 1.95, w: 3.3, h: 0.85, fontFace: HF, fontSize: 40, bold: true, color: C.teal, margin: 0, isTextBox: true });
    s.addText(l, { x: x + 0.3, y: 2.8, w: 3.3, h: 0.4, fontFace: BF, fontSize: 15, bold: true, color: C.navy, margin: 0, isTextBox: true });
    s.addText(n, { x: x + 0.3, y: 3.25, w: 3.3, h: 0.95, fontFace: BF, fontSize: 11.5, color: C.muted, valign: "top", margin: 0, isTextBox: true });
  });
  card(s, 0.6, 4.55, 12.1, 1.15, C.navy, false);
  s.addText([
    { text: "A month of this DR set-up costs less than a fifth of a second of downtime ", options: { bold: true, color: C.white } },
    { text: "at the 2026 average of $15,000 a minute ($38.26 ÷ $250 per second = 0.15 s). [1][13]", options: { color: "C9C3E6" } },
  ], { x: 0.85, y: 4.55, w: 11.6, h: 1.15, fontFace: BF, fontSize: 16, valign: "middle", margin: 0, isTextBox: true });
  s.addText([
    { text: "Cost-aware by design: ", options: { bold: true, color: C.navy } },
    { text: "the Architect prices every strategy from live Azure list prices and flags anything over budget; the whole environment is created and removed with one command (up.sh / down.sh), so nothing runs when it is not needed.", options: { color: C.ink } },
  ], { x: 0.6, y: 5.95, w: 12.1, h: 0.9, fontFace: BF, fontSize: 13, valign: "top", margin: 0, isTextBox: true });
  footer(s);
  s.addNotes("Cost is where this gets compelling. Every AI agent call we made across two days and nine drills cost 9 cents in total, from Azure Cost Management. The entire environment so far cost $2.44. At Azure list prices the DR set-up itself is $38.26 a month. At the 2026 average of $15,000 a minute, a whole month of DR costs less than a fifth of a second of downtime.");

  // ---------- 13. Demo + roadmap ----------
  s = add(); s.background = { color: C.white };
  title(s, "Live demo, and where it goes next", "What you will see in the next minutes, on your own phone.");
  const demo = ["Scan the QR code: Contoso Payments opens on your phone; make a payment",
    "Readiness says NO-GO: Southeast Asia is missing a setting; one approved click fixes it",
    "The Planner builds the drill; we approve it and inject the outage",
    "Your phone goes red, then reconnects to Southeast Asia in ~30 s with the same balance",
    "Failback, then the report: RTO, RPO and every second accounted for"];
  s.addText("The demo", { x: 0.6, y: 1.7, w: 6, h: 0.4, fontFace: HF, fontSize: 18, bold: true, color: C.navy, margin: 0, isTextBox: true });
  demo.forEach((t, i) => {
    const y = 2.2 + i * 0.86;
    s.addShape(pres.shapes.OVAL, { x: 0.6, y: y + 0.05, w: 0.45, h: 0.45, fill: { color: C.teal }, line: { color: C.teal } });
    s.addText(String(i + 1), { x: 0.6, y: y + 0.05, w: 0.45, h: 0.45, fontFace: BF, fontSize: 14, bold: true, color: C.white, align: "center", valign: "middle", margin: 0, isTextBox: true });
    s.addText(t, { x: 1.2, y, w: 5.6, h: 0.6, fontFace: BF, fontSize: 13.5, color: C.ink, valign: "middle", margin: 0, isTextBox: true });
  });
  card(s, 7.2, 1.7, 5.5, 5.15, C.card, false);
  s.addText("Roadmap (not in the demo)", { x: 7.5, y: 1.9, w: 5, h: 0.4, fontFace: HF, fontSize: 18, bold: true, color: C.navy, margin: 0, isTextBox: true });
  const road = [["Scheduled drills", "weekly, and after every infrastructure change"], ["More services", "AKS, Cosmos DB, Storage, Service Bus as new allowlisted actions"],
    ["Azure Chaos Studio", "standard fault library alongside our own faults"], ["Approvals where teams work", "Microsoft Teams approval cards"], ["Portfolio view", "resilience score across many applications"]];
  road.forEach(([h, b], i) => {
    const y = 2.45 + i * 0.85;
    s.addText(h, { x: 7.5, y, w: 4.95, h: 0.32, fontFace: BF, fontSize: 13.5, bold: true, color: C.navy, margin: 0, isTextBox: true });
    s.addText(b, { x: 7.5, y: y + 0.32, w: 4.95, h: 0.4, fontFace: BF, fontSize: 12, color: C.ink, margin: 0, isTextBox: true });
  });
  footer(s);
  s.addNotes("Now the demo, on your own phone. Then the roadmap, which we keep clearly separate from what is built: scheduled drills, more Azure services as new allowlisted actions, Chaos Studio's fault library, Teams approval cards, and a resilience score across many applications.");

  // ---------- Sources ----------
  s = add(); s.background = { color: C.white };
  title(s, "Sources", "Every figure in this deck is taken from the source below. Our own figures come from live drills on Azure.");
  const refs = [
    "[1] Splunk with Oxford Economics, The Hidden Costs of Downtime 2026, 19 May 2026 (2,000 Global 2000 executives). newsroom.cisco.com (The $600 Billion Wake-up Call)",
    "[2] Uptime Institute, Annual Outage Analysis 2026, press release 13 May 2026. uptimeinstitute.com",
    "[3] Parametrix estimate of losses from the AWS us-east-1 outage of 20 Oct 2025 (Insurance Edge, 30 Oct 2025).",
    "[4] RBI order to HDFC Bank, 2 Dec 2020 (Outlook Business, 3 Dec 2020); curbs lifted 17 Aug 2021 (cards) and 11 Mar 2022 (digital) (Business Standard, 12 Mar 2022).",
    "[5] NSE and NSE Clearing settle the 24 Feb 2021 trading-halt case with SEBI for ₹72.64 crore (Moneylife; Business Standard, Jun 2023); ≈ $8.8M at June 2023 rates of ₹82–83 per US$.",
    "[6] RBI action on Kotak Mahindra Bank under Sec. 35A, 24 Apr 2024 (TechCrunch); restrictions lifted 12 Feb 2025 (Business Standard).",
    "[7] RBI, Cybersecurity, Technology: Risk, Resilience and Assurance Framework Directions, 31 Jul 2026, summarised by KPMG India, Sep 2026.",
    "[8] SEBI Cybersecurity and Cyber Resilience Framework: RTO 2 hours, RPO 15 minutes (clarification reported by PTI / The Week, 28 Aug 2025).",
    "[9] SEBI framework for market infrastructure institutions: DR site within 45 minutes, quarterly drills (Business Standard, 22 Mar 2021).",
    "[10] Regulation (EU) 2022/2554 (DORA), Articles 11(6), 12, 35(7)–(8), 50, 51. eur-lex.europa.eu/eli/reg/2022/2554/oj",
    "[11] Cockroach Labs with Wakefield Research, The State of Resilience 2025 (1,000 executives, Aug–Sep 2024). cockroachlabs.com",
    "[12] Azure Cost Management, actual cost by service in US$ (CostUSD), month to date, queried 18 Oct 2026 (our environment).",
    "[13] Azure Retail Prices API (prices.azure.com), East Asia / Southeast Asia list prices for App Service B1 Linux, SQL Database Basic, Traffic Manager, 18 Oct 2026.",
    "Our measurements: 9 drills recorded by the DR Agent (17–18 Oct 2026); each report keeps the raw timestamps, events and approvals.",
  ];
  s.addText(refs.map((r, i) => ({ text: r, options: { breakLine: i < refs.length - 1 } })),
    { x: 0.6, y: 1.65, w: 12.1, h: 5.3, fontFace: BF, fontSize: 10.5, color: C.ink, valign: "top", paraSpaceAfter: 5, margin: 0, isTextBox: true });
  footer(s);

  // ---------- 10. Close ----------
  s = add(); s.background = { color: C.navy };
  circ(s, I.shield, 0.6, 0.8, 1.0, C.teal);
  s.addText("Don't assume you can recover.", { x: 0.6, y: 2.2, w: 12, h: 0.9, fontFace: HF, fontSize: 44, bold: true, color: C.white, margin: 0, isTextBox: true });
  s.addText("Let the DR Agent test it for real, and prove it, whenever you need.", { x: 0.6, y: 3.1, w: 12, h: 0.8, fontFace: HF, fontSize: 30, color: "C9C3E6", margin: 0, isTextBox: true });
  [["Real DR drills", "Fail over, measure, fail back: 9 live drills, RTO ~34 s, RPO 0"], ["Built 100% on Azure", "AI Foundry, SQL failover groups, Traffic Manager, Site Recovery, Monitor"], ["Safe by design", "Allowlisted actions, human approval, kill switch, full audit trail"]].forEach(([h, b], i) => {
    const x = 0.6 + i * 4.1;
    s.addText([{ text: h, options: { bold: true, fontSize: 17, color: C.white, breakLine: true } }, { text: b, options: { fontSize: 13, color: "C9C3E6" } }],
      { x, y: 4.6, w: 3.8, h: 1.0, fontFace: BF, valign: "top", margin: 0, isTextBox: true });
  });
  s.addText("Thank you  ·  Team Azure Armada", { x: 0.6, y: 6.5, w: 12.1, h: 0.45, fontFace: BF, fontSize: 15, color: "9C95BD", margin: 0, isTextBox: true });

  await pres.writeFile({ fileName: process.env.DECK_OUT || "docs/deck/DR-Agent-Azure-Day-2026.pptx" });
  console.log("written");
})();
