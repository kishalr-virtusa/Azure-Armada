# Live demo experience: judges' phones + presenter view (spec for Kiro)

Goal: during the pitch, judges scan a QR code, open **Contoso Payments on their own phones** via our public
URL (Traffic Manager / custom domain), make payments, and then *feel* the outage and the recovery live:
banner turns red + vibration, a live stopwatch and "estimated recovery" countdown, then green "recovered in
Southeast Asia, RTO 34 s (target 30 min), RPO 0 s" + success vibration. The presenter screen shows the same
thing next to the agent timeline.

Owner split: **Kiro** = all UI below (tasks L1–L3). **Claude** = backend contract (`/public/live`, `/api/config`,
chaos/validator, custom domain IaC). Do NOT change engine/agent Python under `apps/dr-console/dr_agent/`.
Follow `.kiro/steering/ui-style.md` (Virtusa tokens). Region A = `--accent` purple, Region B = `--mag` magenta.

---------------------------------------------------------------------------------------------------------
## Contract (provided by Claude; build against it, mock it locally if needed)

### C1. Console public live feed: `GET {CONSOLE}/public/live` (no auth, CORS `*`, poll every 1 s)
```json
{
  "state": "normal | armed | outage | recovered | failing_back | completed | paused | rolled_back",
  "exercise_id": "ex-20261017-140501 | null",
  "headline": "Central India is down. DR Agent is failing over to Southeast Asia.",
  "primary_region": "Central India",
  "serving_region": "Southeast Asia | Central India | null",
  "server_time": "2026-10-17T14:05:31.120Z",
  "t0": "ISO | null",                       // outage start
  "elapsed_s": 12.4,                         // since t0; frozen once recovered; null when no outage
  "eta_s": 21.0,                             // estimated seconds until recovery; null when unknown / not in outage
  "eta_basis": "median of last 3 drills",
  "rto_target_s": 1800, "rpo_target_s": 300,
  "rto_s": null, "rpo_s": null, "rto_met": null, "rpo_met": null,
  "current_step": "Forced SQL failover to Region B",
  "steps_done": 6, "steps_total": 18,
  "chaos": false,                            // a surprise second fault was injected
  "timeline": [{"ts": "ISO", "kind": "info|measurement|diagnosis|approval|error", "message": "..."}]  // last 8
}
```
Use `server_time` to correct the phone clock skew: `skew = Date.now() - Date.parse(server_time)`; the local
stopwatch then ticks smoothly every 100 ms between polls: `elapsed = (Date.now() - skew - Date.parse(t0))/1000`
(stop ticking when `rto_s` is set; show `rto_s`).

### C2. Demo app config: `GET /api/config` (demo app, same origin)
```json
{"region": "Central India", "live_url": "https://azure-armada.azurewebsites.net/public/live",
 "public_url": "https://azure-armada.azurewebsites.net/pay", "alt_url": "https://azure-armada.azurewebsites.net/pay"}
```
`public_url`/`alt_url` may be empty strings (no custom domain yet).

---------------------------------------------------------------------------------------------------------
## L1 [Kiro] Contoso Payments: mobile-first live app (`apps/demo-app/app/templates/index.html`, `static/`)

Keep: `/health`, `/api/txn`, `/api/recent` contracts and the G1 config-gap behaviour in `main.py`.
Add only `/api/config` to `main.py` (reads env `DR_LIVE_URL`, `PUBLIC_URL`, `ALT_PUBLIC_URL`, `REGION_NAME`).

Layout (single page, phone first, looks like a real fintech app):
- Top: Contoso Payments brand + **region chip** ("Served from Central India" purple / "Southeast Asia" magenta).
- Balance card, big **Pay** button (random amount, like today), list of recent payments with the region each
  was written in (shows that data written before the outage is still there after failover = RPO visible).
- **Live DR status card** driven by C1:
  - `normal`: subtle green "All systems normal · DR drills verified by DR Agent".
  - `outage` (t0 set, rto_s null): red banner "Service disruption in Central India", big **stopwatch**
    (mm:ss.t), **ETA countdown** "Estimated recovery in ~21 s" (+ `eta_basis` small text; if elapsed > ETA show
    "Taking longer than usual…"), progress bar `steps_done/steps_total`, current step text, last 3 timeline lines.
    Pay button disabled with "Payments paused while we fail over".
  - `recovered`: green "Recovered in Southeast Asia", **RTO 34 s vs target 30 min ✅**, **RPO 0 s vs 5 min ✅**,
    confetti-free subtle animation; Pay enabled again.
  - `failing_back` / `completed`: info banner "Returning to Central India" / "Drill complete".
  - `chaos: true`: amber line "A second fault was injected; the Validator agent is handling it".
- Reconnect logic (important, DNS-based failover on phones): poll own-origin `GET /health?ts=…` with
  `cache:'no-store'` every 2 s. If C1 says `recovered` but own-origin health keeps failing or reports the old
  region for > 8 s, navigate to `alt_url` (if set, keep the same path) else `location.reload()`.
  Show "Reconnecting to the recovered region…" meanwhile.
- If `live_url` is unreachable, the app still works normally (no live card errors shown to users).

Haptics (Android + iOS), in `static/haptics.js`, one tiny module, no dependencies:
- Android/Chrome: `navigator.vibrate(pattern)` when available.
- iOS Safari (17.4+/18+) has no Vibration API: use the hidden `<input type="checkbox" switch>` + `<label>` click
  trick (toggling an iOS switch control emits a system haptic). It only fires inside a user gesture.
- Patterns: `tap` (10 ms) on every button press; `success` ([20,40,20]) on payment success and on recovery;
  `error` ([60,40,60,40,60]) on payment failure; `alert` ([200,100,200,100,400]) when the outage starts.
- Outage/recovery happen without a user gesture: Android vibrates; on iOS fall back to a screen flash + a short
  WebAudio beep. First visit shows a one-tap "Enable live alerts" button (unlocks AudioContext, does a test
  haptic). Respect `prefers-reduced-motion` (no flash/pulse animations) and keep an in-app mute toggle.
- Feature-detect everything; never throw on unsupported browsers.

Responsive / device requirements:
- `<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">`,
  `theme-color`, safe-area insets (`env(safe-area-inset-*)`), no horizontal scroll at 320 px,
  tap targets ≥ 44 px, works 320–430 px phones, tablets, desktop (center a max-width 480 px column on desktop).
- Light + dark mode (prefers-color-scheme), Montserrat, Virtusa tokens.
- Add `manifest.webmanifest` (name "Contoso Payments", standalone, theme color) so it can be "Added to Home Screen".
- Test in Chrome devtools device mode: iPhone SE, iPhone 15 Pro Max, Pixel 7, Galaxy Fold (folded), iPad.

## L2 [Kiro] Console presenter view: `GET /live` page (`apps/dr-console/web/templates/live.html`)
- Auth required like other pages; add "Live" to the nav. Add the route in `web/main.py` (page only).
- Left: **QR code** of `public_url` (from `GET /api/live-config` → `{"public_url": ..., "app_url": ...}`, Claude
  provides) with the URL in text below ("Scan to open Contoso Payments"). Use `qrcode-generator@1.4.4` from
  cdnjs. Under it, the same stopwatch / ETA / RTO / RPO tiles as L1 (from `/public/live`) in large type.
- Right: a **phone frame** (CSS device bezel, 390×844 scaled to fit) with an `<iframe>` of `app_url`, so the
  audience sees the user's view flip from purple Central India to magenta Southeast Asia.
- Bottom: the agent timeline (last 8 events) + "🔥 Inject surprise fault" button (POST
  `/api/exercise/{xid}/chaos`, confirm dialog; only enabled while state is `outage`).
- Full-screen friendly at 1920×1080 and readable from the back of the room.

## L3 [Kiro] Exercise page: chaos + remediation
- Plan approval: checkbox "Inject a surprise fault during the drill (tests the Validator agent)" → send
  `{"approver_name": ..., "chaos": true}` to `/api/plan/approve`.
- Exercise page: same "🔥 Inject surprise fault" button (as L2).
- Failure panel (status `paused_failed`): the diagnosis now has `fix_action` + `fix_params` (list of
  `{key,value}`); when `fix_action != "none"` show "Proposed fix: Enable Traffic Manager endpoint for Region B"
  and the primary button **"Approve fix & retry"** (POST `/api/exercise/{xid}/retry`). Show the diagnosis badge
  "🤖 Azure AI Foundry Agent" when `diagnosis_via == "foundry-agent"`.

Done = all three pages work at 320 px and 1920 px, no console errors, commit to `dev` with `git pull --rebase` first.

## L4 [Kiro] Virtusa + Azure branding everywhere (logos in `static/brand/`)
Assets (official, already in both apps and `docs/design/brand/`): `virtusa.svg` (purple wordmark),
`virtusa-white.svg` (for dark backgrounds / dark mode), `azure.svg` (Microsoft Azure "A" logo).
Rules: never distort, keep clear space ≥ logo height/2, Virtusa wordmark height 20–28 px in headers,
Azure logo 18–24 px next to the text "Microsoft Azure". Use the white Virtusa logo in dark mode.
- DR Console (all pages via `base.html`): header left = Virtusa logo + divider + "DR Agent" product name;
  header right chip = Azure logo + "Built on Microsoft Azure · AI Foundry". Footer: "© Virtusa · Team Azure
  Armada · Azure Day 2026". Login page: large centred Virtusa logo, Azure badge below, subtle watermark.
- Presenter view `/live`: Virtusa logo top-left, Azure logo + "Microsoft Azure" top-right, very faint
  (opacity .04) large Virtusa wordmark watermark in the background; QR caption "Team Azure Armada".
- Contoso Payments (customer app, keeps its own Contoso identity): small footer "DR protected by
  [Virtusa logo] DR Agent · Powered by [Azure logo] Microsoft Azure"; the live DR status card shows
  "Virtusa DR Agent" as the actor ("Virtusa DR Agent is failing over…"). No watermark over the payment UI.
- Report HTML (`dr_agent/engine/reporting.py` template only, you may edit the HTML/CSS string there): Virtusa
  logo in the header, Azure badge, "Confidential · Generated by Virtusa DR Agent on Microsoft Azure" footer,
  faint diagonal "Virtusa" watermark when printed. Embed logos inline (the report is a standalone blob).
- Favicon: an SVG favicon in Virtusa purple for the console; Contoso keeps its own.

## L5 [Kiro] Email status in the console
- Notification center: each notification row now may have `email_sent` (bool), `email_channel`
  ("resend" | "azure-monitor" | "acs"), `email_ms`, `email_errors`. Show a small chip "✉ sent via Resend · 2.1 s"
  / "✉ sent via Azure Monitor" / "✉ failed" (tooltip = email_errors). No chip when the fields are absent.
- Settings/Overview: a **"Send test email"** button → `POST /api/notifications/test-email` (takes up to ~20 s,
  show a spinner) → show result: channel, duration, recipient, or the errors.

## L6 [Kiro] Instant alerts in the console (primary notification channel for the demo)
- `base.html`: poll `GET /api/notifications` every 3 s (already exists). For each NEW notification:
  - in-page toast (top-right, Virtusa status colours by kind, auto-hide 8 s, click → `link`), plus a short
    WebAudio chime (distinct tones for action / success / failure); mute toggle in the header.
  - **Desktop notification** via the browser Notification API (`new Notification(title, {body, icon:
    '/static/brand/virtusa.svg', tag: id, requireInteraction: kind === 'action'})`, click focuses the tab and
    opens `link`). Ask permission from an explicit "Enable desktop alerts" button (browsers require a gesture).
  - Update the bell badge count and the page `<title>` ("(2) DR Agent").
- Kinds `action` (approval needed) must be the most prominent: amber, stays until clicked.
- Feature-detect; never throw if Notification/WebAudio are unavailable.

## L7 [Kiro] Virtual-presentation adjustments (we will most likely present remotely over a video call)
- Presenter view `/live`: QR code at least 280 px with a white quiet zone, and the short URL
  `azure-armada.azurewebsites.net/pay` in ≥ 28 px text under it, plus a "Copy link" button (for the meeting chat).
- Phone frame: when the embedded app vibrates (outage alert, payment, recovery), show it VISUALLY: the
  frame shakes briefly (CSS keyframes, ~400 ms) with ripple rings + a small "📳 haptic" label, so remote viewers
  "see" the haptics. Implement via `postMessage` from the demo app (`{type:'haptic', pattern}`) → parent page.
  (Demo app: in `haptics.js`, also `window.parent.postMessage(...)` when running inside an iframe.)
- Everything must stay legible in a 1280×720 compressed screen share: min body text 18 px on `/live`,
  high-contrast tiles, avoid thin fonts and tiny grey text.

## L8 [Kiro] Private / Public switch for Contoso Payments (security feature, show it in the demo)
Backend exists: `GET /api/access` → `{public, public_until, changed_by, changed_at}`;
`POST /api/access {"public": true|false, "minutes": 120, "by": "<name>"}`; `GET /api/live-config` now also returns
`team_link` (private link with the access code) and `access`.
- Console header (all pages): a status pill **🔒 Private** (grey) / **🌐 Public · until 14:30** (green, countdown).
  Click → modal: toggle, duration select (30 / 60 / 120 min), name field (prefill last approver), confirm.
  Making it public needs an explicit confirm ("Anyone with the link can open Contoso Payments until …").
- Presenter view `/live`: the same toggle next to the QR code. While PRIVATE, the QR shows the **team link**
  (with code) and a label "Team only"; while PUBLIC it shows the public URL and "Open to everyone".
  "Copy team link" button for the team chat.
- Notifications show the audit entries ("Contoso Payments is now PUBLIC until … — changed by …").
- Contoso app: the gate page lives in `apps/demo-app/app/access.py` (GATE_HTML); you may restyle it to match L1,
  keep the form fields (`code`, `next`) and POST `/access`.

## L9 [Kiro] IaaS tier (Azure Site Recovery) in the UI
Backend: the drill now has 3 extra non-blocking steps (owner team `infra`): `asr_test_failover` (iaas-1),
`asr_verify_recovery` (iaas-2), `asr_test_cleanup` (iaas-3). `/public/live` has `"iaas": {"state":
"idle|recovering|recovered", "recovered_s": 412.0}`. Readiness has a new check "Azure Site Recovery (reports VM)".
The report template receives `iaas` = {tier, recovered_in, result{test_vm, location, reported_region}, status}.
- Presenter view `/live` + exercise page: a second, smaller tile "IaaS · Reports VM (Azure Site Recovery)":
  idle → "Test failover in progress…" (spinner) → "Recovered in 6m52s · Southeast Asia ✅". Label it clearly as a
  *test* failover in an isolated network (non-disruptive).
- Report (`reporting.py` TEMPLATE): add an "IaaS tier" row/card with the same data.
- Team badge colour for `infra` (use `--mag` family).

## L10 [Kiro] Clean "Not deployed yet" state in the console
`/api/status` entries (`region_a`, `region_b`, `traffic_manager`, `routing`) can now be
`{"state": "NotDeployed", "message": "Region A web app is not deployed yet", "detail": "<raw error>"}`.
Show a neutral grey badge "Not deployed yet" with `message` (tooltip = `detail`) instead of a red error.
Never print raw Azure exception text in the UI; put it in a tooltip or a collapsed "details" row.

## NOTE (17 Oct, evening): Region A is now **East Asia** (Hong Kong), not Central India
East Asia ↔ Southeast Asia is an official Azure paired region. Resource names use `eas`
(`dra-app-eas-<sfx>`, `dra-sql-eas-<sfx>`). Replace any remaining "Central India" text in templates/JS
with data from the API (`region` fields) or "East Asia". The console still runs in South India.
