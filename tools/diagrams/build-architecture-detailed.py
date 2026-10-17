"""Generates DR-Agent-Architecture-detailed.drawio (Azure reference-architecture style)."""
from xml.sax.saxutils import escape

cells = []
_id = [100]


def nid():
    _id[0] += 1
    return f"c{_id[0]}"


def vertex(label, x, y, w, h, style):
    i = nid()
    cells.append(f'<mxCell id="{i}" value="{escape(label, {chr(34): "&quot;"})}" style="{style}" vertex="1" parent="1">'
                 f'<mxGeometry x="{x}" y="{y}" width="{w}" height="{h}" as="geometry"/></mxCell>')
    return i


def edge(src, dst, style, label="", points=None, exit=None, entry=None):
    i = nid()
    st = style
    if exit:
        st += f"exitX={exit[0]};exitY={exit[1]};exitDx=0;exitDy=0;"
    if entry:
        st += f"entryX={entry[0]};entryY={entry[1]};entryDx=0;entryDy=0;"
    pts = ""
    if points:
        pts = '<Array as="points">' + "".join(f'<mxPoint x="{px}" y="{py}"/>' for px, py in points) + "</Array>"
    cells.append(f'<mxCell id="{i}" value="{escape(label)}" style="{st}" edge="1" parent="1" source="{src}" target="{dst}">'
                 f'<mxGeometry relative="1" as="geometry">{pts}</mxGeometry></mxCell>')
    return i


AZ = "img/lib/azure2/"
INK, MUTED = "#1B2635", "#5B6B7F"
ORANGE, BLUE, PURPLE, GREEN = "#E8890C", "#0078D4", "#5B5FC7", "#3C8A2E"


def icon(path, x, y, size=48, label="", fs=11, right=False):
    pos = ("labelPosition=right;verticalLabelPosition=middle;align=left;verticalAlign=middle;spacingLeft=6;" if right
           else "verticalLabelPosition=bottom;verticalAlign=top;align=center;")
    st = (f"image;aspect=fixed;html=1;points=[];fontSize={fs};image={AZ}{path};{pos}"
          f"fontColor={INK};labelBackgroundColor=none;")
    return vertex(label, x, y, size, size, st)


def container(label, x, y, w, h, stroke, fill="none", dashed=False, sw=1.5, fs=13, ic=None, fc=INK, rounded=0):
    st = (f"rounded={rounded};arcSize=3;whiteSpace=wrap;html=1;fillColor={fill};strokeColor={stroke};strokeWidth={sw};"
          f"verticalAlign=top;align=left;spacingLeft={40 if ic else 10};spacingTop=4;fontSize={fs};fontStyle=1;"
          f"fontColor={fc};container=0;" + ("dashed=1;dashPattern=6 4;" if dashed else ""))
    c = vertex(label, x, y, w, h, st)
    if ic:
        icon(ic, x + 8, y + 5, 26)
    return c


def text(label, x, y, w, h, fs=11, color=INK, bold=False, align="left"):
    st = (f"text;html=1;whiteSpace=wrap;align={align};verticalAlign=top;fontSize={fs};fontColor={color};"
          f"fontStyle={1 if bold else 0};spacing=0;")
    return vertex(label, x, y, w, h, st)


def badge(n, x, y, color=ORANGE):
    vertex(str(n), x, y, 24, 24, f"ellipse;html=1;fillColor={color};strokeColor=#FFFFFF;strokeWidth=2;"
           "fontColor=#FFFFFF;fontStyle=1;fontSize=12;align=center;verticalAlign=middle;")


E_BASE = "edgeStyle=orthogonalEdgeStyle;rounded=1;html=1;jumpStyle=arc;jumpSize=10;fontSize=11;labelBackgroundColor=#FFFFFF;"
E_EXEC = E_BASE + f"strokeColor={ORANGE};strokeWidth=3;endArrow=block;endFill=1;fontColor={ORANGE};fontStyle=1;"
E_CTRL = E_BASE + f"strokeColor={BLUE};strokeWidth=2;endArrow=block;endFill=1;fontColor={BLUE};"
E_TELE = E_BASE + "strokeColor=#8C99AA;strokeWidth=1.5;dashed=1;endArrow=open;endFill=0;fontColor=#5B6B7F;"
E_REPL = E_BASE + f"strokeColor={GREEN};strokeWidth=2;dashed=1;endArrow=block;endFill=1;fontColor={GREEN};"

# ---------------- Title ----------------
text("Disaster Recovery (DR) Agent: Autonomous Disaster Recovery Validation", 30, 20, 1600, 34, fs=24, color="#0B1F3A", bold=True)
text("Reference architecture  ·  Team Azure Armada  ·  Virtusa Azure Day 2026 Hackathon  ·  100% Azure-native",
     30, 58, 1600, 22, fs=13, color=MUTED)

# ---------------- Users & channels ----------------
container("Users &amp; Channels", 30, 100, 230, 620, "#8C99AA", "#FFFFFF", rounded=1)
icon("identity/Users.svg", 115, 145, 52, "SRE · DR Manager · Auditor")
icon("identity/Azure_Active_Directory.svg", 117, 270, 48, "Microsoft Entra ID<br>(SSO · MFA · RBAC)")
bot = icon("ai_machine_learning/Bot_Services.svg", 117, 451, 48, "Microsoft Teams bot<br>(requests &amp; approvals)")
icon("app_services/App_Services.svg", 117, 600, 48, "DR Sentinel portal<br>(App Service)")

# ---------------- Legend ----------------
container("Flow legend", 30, 750, 230, 690, "#8C99AA", "#FFFFFF", rounded=1)
steps = [
    (1, "Trigger: schedule, infra change or on-demand request", BLUE),
    (2, "Discover workload topology &amp; dependencies", BLUE),
    (3, "Assess readiness, build test plan from runbooks (RAG)", BLUE),
    (4, "Human approval in Teams for non-isolated tests", BLUE),
    (5, "<b>Execute DR test</b>: ASR test failover, SQL failover group, Front Door switch, Chaos Studio faults", ORANGE),
    (6, "<b>Validate</b>: synthetic transactions, measure real RTO / RPO", ORANGE),
    (7, "<b>Fail back</b> to primary &amp; clean up", ORANGE),
    (8, "Remediate: Bicep fix PR, evidence pack, resilience score", BLUE),
]
yy = 790
for n, t, col in steps:
    badge(n, 42, yy, col)
    text(t, 74, yy + 2, 178, 48, fs=11)
    yy += 56 if len(t) < 60 else 70
for lbl, st, y in [("DR test execution", f"strokeColor={ORANGE};strokeWidth=3;", 1310),
                   ("Control / data flow", f"strokeColor={BLUE};strokeWidth=2;", 1338),
                   ("Replication", f"strokeColor={GREEN};strokeWidth=2;dashed=1;", 1366),
                   ("Telemetry / audit", "strokeColor=#8C99AA;strokeWidth=1.5;dashed=1;", 1394)]:
    vertex("", 42, y + 7, 50, 1, "line;html=1;" + st)
    text(lbl, 102, y, 150, 18, fs=11)

# ---------------- Control-plane subscription ----------------
container("Azure Subscription: DR Agent Control Plane  ·  Central India", 290, 100, 1200, 1340, BLUE, "#F5F9FE",
          sw=2, fs=14, ic="general/Subscriptions.svg", fc="#0B1F3A")
container("vnet-drsentinel-hub  ·  10.10.0.0/16", 310, 145, 1160, 890, GREEN, "#FFFFFF", sw=2,
          ic="networking/Virtual_Networks.svg", fc=GREEN)

# triggers / execution subnet
container("snet-integration  ·  triggers &amp; DR drill engine (VNet-integrated)", 330, 190, 1120, 140, "#7FA3C9",
          "#FAFCFF", dashed=True, fs=11, ic="networking/Subnet.svg", fc=MUTED)
logic = icon("integration/Logic_Apps.svg", 680, 235, 46, "Logic Apps<br>(weekly schedule)", right=True)
evg = icon("integration/Event_Grid_Topics.svg", 880, 235, 46, "Event Grid<br>(infra change)", right=True)
icon("management_governance/Automation_Accounts.svg", 1060, 235, 46, "Automation<br>(failover runbooks)", right=True)
func = icon("compute/Function_Apps.svg", 1240, 235, 46, "Durable Functions<br>(DR drill engine)", right=True)

# AI Foundry
container("Azure AI Foundry  ·  Agent Service  (project: dr-sentinel)", 330, 350, 1120, 450, PURPLE, "#F6F5FF",
          sw=2, ic="ai_machine_learning/AI_Foundry.svg", fc=PURPLE, rounded=1)
orch = vertex(
    "<b style='font-size:14px'>Orchestrator Agent</b><br>"
    "<span style='font-size:11px'>Plans the DR test, delegates to specialist agents, enforces guardrails, "
    "approval gates, kill switch &amp; auto-rollback</span>",
    350, 395, 1080, 80,
    f"rounded=1;arcSize=8;whiteSpace=wrap;html=1;fillColor={PURPLE};strokeColor={PURPLE};fontColor=#FFFFFF;"
    "align=left;verticalAlign=middle;spacingLeft=70;spacingRight=330;shadow=1;")
icon("ai_machine_learning/Foundry_Agent_Service.svg", 365, 410, 46)
for k, (p, l) in enumerate([("ai_machine_learning/Azure_OpenAI.svg", "Azure OpenAI<br>GPT-4.1 / o-series"),
                            ("ai_machine_learning/Content_Safety.svg", "Content Safety<br>Prompt Shields"),
                            ("databases/Azure_Cosmos_DB.svg", "Cosmos DB<br>state &amp; memory")]):
    ix = 1120 + k * 105
    icon(p, ix, 405, 30)
    text(l, ix - 38, 437, 106, 30, fs=9, color="#FFFFFF", align="center")

agents = [
    ("Discovery Agent", "Live dependency graph of the workload; criticality ranking",
     ["management_governance/Resource_Graph_Explorer.svg", "devops/Application_Insights.svg"], False),
    ("Readiness Agent", "Pre-test checks: replication, failover groups, secondary Key Vault, quota, NSGs",
     ["management_governance/Policy.svg", "other/Azure_Backup_Center.svg", "security/Security_Center.svg"], False),
    ("Runbook Agent", "RAG over DR runbooks; flags stale steps; builds executable test plan",
     ["app_services/Search_Services.svg", "ai_machine_learning/Form_Recognizers.svg"], False),
    ("DR Test Executor", "CORE · Runs failovers in dependency order, monitors, rolls back, fails back",
     ["management_governance/Recovery_Services_Vaults.svg", "other/Azure_Chaos_Studio.svg", "networking/Front_Doors.svg"], True),
    ("Validation Agent", "CORE · Synthetic transactions, data integrity, measures real RTO / RPO",
     ["other/Azure_Load_Testing.svg", "management_governance/Monitor.svg"], True),
    ("Remediation Agent", "Bicep fix PRs, evidence pack, resilience score, notifications",
     ["devops/Azure_DevOps.svg", "power_platform/PowerBI.svg"], False),
]
A = {}
for i, (name, desc, tools, core) in enumerate(agents):
    x, y, w, h = 350 + i * 182, 495, 170, 285
    st = (f"rounded=1;arcSize=6;whiteSpace=wrap;html=1;shadow=1;verticalAlign=top;align=center;fontSize=13;fontStyle=1;"
          f"spacingTop=62;fillColor={'#FFF4E5' if core else '#FFFFFF'};strokeColor={ORANGE if core else PURPLE};"
          f"strokeWidth={2.5 if core else 1.5};fontColor=#0B1F3A;")
    A[i] = vertex(name, x, y, w, h, st)
    icon("ai_machine_learning/Foundry_Agent_Service.svg", x + 63, y + 12, 44)
    text(desc, x + 10, y + 90, w - 20, 100, fs=11, color=INK, align="center")
    text("TOOLS", x, y + 200, w, 14, fs=9, color=MUTED, bold=True, align="center")
    n = len(tools)
    for t, tp in enumerate(tools):
        icon(tp, x + w / 2 - (n * 34 - 6) / 2 + t * 34, y + 220, 28)
    if core:
        vertex("CORE", x + w - 52, y + 8, 44, 18,
               f"rounded=1;arcSize=40;html=1;fillColor={ORANGE};strokeColor=none;fontColor=#FFFFFF;fontSize=9;fontStyle=1;")

# private endpoint subnet
container("snet-private-endpoints  ·  all PaaS reached over Private Link", 330, 860, 1120, 160, "#7FA3C9", "#FAFCFF",
          dashed=True, fs=11, ic="networking/Subnet.svg", fc=MUTED)
pe_items = [("databases/Azure_Cosmos_DB.svg", "Cosmos DB<br>agent state", 505),
            ("app_services/Search_Services.svg", "AI Search<br>runbook index", 779),
            ("storage/Storage_Accounts.svg", "Blob Storage<br>runbooks · evidence", 880),
            ("ai_machine_learning/Form_Recognizers.svg", "Document<br>Intelligence", 990),
            ("security/Key_Vaults.svg", "Key Vault<br>secrets", 1300)]
PE = {}
for p, l, x in pe_items:
    PE[l] = icon(p, x, 905, 40, l)
    vertex("", x + 26, 930, 18, 18, f"image;aspect=fixed;html=1;image={AZ}networking/Private_Endpoint.svg;")

# shared services row
container("Governance &amp; Discovery", 310, 1060, 380, 360, "#8C99AA", "#FFFFFF", rounded=1)
gov = vertex("", 310, 1060, 380, 360, "rounded=1;fillColor=none;strokeColor=none;")
for k, (p, l) in enumerate([("management_governance/Resource_Graph_Explorer.svg", "Resource Graph"),
                            ("management_governance/Policy.svg", "Azure Policy"),
                            ("other/Azure_Backup_Center.svg", "Business Continuity<br>Center"),
                            ("security/Security_Center.svg", "Defender for Cloud")]):
    icon(p, 380 + (k % 2) * 190, 1130 + (k // 2) * 140, 48, l)

container("Identity &amp; Security", 710, 1060, 370, 360, "#8C99AA", "#FFFFFF", rounded=1)
for k, (p, l) in enumerate([("identity/Managed_Identities.svg", "Managed Identity<br>per agent"),
                            ("identity/Entra_Privileged_Identity_Management.svg", "Entra PIM<br>(just-in-time)"),
                            ("security/Key_Vaults.svg", "Key Vault<br>(no secrets in prompts)"),
                            ("ai_machine_learning/Content_Safety.svg", "AI Content Safety")]):
    icon(p, 780 + (k % 2) * 180, 1130 + (k // 2) * 140, 48, l)

container("Observability &amp; Audit", 1100, 1060, 370, 360, "#8C99AA", "#FFFFFF", rounded=1)
obs = vertex("", 1100, 1060, 370, 360, "rounded=1;fillColor=none;strokeColor=none;")
for k, (p, l) in enumerate([("management_governance/Monitor.svg", "Azure Monitor"),
                            ("management_governance/Log_Analytics_Workspaces.svg", "Log Analytics<br>(RTO/RPO KQL)"),
                            ("devops/Application_Insights.svg", "App Insights<br>(agent tracing)"),
                            ("storage/Storage_Accounts.svg", "Immutable audit<br>log (WORM)")]):
    icon(p, 1170 + (k % 2) * 180, 1130 + (k // 2) * 140, 48, l)

# ---------------- Workload subscription ----------------
container("Azure Subscription: Workload Under Test  (e.g. Payments app)", 1560, 100, 1010, 830, BLUE, "#F5F9FE",
          sw=2, fs=14, ic="general/Subscriptions.svg", fc="#0B1F3A")
tool = container("DR test toolchain", 1580, 150, 620, 170, ORANGE, "#FFF8EE", sw=2, rounded=1, fc=ORANGE)
for k, (p, l) in enumerate([("management_governance/Recovery_Services_Vaults.svg", "Site Recovery<br>(test failover)"),
                            ("databases/SQL_Database.svg", "SQL failover<br>group API"),
                            ("other/Azure_Chaos_Studio.svg", "Chaos Studio<br>(fault injection)"),
                            ("other/Azure_Load_Testing.svg", "Azure Load Testing<br>(synthetic journeys)")]):
    icon(p, 1640 + k * 145, 190, 46, l)
fd = icon("networking/Front_Doors.svg", 2370, 175, 52, "Azure Front Door<br>(global traffic switch)")

# Region A
ra = container("Region A  ·  Central India  (Primary)", 1580, 370, 450, 530, "#6B7A8F", "#FFFFFF", dashed=True)
container("vnet-app-primary", 1595, 405, 420, 480, GREEN, "none", sw=2, fs=11, ic="networking/Virtual_Networks.svg", fc=GREEN)
container("snet-web", 1610, 445, 390, 100, "#7FA3C9", "#FAFCFF", dashed=True, fs=10, fc=MUTED)
appA = icon("app_services/App_Services.svg", 1785, 465, 40, "App Service (active)", fs=10)
container("snet-data", 1610, 565, 390, 130, "#7FA3C9", "#FAFCFF", dashed=True, fs=10, fc=MUTED)
sqlA = icon("databases/SQL_Database.svg", 1650, 595, 40, "SQL DB<br>(FOG primary)", fs=10)
icon("storage/Storage_Accounts.svg", 1785, 595, 40, "Storage<br>(GZRS)", fs=10)
icon("security/Key_Vaults.svg", 1920, 595, 40, "Key Vault", fs=10)
container("snet-compute", 1610, 715, 390, 155, "#7FA3C9", "#FAFCFF", dashed=True, fs=10, fc=MUTED)
vmA = icon("compute/Virtual_Machine.svg", 1785, 755, 44, "VMs<br>(ASR-protected)", fs=10)

# Region B
rb = container("Region B  ·  South India  (Secondary)", 2110, 370, 450, 530, "#6B7A8F", "#FFFFFF", dashed=True)
container("vnet-app-secondary", 2125, 405, 420, 290, GREEN, "none", sw=2, fs=11, ic="networking/Virtual_Networks.svg", fc=GREEN)
container("snet-web", 2140, 445, 390, 100, "#7FA3C9", "#FAFCFF", dashed=True, fs=10, fc=MUTED)
appB = icon("app_services/App_Services.svg", 2315, 465, 40, "App Service (standby)", fs=10)
container("snet-data", 2140, 555, 390, 130, "#7FA3C9", "#FAFCFF", dashed=True, fs=10, fc=MUTED)
sqlB = icon("databases/SQL_Database.svg", 2180, 585, 40, "SQL DB<br>(geo-secondary)", fs=10)
icon("storage/Storage_Accounts.svg", 2315, 585, 40, "Storage<br>(secondary)", fs=10)
icon("security/Key_Vaults.svg", 2450, 585, 40, "Key Vault", fs=10)
container("vnet-dr-test  ·  isolated, no peering", 2125, 710, 420, 175, ORANGE, "#FFFBF5", dashed=True, sw=2, fs=11,
          ic="networking/Virtual_Networks.svg", fc=ORANGE)
vmB = icon("compute/Virtual_Machine.svg", 2315, 765, 44, "Test-failover VMs<br>(zero user impact)", fs=10)

# Outputs
container("Delivery, Reporting &amp; Notifications", 1560, 960, 1010, 460, "#8C99AA", "#FFFFFF", rounded=1)
outs = vertex("", 1560, 960, 1010, 460, "rounded=1;fillColor=none;strokeColor=none;")
for k, (p, l) in enumerate([("devops/Azure_DevOps.svg", "Azure DevOps / Repos<br>Bicep fix pull requests"),
                            ("power_platform/PowerBI.svg", "Power BI<br>resilience dashboard"),
                            ("storage/Storage_Accounts.svg", "DR test evidence pack<br>(audit-ready PDF)"),
                            ("ai_machine_learning/Bot_Services.svg", "Teams notifications<br>&amp; approvals"),
                            ("other/Azure_Communication_Services.svg", "Communication Services<br>(email to owners)")]):
    icon(p, 1640 + k * 190, 1140, 52, l)

# ---------------- Edges ----------------
edge(bot, orch, E_CTRL + "startArrow=block;startFill=1;", "", exit=(1, 0.5), entry=(0, 0.5))
badge(1, 268, 446, BLUE); badge(4, 296, 446, BLUE)
edge(logic, orch, E_CTRL, exit=(0.5, 1), entry=(0.3268, 0))
edge(evg, orch, E_CTRL, exit=(0.5, 1), entry=(0.5120, 0))
badge(1, 712, 296, BLUE)
edge(orch, func, E_EXEC, exit=(0.84, 0), entry=(0.5, 1))
edge(func, tool, E_EXEC, exit=(0.5, 0), entry=(0, 0.35), points=[(1263, 175), (1520, 175), (1520, 209)])
badge(5, 1380, 163)
edge(A[0], gov, E_CTRL, exit=(0.5, 1), entry=(0.33, 0))
edge(A[1], gov, E_CTRL, exit=(0.5, 1), entry=(0.81, 0))
badge(2, 422, 1030, BLUE); badge(3, 604, 1030, BLUE)
edge(A[2], PE["AI Search<br>runbook index"], E_CTRL, exit=(0.5, 1), entry=(0.5, 0))
badge(3, 806, 820, BLUE)
edge(A[4], obs, E_EXEC, exit=(0.5, 1), entry=(0.17, 0))
badge(6, 1150, 1030)
edge(A[5], outs, E_CTRL, exit=(1, 0.3), entry=(0, 0.55), points=[(1530, 580), (1530, 1213)])
badge(8, 1518, 1235, BLUE)
# toolchain -> regions
edge(tool, ra, E_EXEC, "failover", exit=(0.35, 1), entry=(0.5, 0))
edge(tool, rb, E_EXEC, "test / validate", exit=(0.92, 1), entry=(0.2, 0))
edge(fd, rb, E_EXEC, "switch", exit=(1, 0.5), entry=(0.95, 0), points=[(2535, 201)])
edge(fd, ra, E_TELE, "", exit=(0, 0.5), entry=(0.85, 0), points=[(2240, 201), (2240, 345), (1962, 345)])
# region to region
edge(appA, appB, E_EXEC + "startArrow=block;startFill=1;", exit=(1, 0.5), entry=(0, 0.5))
badge(5, 2058, 455); badge(7, 2058, 500)
edge(sqlA, sqlB, E_REPL, "geo-replication", exit=(0, 0.5), entry=(0, 0.5), points=[(1628, 615), (1628, 684), (2160, 684), (2160, 605)])
edge(vmA, vmB, E_REPL, "ASR replication", exit=(1, 0.5), entry=(0, 0.5))
# telemetry
edge(ra, obs, E_TELE, "diagnostics", exit=(0, 0.95), entry=(1, 0.25), points=[(1505, 873), (1505, 1150)])

xml = ('<mxfile host="drawio"><diagram name="DR Agent Architecture" id="arch">'
       '<mxGraphModel dx="2600" dy="1500" grid="0" gridSize="10" guides="1" tooltips="1" connect="1" arrows="1" '
       'fold="1" page="1" pageScale="1" pageWidth="2620" pageHeight="1480" math="0" shadow="0" background="#FFFFFF">'
       '<root><mxCell id="0"/><mxCell id="1" parent="0"/>' + "".join(cells) + "</root></mxGraphModel></diagram></mxfile>")
open("DR-Agent-Architecture-detailed.drawio", "w").write(xml)
print("cells:", len(cells))
