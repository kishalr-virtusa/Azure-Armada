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
      color: dark ? "B8C7DA" : C.muted, italic: true, margin: 0, isTextBox: true });
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
      x: 0.6, y: 7.08, w: 12.1, h: 0.3, fontFace: BF, fontSize: 9, color: dark ? "8FA3BD" : "8C99AA",
      align: "right", margin: 0, isTextBox: true });

  // ---------- 1. Title ----------
  let s = add(); s.background = { color: C.navy };
  circ(s, I.shield, 0.6, 0.7, 1.0, C.teal);
  s.addText("AZURE DAY 2026 HACKATHON  ·  DISASTER RECOVERY AGENT", { x: 1.85, y: 0.95, w: 9, h: 0.5,
    fontFace: BF, fontSize: 13, bold: true, color: "8FD6D3", charSpacing: 2, margin: 0, isTextBox: true });
  s.addText("Disaster Recovery (DR) Agent", { x: 0.6, y: 2.1, w: 12, h: 1.3, fontFace: HF, fontSize: 54, bold: true,
    color: C.white, margin: 0, isTextBox: true });
  s.addText("Autonomous Disaster Recovery Validation", { x: 0.6, y: 3.35, w: 12, h: 0.7,
    fontFace: HF, fontSize: 28, color: "CFE0F2", margin: 0, isTextBox: true });
  s.addText("Built 100% on Azure. AI agents that validate DR by executing real tests: fail over, measure RTO/RPO, fail back, and prove you can recover, every week instead of once a year.",
    { x: 0.6, y: 4.3, w: 9.5, h: 0.9, fontFace: BF, fontSize: 17, color: "B8C7DA", italic: true, margin: 0, isTextBox: true });
  const steps1 = [["Fail over", I.sync], ["Measure", I.clock], ["Fail back", I.undo], ["Prove", I.audit]];
  steps1.forEach(([t, ic], i) => {
    const x = 0.6 + i * 2.2;
    circ(s, ic, x, 5.65, 0.6, C.navy2);
    s.addText(t, { x: x + 0.72, y: 5.65, w: 1.4, h: 0.6, fontFace: BF, fontSize: 15, bold: true,
      color: C.white, valign: "middle", margin: 0, isTextBox: true });
  });
  s.addText("Team Azure Armada  ·  Virtusa", { x: 8.7, y: 6.75, w: 4.0, h: 0.4, fontFace: BF, fontSize: 13,
    color: "8FA3BD", align: "right", margin: 0, isTextBox: true });
  s.addNotes("The Disaster Recovery (DR) Agent is a team of AI agents built entirely on Azure that actually performs disaster recovery testing: it fails workloads over, measures real recovery time and data loss, fails back, and produces audit-ready proof.");

  // ---------- 2. Problem: what downtime costs ----------
  s = add(); s.background = { color: C.white };
  title(s, "One hour of downtime now costs more than most DR budgets",
    "Independent surveys and real incidents, 2024: the cost of not recovering on time.");
  const stats = [
    ["$300K+", "per hour of downtime", "for over 90% of mid-size and large enterprises. 41% put it at $1M to over $5M per hour, before fines and litigation.", "ITIC 2024 Hourly Cost of Downtime Survey (1,000+ firms) [1]"],
    ["$200M", "per company, every year", "lost to downtime by each Global 2000 company: about 9% of profits. Includes $49M lost revenue and $22M in regulatory fines.", "Splunk & Oxford Economics, The Hidden Costs of Downtime, Jun 2024 (2,000 executives) [2]"],
    ["$5.4B", "from one IT outage", "in direct losses to Fortune 500 companies from the 19 July 2024 CrowdStrike incident; banking alone about $1.15B. Insurance covers only 10–20%.", "Parametrix estimate, Jul 2024 [3]"],
  ];
  stats.forEach(([big, unit, body, src], i) => {
    const x = 0.6 + i * 4.1, y = 1.75;
    card(s, x, y, 3.85, 4.15);
    s.addText(big, { x: x + 0.3, y: y + 0.3, w: 3.3, h: 1.0, fontFace: HF, fontSize: 48, bold: true, color: C.teal, margin: 0, isTextBox: true });
    s.addText(unit, { x: x + 0.3, y: y + 1.3, w: 3.3, h: 0.45, fontFace: BF, fontSize: 16, bold: true, color: C.navy, margin: 0, isTextBox: true });
    s.addText(body, { x: x + 0.3, y: y + 1.85, w: 3.3, h: 1.45, fontFace: BF, fontSize: 13, color: C.ink, valign: "top", margin: 0, isTextBox: true });
    s.addText(src, { x: x + 0.3, y: y + 3.35, w: 3.3, h: 0.6, fontFace: BF, fontSize: 9.5, color: C.muted, italic: true, valign: "bottom", margin: 0, isTextBox: true });
  });
  card(s, 0.6, 6.12, 12.1, 0.72, C.navy, false);
  s.addText([
    { text: "At $300K per hour, every minute of downtime costs about $5,000.", options: { bold: true, color: C.white } },
    { text: "   Recovery that is assumed, not proven, is an unpriced risk.", options: { color: "C9C3E6" } },
  ], { x: 0.85, y: 6.12, w: 11.7, h: 0.72, fontFace: BF, fontSize: 16, valign: "middle", margin: 0, isTextBox: true });
  footer(s);
  s.addNotes("Start with money. ITIC's 2024 survey of over 1,000 firms: for more than 90% of mid-size and large enterprises one hour of downtime costs over $300,000, and 41% say $1M to over $5M, excluding fines and litigation. Splunk with Oxford Economics (June 2024, 2,000 executives): downtime costs each Global 2000 company about $200M a year, roughly 9% of profits, including $22M of regulatory fines. And in July 2024 one faulty update caused an estimated $5.4B of direct losses to the Fortune 500, $1.15B of it in banking. Simple arithmetic: $300K an hour is $5,000 a minute.");

  // ---------- 3. Problem: India, consequences ----------
  s = add(); s.background = { color: C.white };
  title(s, "In India, failing to recover is punished by freezing growth",
    "Regulators act on outages and weak disaster recovery, not just on breaches.");
  const cases = [
    ["HDFC Bank", "Dec 2020", "After repeated outages, the latest caused by a power failure at a primary data centre, the RBI stopped all new digital launches and new credit-card sourcing.", "≈15 months", "until digital launches were allowed again (Mar 2022); cards resumed Aug 2021", "[4]"],
    ["NSE", "Feb 2021", "Trading was halted for nearly four hours on 24 February 2021. NSE and NSE Clearing settled with SEBI; executives were ordered to do community service and training.", "₹72.64 crore", "paid by NSE and NSE Clearing to settle (2023)", "[5]"],
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
  s.addNotes("Closer to home, the penalty is a growth freeze. HDFC Bank: after repeated outages, the most recent a power failure at a primary data centre, the RBI stopped new digital launches and new credit cards in December 2020; cards resumed in August 2021, digital launches only in March 2022. NSE: a nearly four-hour trading halt in February 2021 ended in a ₹72.64 crore settlement with SEBI. Kotak Mahindra Bank: in April 2024 the RBI cited deficiencies including business continuity and disaster recovery, plus frequent outages, and stopped online onboarding and new cards for about ten months.");

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
     cell("Enforcement and settlements, as with NSE: ₹72.64 crore. [5]")],
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
    ["85%", "of major human-error outages come from staff not following procedures, or from flawed procedures", "Uptime Institute, Annual Outage Analysis 2025 [12]"],
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
  s.addNotes("So why don't companies just test? Cockroach Labs surveyed 1,000 executives: fewer than one in three do regular failover testing, and only 20% feel fully prepared. Uptime Institute found 85% of major human-error outages come from procedures not followed or flawed procedures, which is exactly what a stale runbook is. The reasons are structural: tests are disruptive and manual, environments drift between tests, runbooks go stale, and RTO/RPO are signed off on paper instead of measured.");

  // ---------- 6. Problem: worked example ----------
  s = add(); s.background = { color: C.white };
  title(s, "What an unproven DR plan costs: one bad morning",
    "Illustration for a tier-1 payments service, using the cited figures. Not a forecast; the inputs are shown.");
  const rows = [
    ["Recovery target (RTO)", "30 minutes", "Signed off on paper; never measured end to end"],
    ["Actual recovery", "4 hours", "Comparable to the NSE trading halt of Feb 2021 [5]"],
    ["Hourly cost of downtime", "$300,000", "ITIC 2024 floor for 90%+ of mid-size and large firms [1]"],
    ["Direct loss", "$1.2M", "4 h × $300K; $1.05M of it beyond the 30-minute target"],
    ["Regulatory precedent", "₹72.64 crore", "What NSE paid to settle its 2021 outage case [5]; banks have faced 10–15 month business restrictions [4][6]"],
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
  s.addNotes("Put it together with transparent inputs. A tier-1 payments service with a 30-minute target that has never really been tested. On the day, recovery takes four hours, like the NSE halt. At ITIC's floor of $300,000 an hour, that is $1.2M of direct loss, $1.05M of it beyond the target, before any regulatory action: the NSE settlement was ₹72.64 crore, and the RBI froze HDFC's and Kotak's digital growth for 10 to 15 months. Our agent measured 34.35 seconds for the same kind of service, with zero payments lost, and it can prove it again every week.");

  // ---------- 3. Solution loop ----------
  s = add(); s.background = { color: C.white };
  title(s, "Validation by execution: the agent runs real DR tests",
    "Not just DR design review — autonomous, end-to-end failover testing with a human approving anything risky.");
  const loop = [
    [I.search, "Discover", "Map workload & dependencies"],
    [I.check, "Assess", "Find readiness gaps"],
    [I.route, "Plan test", "Order failover steps"],
    [I.user, "Approve", "Human sign-off in Teams"],
    [I.play, "Execute", "Run the failover drill"],
    [I.heart, "Validate", "Synthetic tests, RTO/RPO"],
    [I.undo, "Fail back", "Restore & clean up"],
    [I.file, "Fix & prove", "Bicep PRs, evidence pack"],
  ];
  loop.forEach(([ic, h, b], i) => {
    const x = 0.6 + i * 1.53;
    const core = i >= 4 && i <= 6;
    if (i < loop.length - 1)
      s.addShape(pres.shapes.LINE, { x: x + 0.95, y: 2.35, w: 1.05, h: 0, line: { color: C.line, width: 2, endArrowType: "triangle" } });
    circ(s, ic, x + 0.2, 1.9, 0.9, core ? C.teal : C.navy);
    s.addText(h, { x: x - 0.1, y: 2.9, w: 1.5, h: 0.4, fontFace: BF, fontSize: 14, bold: true,
      color: core ? C.teal : C.navy, align: "center", margin: 0, isTextBox: true });
    s.addText(b, { x: x - 0.1, y: 3.28, w: 1.5, h: 0.55, fontFace: BF, fontSize: 11, color: C.muted,
      align: "center", margin: 0, isTextBox: true });
  });
  s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x: 6.6, y: 1.72, w: 4.65, h: 2.2, rectRadius: 0.1,
    fill: { color: C.teal, transparency: 92 }, line: { color: C.teal, width: 1.25, dashType: "dash" } });
  s.addText("CORE: LIVE DR TEST", { x: 6.6, y: 1.42, w: 4.65, h: 0.28, fontFace: BF, fontSize: 10, bold: true,
    color: C.teal, align: "center", charSpacing: 2, margin: 0, isTextBox: true });
  // Before / after
  const rowsBA = [
    ["DR testing frequency", "Once or twice a year", "Every week, or after every change"],
    ["Effort per drill", "Weeks, many teams, weekends", "Automated; engineers approve & review"],
    ["RTO / RPO", "Assumed on paper", "Measured on every test"],
    ["Gaps found", "During a real outage", "Before the outage — with a fix PR"],
    ["Audit evidence", "Screenshots & spreadsheets", "Auto-generated evidence pack"],
  ];
  card(s, 0.6, 4.25, 12.1, 2.7, C.card, false);
  s.addText("Today", { x: 4.1, y: 4.35, w: 4, h: 0.35, fontFace: BF, fontSize: 13, bold: true, color: C.red, margin: 0, isTextBox: true });
  s.addText("With the DR Agent", { x: 8.3, y: 4.35, w: 4.2, h: 0.35, fontFace: BF, fontSize: 13, bold: true, color: C.teal, margin: 0, isTextBox: true });
  rowsBA.forEach(([a, b, c], i) => {
    const y = 4.75 + i * 0.42;
    s.addText(a, { x: 0.9, y, w: 3.1, h: 0.38, fontFace: BF, fontSize: 13, bold: true, color: C.navy, margin: 0, valign: "middle", isTextBox: true });
    s.addText(b, { x: 4.1, y, w: 4.0, h: 0.38, fontFace: BF, fontSize: 13, color: C.ink, margin: 0, valign: "middle", isTextBox: true });
    s.addText(c, { x: 8.3, y, w: 4.2, h: 0.38, fontFace: BF, fontSize: 13, bold: true, color: C.ink, margin: 0, valign: "middle", isTextBox: true });
  });
  footer(s, 3);
  s.addNotes("The agent closes the full loop. The highlighted steps — execute, validate, fail back — are the heart of the solution: the agent really performs the DR test rather than only reviewing design.");

  // ---------- 4. DR tests executed ----------
  s = add(); s.background = { color: C.white };
  title(s, "How the agent validates DR: the tests it executes",
    "Real failovers on Azure, ordered by dependency (data → app → traffic), with measured RTO/RPO and automatic failback.");
  const tests = [
    [I.server, "VM / IaaS failover", "Fail over replicated VMs into an isolated test VNet, boot, health-check, clean up.", "Azure Site Recovery · Test Failover"],
    [I.db, "Database failover", "Planned failover of SQL failover group / Cosmos DB region; measure replication lag and switch time.", "SQL Failover Groups · Cosmos DB"],
    [I.hdd, "Storage failover", "Initiate storage account failover in non-prod; verify data is readable in the secondary region.", "GRS / GZRS customer-managed failover"],
    [I.globe, "Traffic / region switch", "Shift user traffic to the secondary region and run synthetic user journeys end to end.", "Azure Front Door · Traffic Manager"],
    [I.flask, "Fault injection", "Simulate zone outage, network block, Key Vault denial or AKS pod loss — prove the app survives.", "Azure Chaos Studio"],
    [I.undo, "Failback", "Return to primary, re-protect replication, verify nothing broke on the way back.", "ASR re-protect · SQL failover"],
  ];
  tests.forEach(([ic, h, b, svc], i) => {
    const col = i % 3, row = Math.floor(i / 3);
    const x = 0.6 + col * 4.1, y = 1.75 + row * 2.05;
    card(s, x, y, 3.85, 1.85);
    circ(s, ic, x + 0.25, y + 0.25, 0.6, C.teal);
    s.addText(h, { x: x + 1.0, y: y + 0.25, w: 2.7, h: 0.6, fontFace: BF, fontSize: 16, bold: true,
      color: C.navy, valign: "middle", margin: 0, isTextBox: true });
    s.addText(b, { x: x + 0.25, y: y + 0.95, w: 3.4, h: 0.55, fontFace: BF, fontSize: 11.5, color: C.ink, margin: 0, valign: "top", isTextBox: true });
    s.addText(svc, { x: x + 0.25, y: y + 1.5, w: 3.4, h: 0.28, fontFace: BF, fontSize: 10.5, bold: true, color: C.teal, margin: 0, isTextBox: true });
  });
  const modes = [
    ["Isolated test", "Production-safe · no user impact · runs weekly", C.teal],
    ["Full drill", "Non-prod / staging · real region failover and failback", C.navy],
    ["Game day", "Production · explicit approval, kill switch, auto-rollback", C.amber],
  ];
  s.addText("TESTING MODES", { x: 0.6, y: 5.95, w: 2, h: 0.3, fontFace: BF, fontSize: 10, bold: true, color: C.muted, charSpacing: 2, margin: 0, isTextBox: true });
  modes.forEach(([h, b, col], i) => {
    const x = 0.6 + i * 4.1;
    s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x, y: 6.25, w: 3.85, h: 0.75, rectRadius: 0.08, fill: { color: col }, line: { color: col } });
    s.addText([{ text: h, options: { bold: true, fontSize: 14, breakLine: true } }, { text: b, options: { fontSize: 10.5 } }],
      { x: x + 0.2, y: 6.25, w: 3.5, h: 0.75, fontFace: BF, color: col === C.amber ? C.navy : C.white, valign: "middle", margin: 0, isTextBox: true });
  });
  footer(s, 4);
  s.addNotes("This is the heart of the solution: the agent performs real DR tests. Three modes let it run safely everywhere — isolated test failovers weekly in production, full drills in staging, and approved game days in production.");

  // ---------- 5. Agent team ----------
  s = add(); s.background = { color: C.white };
  title(s, "A team of specialist agents on Azure AI Foundry",
    "Each agent has its own tools, its own Managed Identity and least-privilege RBAC.");
  card(s, 0.6, 1.7, 12.1, 0.95, C.navy);
  circ(s, I.brain, 0.85, 1.85, 0.65, C.teal);
  s.addText([{ text: "Orchestrator Agent  ", options: { bold: true, fontSize: 17 } },
    { text: "Takes a goal (\"Run a DR test on the Payments workload\"), plans the drill, delegates to specialists, enforces guardrails and approval gates.", options: { fontSize: 13 } }],
    { x: 1.7, y: 1.7, w: 7.6, h: 0.95, fontFace: BF, color: C.white, valign: "middle", margin: 0, isTextBox: true });
  s.addText("Azure AI Foundry Agent Service · Azure OpenAI · Microsoft Agent Framework", { x: 9.4, y: 1.7, w: 3.1, h: 0.95,
    fontFace: BF, fontSize: 10.5, color: "8FD6D3", valign: "middle", align: "right", margin: 0, isTextBox: true });
  const agents = [
    [I.play, "DR Test Executor", "CORE · Runs failovers in dependency order, monitors each step, rolls back or fails back.", "Site Recovery · Chaos Studio · Durable Functions", true],
    [I.heart, "Validation", "Synthetic transactions on the recovered app, data-integrity checks, measures actual RTO & RPO.", "Azure Monitor · App Insights · Load Testing", true],
    [I.search, "Discovery", "Builds a live dependency graph of the workload and ranks components by criticality.", "Resource Graph · App Insights map · Cosmos DB", false],
    [I.check, "Readiness Assessor", "Pre-test checks: replication, failover groups, secondary Key Vault, quota, Front Door, NSGs.", "Azure Policy · Business Continuity Center", false],
    [I.book, "Runbook Intelligence", "Reads existing runbooks (RAG), flags stale steps, generates an executable test plan.", "Azure AI Search · Document Intelligence", false],
    [I.branch, "Remediation & Reporting", "Bicep/Terraform fix PRs, work items, evidence pack and resilience score.", "Azure DevOps / GitHub · Power BI · Teams", false],
  ];
  agents.forEach(([ic, h, b, svc, core], i) => {
    const col = i % 3, row = Math.floor(i / 3);
    const x = 0.6 + col * 4.1, y = 2.9 + row * 2.05;
    card(s, x, y, 3.85, 1.85, core ? C.tealLight : C.card);
    circ(s, ic, x + 0.25, y + 0.25, 0.6, core ? C.teal : C.navy);
    s.addText(h + " Agent", { x: x + 1.0, y: y + 0.25, w: 2.75, h: 0.6, fontFace: BF, fontSize: 15, bold: true,
      color: C.navy, valign: "middle", margin: 0, isTextBox: true });
    s.addText(b, { x: x + 0.25, y: y + 0.92, w: 3.4, h: 0.6, fontFace: BF, fontSize: 11.5, color: C.ink, margin: 0, valign: "top", isTextBox: true });
    s.addText(svc, { x: x + 0.25, y: y + 1.5, w: 3.4, h: 0.28, fontFace: BF, fontSize: 10, bold: true, color: C.teal, margin: 0, isTextBox: true });
  });
  footer(s, 5);
  s.addNotes("Seven agents. The DR Test Executor and Validation agents (highlighted) do the actual testing; the others prepare the test and turn results into fixes and evidence.");

  // ---------- 6. Architecture ----------
  s = add(); s.background = { color: C.white };
  title(s, "Azure architecture", "100% Azure-native · agents orchestrate real Azure resilience services against the workload under test.");
  const box = (x, y, w, h, fill, line, text, opts = {}) => {
    s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x, y, w, h, rectRadius: 0.06, fill: { color: fill }, line: { color: line, width: 1 } });
    if (text) s.addText(text, { x, y, w, h, fontFace: BF, fontSize: 10, color: C.ink, align: "center", valign: "middle", margin: 3, isTextBox: true, ...opts });
  };
  const lbl = (t, x, y, w, col = C.muted) => s.addText(t, { x, y, w, h: 0.26, fontFace: BF, fontSize: 9.5, bold: true, color: col, charSpacing: 1.5, margin: 0, isTextBox: true });
  // Triggers
  lbl("TRIGGERS", 0.6, 1.62, 1.9);
  [["Schedule", "Logic Apps"], ["Infra change", "Event Grid"], ["On demand", "Teams / Portal"]].forEach(([a, b], i) =>
    box(0.6, 1.95 + i * 0.85, 1.9, 0.7, C.card, C.line, [{ text: a, options: { bold: true, breakLine: true } }, { text: b, options: { color: C.muted } }]));
  s.addShape(pres.shapes.LINE, { x: 2.5, y: 2.95, w: 0.35, h: 0, line: { color: C.navy, width: 1.5, endArrowType: "triangle" } });
  // Foundry
  s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x: 2.9, y: 1.6, w: 6.6, h: 2.65, rectRadius: 0.08, fill: { color: C.navy }, line: { color: C.navy } });
  lbl("AZURE AI FOUNDRY AGENT SERVICE", 3.1, 1.7, 5, "8FD6D3");
  box(3.1, 2.02, 6.2, 0.55, C.teal, C.teal, "Orchestrator Agent  ·  Azure OpenAI (GPT-4.1 / o-series)  ·  Agent Framework", { color: C.white, bold: true, fontSize: 11 });
  const ag = ["Discovery", "Readiness", "Runbook", "DR Test\nExecutor", "Validation", "Remediate\n& Report"];
  ag.forEach((a, i) => {
    const x = 3.1 + i * 1.04;
    const core = i === 3 || i === 4;
    box(x, 2.75, 0.96, 0.8, core ? C.amber : C.navy2, core ? C.amber : "3B5A85", a, { color: core ? C.navy : C.white, bold: true, fontSize: 10 });
  });
  s.addText("Agent memory & state: Cosmos DB  ·  Tracing: Application Insights", { x: 3.1, y: 3.7, w: 6.2, h: 0.4, fontFace: BF, fontSize: 10, color: "B8C7DA", align: "center", margin: 0, isTextBox: true });
  // Tools row
  lbl("AZURE SERVICES USED AS AGENT TOOLS", 2.9, 4.38, 6);
  const tools = ["Resource Graph", "Policy · BCC · Defender", "AI Search · Blob · Doc Intel", "Site Recovery · Chaos Studio · Automation", "Monitor · Log Analytics · Load Testing", "DevOps / GitHub · Power BI"];
  tools.forEach((t, i) => {
    const x = 3.1 + i * 1.04;
    s.addShape(pres.shapes.LINE, { x: x + 0.48, y: 4.25, w: 0, h: 0.42, line: { color: C.line, width: 1, dashType: "dash" } });
    box(x, 4.67, 0.96, 1.0, (i === 3 || i === 4) ? C.amberLight : C.tealLight, (i === 3 || i === 4) ? C.amber : C.teal, t, { fontSize: 9 });
  });
  // Outputs
  lbl("PEOPLE & OUTPUTS", 9.95, 1.62, 2.8);
  [["Teams approvals", "Human-in-the-loop"], ["Power BI", "Resilience score"], ["Bicep / Terraform PRs", "Gap fixes"], ["Evidence pack", "Audit-ready report"]].forEach(([a, b], i) =>
    box(9.95, 1.95 + i * 0.68, 2.75, 0.56, C.card, C.line, [{ text: a + "  ", options: { bold: true } }, { text: b, options: { color: C.muted } }]));
  s.addShape(pres.shapes.LINE, { x: 9.5, y: 2.95, w: 0.45, h: 0, line: { color: C.navy, width: 1.5, endArrowType: "triangle", beginArrowType: "triangle" } });
  // Workload under test
  lbl("WORKLOAD UNDER TEST", 2.9, 5.82, 4);
  box(3.1, 6.12, 2.75, 0.85, C.white, C.navy, [{ text: "Region A · Primary", options: { bold: true, breakLine: true } }, { text: "Front Door · App Service · SQL · Storage · Key Vault", options: { fontSize: 9, color: C.muted } }]);
  box(6.55, 6.12, 2.75, 0.85, C.white, C.navy, [{ text: "Region B · Secondary", options: { bold: true, breakLine: true } }, { text: "Replicas · Failover group · GZRS · Isolated test VNet", options: { fontSize: 9, color: C.muted } }]);
  s.addShape(pres.shapes.LINE, { x: 5.85, y: 6.55, w: 0.7, h: 0, line: { color: C.amber, width: 2, endArrowType: "triangle", beginArrowType: "triangle" } });
  s.addText("failover\nfailback", { x: 5.85, y: 6.62, w: 0.7, h: 0.35, fontFace: BF, fontSize: 8, color: C.amber, bold: true, align: "center", margin: 0, isTextBox: true });
  s.addShape(pres.shapes.LINE, { x: 6.2, y: 5.67, w: 0, h: 0.5, line: { color: C.amber, width: 2, endArrowType: "triangle" } });
  // Security
  card(s, 9.95, 4.75, 2.75, 2.22, C.navy, false);
  circ(s, I.lock, 10.1, 4.88, 0.45, C.teal);
  s.addText("Security & governance", { x: 10.62, y: 4.88, w: 2.0, h: 0.45, fontFace: BF, fontSize: 11.5, bold: true, color: C.white, valign: "middle", margin: 0, isTextBox: true });
  s.addText([
    { text: "Managed Identity per agent", options: { bullet: true, breakLine: true } },
    { text: "RBAC + Entra PIM (JIT)", options: { bullet: true, breakLine: true } },
    { text: "Key Vault for secrets", options: { bullet: true, breakLine: true } },
    { text: "AI Content Safety", options: { bullet: true, breakLine: true } },
    { text: "Kill switch + auto-rollback", options: { bullet: true } },
  ], { x: 10.1, y: 5.4, w: 2.5, h: 1.5, fontFace: BF, fontSize: 10, color: "DCE6F2", margin: 0, paraSpaceAfter: 2, valign: "top", isTextBox: true });
  footer(s, 6);
  s.addNotes("Triggers start a DR test run. The orchestrator in AI Foundry delegates to specialist agents, which use Azure services as tools. Amber highlights the testing path: the DR Test Executor drives Site Recovery, SQL failover groups, Front Door and Chaos Studio against the workload, failing over Region A to Region B and back, while the Validation agent measures RTO/RPO through Azure Monitor.");

  // ---------- 7. Guardrails ----------
  s = add(); s.background = { color: C.white };
  title(s, "Autonomy with guardrails", "Safe enough for BFSI and healthcare: the riskier the action, the stronger the control.");
  const ladder = [
    ["Read-only discovery & assessment", "Fully autonomous", C.teal, 1],
    ["Isolated test failover (no user impact)", "Autonomous, pre-approved policy", C.teal, 2],
    ["Full drill in non-prod", "Approval in Teams", C.navy, 3],
    ["Production game day", "Approval + kill switch + auto-rollback", C.amber, 4],
  ];
  s.addText("AUTONOMY LADDER", { x: 0.6, y: 1.75, w: 5, h: 0.3, fontFace: BF, fontSize: 10, bold: true, color: C.muted, charSpacing: 2, margin: 0, isTextBox: true });
  ladder.forEach(([a, b, col, lv], i) => {
    const y = 5.3 - i * 1.05, w = 3.4 + lv * 0.6;
    s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x: 0.6, y, w, h: 0.85, rectRadius: 0.08, fill: { color: col }, line: { color: col } });
    s.addText([{ text: a, options: { bold: true, fontSize: 14, breakLine: true } }, { text: b, options: { fontSize: 11.5 } }],
      { x: 0.85, y, w: w - 0.4, h: 0.85, fontFace: BF, color: col === C.amber ? C.navy : C.white, valign: "middle", margin: 0, isTextBox: true });
  });
  const guards = [
    [I.key, "Least privilege", "A Managed Identity per agent with scoped RBAC; write actions need just-in-time elevation via Entra PIM."],
    [I.power, "Kill switch & rollback", "Any step can be halted; the agent rolls back automatically if health checks fail mid-test."],
    [I.eye, "Full traceability", "Every decision, tool call and approval logged to Log Analytics & AI Foundry tracing."],
    [I.shield, "Responsible AI", "Content Safety and prompt-injection shields on all inputs; secrets never reach prompts."],
  ];
  guards.forEach(([ic, h, b], i) => {
    const y = 1.8 + i * 1.3;
    circ(s, ic, 7.2, y, 0.7, C.navy);
    s.addText(h, { x: 8.1, y: y - 0.02, w: 4.6, h: 0.38, fontFace: BF, fontSize: 16, bold: true, color: C.navy, margin: 0, isTextBox: true });
    s.addText(b, { x: 8.1, y: y + 0.36, w: 4.6, h: 0.7, fontFace: BF, fontSize: 12.5, color: C.ink, margin: 0, valign: "top", isTextBox: true });
  });
  footer(s, 7);
  s.addNotes("Because the agent performs real failovers, safety is designed in: graduated autonomy, least-privilege identities, kill switch with auto-rollback, and a full audit trail.");

  // ---------- 8. MVP & demo ----------
  s = add(); s.background = { color: C.white };
  title(s, "What we'll build and demo live", "Sample 3-tier app across two regions with deliberately planted DR gaps — the DR Agent validates it for real.");
  const tl = [["Oct 15", "Idea submitted"], ["Oct 16–17", "Two-region workload + planted gaps (Bicep)"], ["Oct 18–20", "Agents, tools & Teams approvals"], ["Oct 21–22", "End-to-end DR test runs, reports"], ["Oct 23", "Build demo to panel"], ["Oct 29", "Finals at Azure Day"]];
  s.addShape(pres.shapes.LINE, { x: 0.9, y: 2.05, w: 11.5, h: 0, line: { color: C.line, width: 2 } });
  tl.forEach(([d, t], i) => {
    const x = 0.6 + i * 2.05;
    s.addShape(pres.shapes.OVAL, { x: x + 0.2, y: 1.9, w: 0.3, h: 0.3, fill: { color: i >= 4 ? C.amber : C.teal }, line: { color: C.white, width: 2 } });
    s.addText(d, { x, y: 2.3, w: 1.95, h: 0.32, fontFace: BF, fontSize: 13, bold: true, color: C.navy, margin: 0, isTextBox: true });
    s.addText(t, { x, y: 2.62, w: 1.9, h: 0.55, fontFace: BF, fontSize: 11, color: C.muted, margin: 0, valign: "top", isTextBox: true });
  });
  const demo = [
    [I.search, "1  Discover & assess", "Maps the app, flags planted gaps: LRS storage, stale runbook step."],
    [I.user, "2  Approve in Teams", "Agent proposes a DR test plan; a human approves with one click."],
    [I.play, "3  Run the DR test live", "SQL failover group failover, Front Door switch to Region B, Chaos Studio zone-down."],
    [I.warn, "4  Catch a real failure", "App breaks in Region B — secret missing from secondary Key Vault. Measured RTO/RPO shown."],
    [I.undo, "5  Fail back, fix, re-test", "Fails back, raises a Bicep fix PR, re-runs the test: it now passes."],
    [I.audit, "6  Prove it", "Publishes evidence pack & resilience score to Power BI."],
  ];
  demo.forEach(([ic, h, b], i) => {
    const col = i % 3, row = Math.floor(i / 3);
    const x = 0.6 + col * 4.1, y = 3.5 + row * 1.75;
    const hot = i === 2 || i === 3;
    card(s, x, y, 3.85, 1.55, hot ? C.amberLight : C.card);
    circ(s, ic, x + 0.25, y + 0.25, 0.55, hot ? C.amber : C.teal);
    s.addText(h, { x: x + 0.95, y: y + 0.25, w: 2.8, h: 0.55, fontFace: BF, fontSize: 15, bold: true, color: C.navy, valign: "middle", margin: 0, isTextBox: true });
    s.addText(b, { x: x + 0.25, y: y + 0.88, w: 3.4, h: 0.6, fontFace: BF, fontSize: 11.5, color: C.ink, margin: 0, valign: "top", isTextBox: true });
  });
  footer(s, 8);
  s.addNotes("The demo is a real DR test, not slides: the agent fails over a live two-region app, catches a planted gap through the test itself, fails back, fixes it via a Bicep PR, and re-tests to green.");

  // ---------- 9. Benefits ----------
  s = add(); s.background = { color: C.white };
  title(s, "Business benefits", "Recovery you can prove — continuously, safely and at a fraction of today's effort.");
  const big = [["Once a year", "Every week", "DR testing frequency"], ["Weeks", "Hours", "Effort per DR test"], ["Assumed", "Measured", "RTO / RPO on every test"]];
  big.forEach(([a, b, l], i) => {
    const x = 0.6 + i * 4.1;
    card(s, x, 1.75, 3.85, 1.75, C.navy, false);
    s.addText([{ text: a, options: { color: "8FA3BD", fontSize: 15, strike: "sngStrike" } }, { text: "  →", options: { color: "8FA3BD", fontSize: 15, breakLine: true } }, { text: b, options: { color: C.white, fontSize: 30, bold: true, fontFace: HF } }],
      { x: x + 0.3, y: 1.9, w: 3.4, h: 0.95, fontFace: BF, valign: "middle", margin: 0, isTextBox: true });
    s.addText(l, { x: x + 0.3, y: 2.85, w: 3.4, h: 0.45, fontFace: BF, fontSize: 13, color: "8FD6D3", margin: 0, isTextBox: true });
  });
  const ben = [
    [I.bolt, "Lower downtime risk", "Failover blockers found and fixed before a real outage — protecting revenue, SLAs and trust."],
    [I.gavel, "Audit-ready compliance", "Auto-generated test evidence for RBI, SEBI, DORA, SOX, ISO 22301."],
    [I.tools, "Self-healing posture", "Every gap becomes a ready-to-merge IaC fix, re-tested automatically."],
    [I.book, "Living runbooks", "Runbooks always match the live architecture — less reliance on a few experts."],
    [I.chart, "Data-driven decisions", "Leadership sees real recovery metrics per application to target investment."],
    [I.hand, "Strategic for Virtusa", "Reusable \"Resilience-as-a-Service\" accelerator for BFSI and managed-services clients."],
  ];
  ben.forEach(([ic, h, b], i) => {
    const col = i % 3, row = Math.floor(i / 3);
    const x = 0.6 + col * 4.1, y = 3.85 + row * 1.6;
    circ(s, ic, x, y, 0.6, C.teal);
    s.addText(h, { x: x + 0.78, y: y - 0.02, w: 3.1, h: 0.36, fontFace: BF, fontSize: 15, bold: true, color: C.navy, margin: 0, isTextBox: true });
    s.addText(b, { x: x + 0.78, y: y + 0.36, w: 3.1, h: 0.95, fontFace: BF, fontSize: 12, color: C.ink, margin: 0, valign: "top", isTextBox: true });
  });
  footer(s, 9);
  s.addNotes("Headline value: continuous, measured DR testing at a fraction of today's effort, with compliance evidence produced automatically. For Virtusa it becomes a reusable accelerator.");

  // ---------- Sources ----------
  s = add(); s.background = { color: C.white };
  title(s, "Sources", "Every figure in this deck is taken from the source below. Our own figures come from live drills on Azure.");
  const refs = [
    "[1] ITIC, 2024 Hourly Cost of Downtime Survey, Part 2 (L. DiDio), 1,000+ firms, Nov 2023–Mar 2024. calyptix.com/wp-content/uploads/ITIC-2024-Hourly-Cost-of-Downtime-Survey-Results-Part-2.pdf",
    "[2] Splunk with Oxford Economics, The Hidden Costs of Downtime, 12 Jun 2024. splunk.com/en_us/blog/cio-office/uncovering-downtime.html",
    "[3] Parametrix estimate of CrowdStrike outage losses (19 Jul 2024), reported by Dark Reading and Cybersecurity Dive, Jul 2024. darkreading.com/cybersecurity-operations/crowdstrike-outage-losses-estimated-staggering-54b",
    "[4] RBI order to HDFC Bank, 2 Dec 2020 (Outlook Business, 3 Dec 2020); curbs lifted 17 Aug 2021 (cards) and 11 Mar 2022 (digital) (Business Standard, 12 Mar 2022).",
    "[5] NSE and NSE Clearing settle the 24 Feb 2021 trading-halt case with SEBI for ₹72.64 crore (Moneylife; Business Standard, Jun 2023).",
    "[6] RBI action on Kotak Mahindra Bank under Sec. 35A, 24 Apr 2024 (TechCrunch); restrictions lifted 12 Feb 2025 (Business Standard).",
    "[7] RBI, Cybersecurity, Technology: Risk, Resilience and Assurance Framework Directions, 31 Jul 2026, summarised by KPMG India, Sep 2026.",
    "[8] SEBI Cybersecurity and Cyber Resilience Framework: RTO 2 hours, RPO 15 minutes (clarification reported by PTI / The Week, 28 Aug 2025).",
    "[9] SEBI framework for market infrastructure institutions: DR site within 45 minutes, quarterly drills (Business Standard, 22 Mar 2021).",
    "[10] Regulation (EU) 2022/2554 (DORA), Articles 11(6), 12, 35(7)–(8), 50, 51. eur-lex.europa.eu/eli/reg/2022/2554/oj",
    "[11] Cockroach Labs with Wakefield Research, The State of Resilience 2025 (1,000 executives, Aug–Sep 2024). cockroachlabs.com",
    "[12] Uptime Institute, Annual Outage Analysis 2025, press release 6 May 2025. uptimeinstitute.com",
  ];
  s.addText(refs.map((r, i) => ({ text: r, options: { breakLine: i < refs.length - 1 } })),
    { x: 0.6, y: 1.65, w: 12.1, h: 5.2, fontFace: BF, fontSize: 11, color: C.ink, valign: "top", paraSpaceAfter: 5, margin: 0, isTextBox: true });
  footer(s);

  // ---------- 10. Close ----------
  s = add(); s.background = { color: C.navy };
  circ(s, I.shield, 0.6, 0.8, 1.0, C.teal);
  s.addText("Don't assume you can recover.", { x: 0.6, y: 2.2, w: 12, h: 0.9, fontFace: HF, fontSize: 44, bold: true, color: C.white, margin: 0, isTextBox: true });
  s.addText("Let the DR Agent validate it, and prove it, every week.", { x: 0.6, y: 3.1, w: 12, h: 0.8, fontFace: HF, fontSize: 30, color: "8FD6D3", margin: 0, isTextBox: true });
  [["Real DR tests", "Failover, validate, failback — executed by agents"], ["Built 100% on Azure", "AI Foundry, Site Recovery, Chaos Studio, Monitor"], ["Safe by design", "Graduated autonomy, human approval, full audit trail"]].forEach(([h, b], i) => {
    const x = 0.6 + i * 4.1;
    s.addText([{ text: h, options: { bold: true, fontSize: 17, color: C.white, breakLine: true } }, { text: b, options: { fontSize: 13, color: "B8C7DA" } }],
      { x, y: 4.6, w: 3.8, h: 1.0, fontFace: BF, valign: "top", margin: 0, isTextBox: true });
  });
  s.addText("Thank you  ·  Team Azure Armada", { x: 0.6, y: 6.5, w: 12.1, h: 0.45, fontFace: BF, fontSize: 15, color: "8FA3BD", margin: 0, isTextBox: true });

  await pres.writeFile({ fileName: process.env.DECK_OUT || "docs/deck/DR-Agent-Azure-Day-2026.pptx" });
  console.log("written");
})();
