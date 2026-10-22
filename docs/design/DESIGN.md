---
version: 1
name: virtusa-dr-agent
description: "Engineered, calm, precise. The DR console follows Vercel's product discipline (near-white canvas, ink text,
  1px hairline rings, 4px grid, weight ceiling 600, mono for technical labels) with Virtusa purple as the single
  chromatic accent. Contoso Payments (the customer app) follows Mercury's fintech calm (soft canvas, one confident
  action pill, rounded cards, plain-language copy). Derived from VoltAgent/awesome-design-md (vercel, mercury)."

colors:              # console + Contoso share these tokens
  canvas: "#ffffff"
  canvas-soft: "#fafafa"
  canvas-soft-2: "#f4f4f5"
  ink: "#171717"          # text + primary buttons (black is the action colour in the console)
  body: "#4d4d4d"
  mute: "#8a8a8a"
  hairline: "#ebebeb"
  hairline-strong: "#d4d4d4"
  accent: "#5e2cfe"       # Virtusa purple: the ONLY chromatic accent (active nav, focus, links, live/progress)
  accent-soft: "#efe9ff"
  region-a: "#5e2cfe"     # East Asia (primary) identity token
  region-b: "#c105b2"     # Southeast Asia (DR) identity token
  success: "#0f7b3f"
  success-soft: "#e3f5ea"
  warning: "#a15c07"
  warning-soft: "#fff3d6"
  danger: "#d0021b"
  danger-soft: "#fde8ea"
  dark: { canvas: "#0a0a0a", canvas-soft: "#111111", canvas-soft-2: "#1a1a1a", ink: "#ededed", body: "#a1a1a1",
          mute: "#737373", hairline: "#262626", hairline-strong: "#333333", accent: "#8b6bff", accent-soft: "#211a3d" }

typography:
  sans: "Geist, Inter, system-ui, sans-serif"   # weights 400 / 500 / 600 only (600 is the ceiling)
  mono: "Geist Mono, JetBrains Mono, ui-monospace, monospace"   # 12-13px technical labels only
  scale:            # size / line-height / tracking
    page-title: 24 / 32 / -0.96px   (600)
    section:    18 / 28 / -0.4px    (600)
    card-title: 14 / 20 / -0.28px   (600)
    body:       14 / 20 / -0.1px    (400)   # console default
    small:      13 / 18 / 0         (400)
    caption:    12 / 16 / 0         (400/500)
    mono-label: 12 / 16 / 0         (mono 400)   # resource names, step ids, timestamps, eyebrows
    metric:     28 / 32 / -1px      (600, tabular-nums)
    metric-xl:  56 / 60 / -2.4px    (600, tabular-nums)   # presenter view only

spacing: 4px base. Tokens 4 8 12 16 24 32 48 64. Card padding 16-24. Gaps between cards 16. Page gutter 24 (16 mobile).
radius: 6 (buttons, inputs, pills-in-tables), 8 (cards), 12 (large panels), 999 (status pills, region chips).
elevation:
  ring: "0 0 0 1px rgba(0,0,0,.08)"                        # default card chrome (no heavy borders)
  raised: "0 0 0 1px rgba(0,0,0,.08), 0 1px 2px rgba(0,0,0,.04), 0 4px 8px -4px rgba(0,0,0,.06)"
  overlay: "0 0 0 1px rgba(0,0,0,.08), 0 8px 16px -4px rgba(0,0,0,.08), 0 24px 32px -8px rgba(0,0,0,.10)"
---

# DR Agent design system

## Layout (console)
- **App shell**: fixed left sidebar 240px (Virtusa wordmark + "DR Agent" product label, nav with 16px line icons,
  bottom: environment card "East Asia → Southeast Asia", "Built on Microsoft Azure" mark, sign out). Content area:
  page header (eyebrow in mono-label, page-title, one-line description, status pill / primary action on the right)
  then content on a 12-column grid, max-width 1200px, gutter 24px.
- Below 960px the sidebar becomes a 56px top bar with a horizontally scrolling nav row. No horizontal page scroll.
- Large gaps between sections (32px), tight inside cards (8-12px). Never centre-align body content.

## Components
- **Card**: canvas background, radius 8, `ring` elevation, padding 20. Header row = card-title + optional caption
  on the right. No coloured top borders; state is shown with a status pill, not by tinting the whole card.
- **Metric tile**: caption label (mute), metric value (tabular), one small context line. Max 4 per row.
- **Status pill**: radius 999, 12px/500, 2px 8px padding, soft background + dark text of the semantic colour,
  6px dot before the label. Labels in sentence case ("Running", "Not deployed", "Checking"), never ALL CAPS.
- **Region tag**: mono-label in region colour with a 6px square swatch ("A · East Asia").
- **Buttons**: primary = ink background, white text, radius 6, height 36, 14/500. Secondary = canvas + ring.
  Danger = danger background (kill switch only). Ghost = text only. One primary per view.
- **Tables**: 13px rows, 40px row height, header 12/500 mute (sentence case, no uppercase), hairline row dividers,
  technical columns (ids, actions, timestamps) in mono-label, numbers right-aligned tabular.
- **Timeline**: 8px dot aligned to the first text line (top 6px), event text 14/500, meta line in mono-label.
- **Forms**: inputs 36px, radius 6, ring border, label 13/500 above.
- **Scrollbars**: 6px, thumb accent at 35% opacity, transparent track. Only scroll containers that need it.

## Presenter view
Same tokens, larger scale for screen sharing: metric-xl for the stopwatch, metric 40px for RTO/RPO, section 20px.
Idle state shows only QR + status; drill state collapses the QR to a link row.

## Contoso Payments (customer app, Mercury-inspired)
- Canvas `#fbfcfd`, cards white radius 16 with `ring`, one action pill (radius 999, accent, 48px) per screen.
- Balance card: deep ink-violet `#171721` surface, white numerals 40/600 tabular. No rainbow gradients.
- Copy is for customers: no infrastructure terms (no RTO, RPO, Traffic Manager, region codes). Facts in plain words:
  "Service restored in 32 seconds", "None of your payments were lost".
- Status card: one calm sentence + a 4-step checklist + a single small line of timing.

## Do
- Use weight 600 for headings, 400/500 for everything else. Negative tracking on headings.
- Put technical identifiers (resource names, step ids, timestamps) in the mono face.
- Show state with pills and small dots; keep surfaces neutral.
- Keep one accent colour; use region-a / region-b only to label regions.

## Don't
- No emoji as icons in the console. No ALL-CAPS labels except the mono eyebrow.
- No coloured top borders on cards, no gradient buttons, no heavy drop shadows, no watermarks.
- No more than one primary button per view. No centred paragraphs in the console.
- No font weight 700+.
