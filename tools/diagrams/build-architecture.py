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

# ---------------- Simplified, presentation-grade layout ----------------
text("Disaster Recovery (DR) Agent: Autonomous Disaster Recovery Validation", 30, 22, 1200, 34, fs=26, color="#0B1F3A", bold=True)
text("Validates recoverability by executing real DR tests  ·  100% Azure  ·  Team Azure Armada  ·  Virtusa Azure Day 2026", 30, 58, 1200, 20, fs=14, color=MUTED)

# Users
container("Users", 30, 100, 170, 540, "#8C99AA", "#FFFFFF", rounded=1, fs=14)
icon("identity/Users.svg", 87, 150, 56, "SRE · DR Manager<br>· Auditor", fs=12)
bot = icon("ai_machine_learning/Bot_Services.svg", 87, 320, 56, "Microsoft Teams<br>(request · approve)", fs=12)
icon("identity/Azure_Active_Directory.svg", 87, 490, 56, "Microsoft Entra ID", fs=12)

# Legend
container("Legend", 30, 670, 170, 220, "#8C99AA", "#FFFFFF", rounded=1, fs=14)
for lbl, st, y in [("DR test execution", f"strokeColor={ORANGE};strokeWidth=3;", 715),
                   ("Control flow", f"strokeColor={BLUE};strokeWidth=2;", 755),
                   ("Replication", f"strokeColor={GREEN};strokeWidth=2;dashed=1;", 795),
                   ("Core agents", "", 835)]:
    if st:
        vertex("", 45, y + 8, 40, 1, "line;html=1;" + st)
    else:
        vertex("", 45, y, 40, 18, f"rounded=1;fillColor=#FFF4E5;strokeColor={ORANGE};strokeWidth=2;")
    text(lbl, 95, y, 100, 18, fs=12)

# Control plane
container("Azure Subscription · DR Agent Control Plane", 230, 100, 730, 790, BLUE, "#F5F9FE", sw=2, fs=15,
          ic="general/Subscriptions.svg", fc="#0B1F3A")
container("Triggers &amp; drill engine", 250, 145, 690, 95, "#7FA3C9", "#FFFFFF", dashed=True, fs=12, fc=MUTED)
logic = icon("integration/Logic_Apps.svg", 290, 177, 44, "Logic Apps<br>(schedule)", fs=12, right=True)
evg = icon("integration/Event_Grid_Topics.svg", 500, 177, 44, "Event Grid<br>(infra change)", fs=12, right=True)
func = icon("compute/Function_Apps.svg", 880, 177, 44)
text("Durable Functions<br>(DR drill engine)", 745, 182, 125, 40, fs=12, align="right")

vertex("Azure AI Foundry · Agent Service", 250, 260, 690, 445, f"rounded=1;arcSize=3;whiteSpace=wrap;html=1;fillColor=#F6F5FF;"
       f"strokeColor={PURPLE};strokeWidth=2;verticalAlign=bottom;align=left;spacingLeft=40;spacingBottom=8;fontSize=14;fontStyle=1;fontColor={PURPLE};")
icon("ai_machine_learning/AI_Foundry.svg", 258, 673, 26)
orch = vertex("<b style='font-size:15px'>Orchestrator Agent</b><br><span style='font-size:12px'>Plans the DR test · delegates · approval gates · kill switch</span>",
              270, 305, 650, 70, f"rounded=1;arcSize=10;whiteSpace=wrap;html=1;fillColor={PURPLE};strokeColor={PURPLE};"
              "fontColor=#FFFFFF;align=left;verticalAlign=middle;spacingLeft=70;shadow=1;")
icon("ai_machine_learning/Foundry_Agent_Service.svg", 285, 318, 44)

agents = [("Discovery", "Maps workload &amp; dependencies", False),
          ("Readiness", "Pre-test DR checks", False),
          ("Runbook", "RAG over DR runbooks", False),
          ("DR Test Executor", "Runs failover &amp; failback", True),
          ("Validation", "Measures real RTO / RPO", True),
          ("Remediation", "Fix PRs &amp; evidence pack", False)]
A = []
for i, (n, d, core) in enumerate(agents):
    x, y = 270 + (i % 3) * 220, 395 + (i // 3) * 140
    A.append(vertex(f"<b style='font-size:14px'>{n}</b><br><span style='font-size:12px'>{d}</span>", x, y, 210, 125,
                    f"rounded=1;arcSize=8;whiteSpace=wrap;html=1;verticalAlign=top;align=center;spacingTop=58;shadow=1;"
                    f"fillColor={'#FFF4E5' if core else '#FFFFFF'};strokeColor={ORANGE if core else PURPLE};"
                    f"strokeWidth={2.5 if core else 1.5};fontColor=#0B1F3A;"))
    icon("ai_machine_learning/Foundry_Agent_Service.svg", x + 85, y + 12, 40)

container("Shared services  ·  via Private Link", 250, 720, 690, 155, "#7FA3C9", "#FFFFFF", dashed=True, fs=12, fc=MUTED)
for k, (p, l) in enumerate([("ai_machine_learning/Azure_OpenAI.svg", "Azure OpenAI"),
                            ("app_services/Search_Services.svg", "AI Search"),
                            ("databases/Azure_Cosmos_DB.svg", "Cosmos DB"),
                            ("security/Key_Vaults.svg", "Key Vault"),
                            ("management_governance/Monitor.svg", "Azure Monitor")]):
    icon(p, 290 + k * 135, 760, 48, l, fs=12)

# Workload
container("Azure Subscription · Workload Under Test", 990, 100, 620, 560, BLUE, "#F5F9FE", sw=2, fs=15,
          ic="general/Subscriptions.svg", fc="#0B1F3A")
tool = container("DR test toolchain", 1010, 145, 580, 115, ORANGE, "#FFF8EE", sw=2, rounded=1, fs=12, fc=ORANGE)
for k, (p, l) in enumerate([("management_governance/Recovery_Services_Vaults.svg", "Site Recovery"),
                            ("other/Azure_Chaos_Studio.svg", "Chaos Studio"),
                            ("other/Azure_Load_Testing.svg", "Load Testing"),
                            ("networking/Front_Doors.svg", "Front Door")]):
    icon(p, 1065 + k * 140, 172, 44, l, fs=12)

ra = container("Region A · Primary", 1010, 300, 280, 340, "#6B7A8F", "#FFFFFF", dashed=True, fs=13)
rb = container("Region B · Secondary", 1310, 300, 280, 340, "#6B7A8F", "#FFFFFF", dashed=True, fs=13)
appA = icon("app_services/App_Services.svg", 1126, 340, 48, "App Service", fs=12)
sqlA = icon("databases/SQL_Database.svg", 1126, 445, 48, "Azure SQL (primary)", fs=12)
icon("compute/Virtual_Machine.svg", 1126, 550, 48, "VMs (ASR-protected)", fs=12)
appB = icon("app_services/App_Services.svg", 1426, 340, 48, "App Service (standby)", fs=12)
sqlB = icon("databases/SQL_Database.svg", 1426, 445, 48, "Azure SQL (geo-secondary)", fs=12)
icon("compute/Virtual_Machine.svg", 1426, 550, 48, "Isolated test VNet", fs=12)

# Outputs
outs = container("Outputs", 990, 690, 620, 200, "#8C99AA", "#FFFFFF", rounded=1, fs=14)
for k, (p, l) in enumerate([("devops/Azure_DevOps.svg", "Bicep fix PRs"),
                            ("power_platform/PowerBI.svg", "Resilience dashboard"),
                            ("storage/Storage_Accounts.svg", "DR evidence pack"),
                            ("ai_machine_learning/Bot_Services.svg", "Teams alerts")]):
    icon(p, 1050 + k * 145, 745, 52, l, fs=12)

# Edges
edge(bot, orch, E_CTRL + "startArrow=block;startFill=1;", exit=(1, 0.5), entry=(0, 0.5))
edge(logic, orch, E_CTRL, exit=(0.5, 1), entry=(0.1231, 0))
edge(evg, orch, E_CTRL, exit=(0.5, 1), entry=(0.4462, 0))
edge(orch, func, E_EXEC, exit=(0.9723, 0), entry=(0.5, 1))
edge(func, tool, E_EXEC, "", exit=(1, 0.5), entry=(0, 0.5))
edge(tool, ra, E_EXEC, "", exit=(0.24, 1), entry=(0.5, 0))
edge(tool, rb, E_EXEC, "", exit=(0.76, 1), entry=(0.5, 0))
edge(appA, appB, E_EXEC + "startArrow=block;startFill=1;", "failover / failback", exit=(1, 0.5), entry=(0, 0.5))
edge(sqlA, sqlB, E_REPL, "geo-replication", exit=(1, 0.5), entry=(0, 0.5))
edge(A[5], outs, E_CTRL, exit=(1, 0.5), entry=(0, 0.5), points=[(970, 597), (970, 790)])

xml = ('<mxfile host="drawio"><diagram name="DR Agent Architecture" id="arch">'
       '<mxGraphModel dx="1640" dy="920" grid="0" gridSize="10" guides="1" tooltips="1" connect="1" arrows="1" '
       'fold="1" page="1" pageScale="1" pageWidth="1640" pageHeight="920" math="0" shadow="0" background="#FFFFFF">'
       '<root><mxCell id="0"/><mxCell id="1" parent="0"/>' + "".join(cells) + "</root></mxGraphModel></diagram></mxfile>")
open("DR-Agent-Architecture.drawio", "w").write(xml)
print("cells:", len(cells))
