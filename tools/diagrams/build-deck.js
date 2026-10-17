const pptxgen = require("pptxgenjs");
const React = require("react");
const ReactDOMServer = require("react-dom/server");
const sharp = require("sharp");
const fa = require("react-icons/fa");

// Palette: deep navy (dominant), recovery teal, risk amber accent
const C = {
  navy: "0B1F3A", navy2: "14305A", teal: "0E8C8C", tealLight: "D9F0EF",
  amber: "F2A541", amberLight: "FDF0DB", red: "D64545",
  ink: "1B2635", muted: "5B6B7F", line: "C9D3DE", card: "F1F5F9", white: "FFFFFF",
};
const HF = "Cambria", BF = "Calibri";

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
  const footer = (s, n, dark = false) =>
    s.addText(`DR Agent: Autonomous DR Validation  ·  Team Azure Armada  ·  Azure Day 2026 Hackathon   ${n}`, {
      x: 0.6, y: 7.08, w: 12.1, h: 0.3, fontFace: BF, fontSize: 9, color: dark ? "8FA3BD" : "8C99AA",
      align: "right", margin: 0, isTextBox: true });

  // ---------- 1. Title ----------
  let s = pres.addSlide(); s.background = { color: C.navy };
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

  // ---------- 2. Problem ----------
  s = pres.addSlide(); s.background = { color: C.white };
  title(s, "Everyone has a DR plan. Few can prove it works today.",
    "Recovery gaps stay hidden until a real outage — when downtime costs the most.");
  const probs = [
    [I.cal, "Testing is rare and manual", "Drills happen once or twice a year, take weeks of multi-team effort and weekend work, so coverage is minimal."],
    [I.drift, "Drift silently breaks recovery", "LRS storage, secrets only in primary, missing quota, unmirrored firewall rules — changes made daily, never re-tested."],
    [I.book, "Runbooks go stale", "Word docs and wikis describe last year's architecture. Engineers discover broken steps mid-incident."],
    [I.clock, "RTO / RPO are assumed", "Recovery targets are signed off on paper; actual recovery time and data loss are rarely measured end-to-end."],
    [I.gavel, "Compliance evidence is manual", "RBI, SEBI, DORA, SOX and ISO 22301 demand demonstrated resilience — evidence is assembled from screenshots."],
    [I.graph, "Dependencies are invisible", "Failing over one tier breaks another: identity, Key Vault, messaging and DNS links are rarely mapped."],
  ];
  probs.forEach(([ic, h, b], i) => {
    const col = i % 3, row = Math.floor(i / 3);
    const x = 0.6 + col * 4.1, y = 1.85 + row * 2.55;
    card(s, x, y, 3.85, 2.3);
    circ(s, ic, x + 0.3, y + 0.3, 0.65, row === 0 && col === 1 ? C.amber : C.navy);
    s.addText(h, { x: x + 1.1, y: y + 0.3, w: 2.6, h: 0.65, fontFace: BF, fontSize: 16, bold: true,
      color: C.navy, valign: "middle", margin: 0, isTextBox: true });
    s.addText(b, { x: x + 0.3, y: y + 1.1, w: 3.3, h: 1.05, fontFace: BF, fontSize: 13, color: C.ink,
      valign: "top", margin: 0, isTextBox: true });
  });
  footer(s, 2);
  s.addNotes("The core problem: DR is designed but not continuously tested. Drift between annual drills silently breaks recovery, and RTO/RPO are assumed rather than measured.");

  // ---------- 3. Solution loop ----------
  s = pres.addSlide(); s.background = { color: C.white };
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
  s = pres.addSlide(); s.background = { color: C.white };
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
  s = pres.addSlide(); s.background = { color: C.white };
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
  s = pres.addSlide(); s.background = { color: C.white };
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
  s = pres.addSlide(); s.background = { color: C.white };
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
  s = pres.addSlide(); s.background = { color: C.white };
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
  s = pres.addSlide(); s.background = { color: C.white };
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

  // ---------- 10. Close ----------
  s = pres.addSlide(); s.background = { color: C.navy };
  circ(s, I.shield, 0.6, 0.8, 1.0, C.teal);
  s.addText("Don't assume you can recover.", { x: 0.6, y: 2.2, w: 12, h: 0.9, fontFace: HF, fontSize: 44, bold: true, color: C.white, margin: 0, isTextBox: true });
  s.addText("Let the DR Agent validate it, and prove it, every week.", { x: 0.6, y: 3.1, w: 12, h: 0.8, fontFace: HF, fontSize: 30, color: "8FD6D3", margin: 0, isTextBox: true });
  [["Real DR tests", "Failover, validate, failback — executed by agents"], ["Built 100% on Azure", "AI Foundry, Site Recovery, Chaos Studio, Monitor"], ["Safe by design", "Graduated autonomy, human approval, full audit trail"]].forEach(([h, b], i) => {
    const x = 0.6 + i * 4.1;
    s.addText([{ text: h, options: { bold: true, fontSize: 17, color: C.white, breakLine: true } }, { text: b, options: { fontSize: 13, color: "B8C7DA" } }],
      { x, y: 4.6, w: 3.8, h: 1.0, fontFace: BF, valign: "top", margin: 0, isTextBox: true });
  });
  s.addText("Thank you  ·  Team Azure Armada", { x: 0.6, y: 6.5, w: 12.1, h: 0.45, fontFace: BF, fontSize: 15, color: "8FA3BD", margin: 0, isTextBox: true });

  await pres.writeFile({ fileName: "/home/ubuntu/agents/azure-armada/DR-Agent-Azure-Day-2026.pptx" });
  console.log("written");
})();
