---
inclusion: always
---

# UI style (all frontends: demo app, DR console, HTML report)

Follow the Virtusa reference styling in `docs/design/virtusa-reference.css`. Copy its design tokens and component patterns. Do not invent a new look.

- **Font:** Montserrat from Google Fonts (weights 300/400/500/600/700); fallback `system-ui, "Segoe UI", sans-serif`.
- **Tokens:** use the `:root` CSS variables exactly: `--bg --surface --surface-2 --ink --ink-2 --ink-3 --line --line-2 --accent(#5E2CFE Virtusa purple) --mag(#C105B2)`, plus the status pairs `--have/--have-bg` (green = pass/healthy), `--upd/--upd-bg` (amber = warn/in progress), `--add/--add-bg` (red = fail/outage), `--nr/--nr-bg` (neutral), `--oth/--oth-bg` (blue = info).
- **Dark mode:** include the same `prefers-color-scheme` dark tokens and the `[data-theme]` overrides from the reference.
- **Components to reuse:**
  - `.wrap` (max 1300px)
  - `.eyebrow` (small uppercase accent label)
  - light-weight `h1` (300)
  - `.card` (10px radius, 1px line border)
  - `.kpi` (top 3px accent border, big 30px light number), used for RTO/RPO and region status tiles
  - `.chip` (pills)
  - `.st` / `.pr` status tags
  - `.badge`
  - tables styled as in the reference (uppercase 10px headers)
  - `.box`
  - `details/summary` for evidence expanders
- **Accents:** Region A = `--accent` (purple), Region B = `--mag` (magenta). Outage banner = `--add-bg`/`--add`.
- **Layout:** cards, generous whitespace, no heavy shadows, no gradients. Large readable numbers for the projector: KPI values may go up to 44px on the results page.
- **Constraints:** vanilla CSS in `static/` (one shared `virtusa.css` per app, copied from the reference tokens). No CSS frameworks.
