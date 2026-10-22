# Redesign: the "Blend" look and widget dashboard

How to build the next look of the web interface: Homestead's layouts and
components, carrying OpenInverter's cyan and mint identity, with a dashboard
made of the same widgets as the gauge pages.

## Mockups

Design canvas: <https://claude.ai/artifact/7FBxRFGGCW7v8JjinDNeKV>
(private until shared from its Share menu).

| Row | Artboards | Use |
|---|---|---|
| **Blend** (build this) | Blend · Dashboard, Blend · Gauges, Blend · Phone dashboard | The target look |
| Homestead reference | Desktop · Dashboard, Gauges, Parameters; Phone · Dashboard, Gauges | Layout reference, and the Parameters page (not redrawn in Blend yet: apply the Blend tokens to it) |

Homestead source the mockups were measured from:
<https://github.com/homestead-lab/homestead/blob/main/web/style.css> (tokens at the top, `.side`, `.top`, `.card`,
`.btn`, `.pill`, `.bottombar`, `.dashboard-*`).

The mockup values (355.6 V, 4767 rpm …) are the live demo's data. Small extras such as "≈ 31 kWh left", "BMS ok" and the
cell min/max are illustrations of what a widget *can* show, not existing features.

## Progress

| Phase | State | Checkpoint |
|---|---|---|
| 1. Tokens and fonts | **Done** | Neutral `#070708` ground, glass surfaces, 20/14/9 px radii, `--grad-signature`, new `--grad-edge`; light theme on Homestead's greys with deeper status colours. Inter Tight + JetBrains Mono bundled (`data/fonts/`, 85 KB, short names because SPIFFS paths max out at 31 characters); firmware serves `.woff2` as `font/woff2` (+40 bytes flash). Numbers now use the mono face. 245/245 E2E, screenshots regenerated. |
| 2. Components | **Done** | Glass cards (`--card-bg`) with the gradient edge only on hero cards (`.edge`, `.hero-card`) plus a running aurora (`.hero-card:has(.pill.active)`); sidebar selection is Homestead's pill (`--on-bg`/`--on-fg`) lit by `--glow-pri`; neutral glass buttons, `button.primary` = the selected-pill colours, `button.danger` red tint; gradient `Run` pill, outlined info/warn pills; mono uppercase labels (h3, metric labels, table heads); 12 px fields; gauge tiles share the card glass; page switchers are a segmented control (`.seg` on `#gauge-pages`, `#settings-subtabs`); reduced-motion and reduced-transparency honoured. Both themes checked in screenshots. 245 E2E pass (one existing timing flake in `gauge-extras` under full parallel load: 10/10 alone). Not yet done: gauge arcs keep each tile's own colour (the signature gradient as the default tile colour comes with Phase 3's new tile types). |
| 3. Shared widget system | Next | |
| 4. App chrome | Planned | |
| 5. Polish | Planned | |

## Ground rules

- **The firmware is not touched.** Everything here is `data/` (SPIFFS), so app flash stays at its current size and OTA images
  don't grow. The Wemos SPIFFS partition is 1.44 MB with about 820 KB used.
- **No internet on the device.** Fonts are bundled in `data/fonts/`, never loaded from Google Fonts.
- **Keep the variable names in `style.css`.** About 200 rules already use `var(--…)`; changing the values restyles most of
  the app without touching `app.js`.
- **Each phase ships as its own dev pre-release** (push to `dev`), checked on hardware before the next starts.
- **Tests stay green.** E2E tests target `.gauge-tile`, `.page-pill`, `.tablink`, `#params`, `#update-badge`, `.svg-gauge`,
  `.grid-stack-item`: keep those hooks (add classes, don't rename them).

## Phase 1: tokens and fonts (`data/style.css`)

Replace the values in `:root[data-theme="dark"]` (and the `prefers-color-scheme: dark` copy):

| Token | Blend value | Notes |
|---|---|---|
| `--bg` | `#070708` | Homestead's ground, neutral not navy |
| `--bg-glow1` / `--bg-glow2` | `rgba(76,201,240,.08)` top-right / `rgba(84,230,164,.06)` bottom-left | the "aurora", replaces Homestead's gold |
| `--surface`, `--surface2`, `--surface3` | `#111113`, `#161618`, `#1d1d20` | |
| `--card` | `linear-gradient(158deg, rgba(255,255,255,.085), rgba(255,255,255,.028) 42%, rgba(255,255,255,.012))` | glass |
| `--border`, `--border2` | `rgba(255,255,255,.07)`, `rgba(255,255,255,.13)` | |
| `--text`, `--text2`, `--text3` | `#e8ecf1`, `#9aa3b2`, `#8a93a3` | `--text3` is lighter than today's `#5d6675` so small labels pass 4.5:1 |
| `--accent`, `--green` | `#4cc9f0`, `#54e6a4` | unchanged: the identity |
| `--amber`, `--red` | `#ffb454`, `#ff6b6b` | unchanged |
| `--radius`, `--radius-sm`, `--radius-xs` | `20px`, `14px`, `9px` | |
| `--shadow` | `inset 0 1px 0 rgba(255,255,255,.07), 0 10px 34px rgba(0,0,0,.45)` | |
| new `--grad-signature` | `linear-gradient(90deg, #4cc9f0, #54e6a4)` | data fills: meters, SOC, gauge arcs |
| `--grad-edge` | `linear-gradient(120deg, rgba(76,201,240,.7), rgba(76,201,240,.08) 30%, rgba(255,255,255,.06) 55%, rgba(84,230,164,.45))` | hero cards only (Phase 2) |
| `--font` | `"Inter Tight", system-ui, sans-serif` | |
| `--mono`, `--num` | `"JetBrains Mono", ui-monospace, monospace` | numbers and labels |

Light theme: Homestead's light ground (`#ececed` bg, `#fff` surfaces, `#16161a` text, `#5c5c66`/`#686872` muted), keeping
today's deeper light-mode accents (`#0284c7`, `#0f9d63`) so text contrast holds.

Fonts:

- Inter Tight (variable, weights 400–800) and JetBrains Mono (variable, 400–700), Latin subset, woff2, into `data/fonts/`.
  Both are SIL OFL: add them to `THIRD_PARTY_NOTICES.md`.
- `@font-face` with `font-display: swap`; system fonts stay in each stack as fallback.
- woff2 is already compressed: don't give them `.gz` twins.
- Cost about 90–110 KB of SPIFFS.

Done when: the app shows the new palette and type everywhere, both themes, nothing else changed; `npm test` passes.

## Phase 2: components (`style.css`, a few classes in `app.js`)

| Component | Today | Blend |
|---|---|---|
| Card (`.dash-box`, gauge tiles) | navy glass, gradient edge on every card | Homestead glass (`--card`, 1px `--border`, `--shadow`, hover lift −2px). New class `.edge` draws the `--grad-edge` ring: use it only on the status hero and the two largest gauges of a page |
| Running state | — | `.glow-run`: two radial washes inside the hero card, mint bottom-left, cyan top-right |
| Buttons | accent outline | neutral glass; **primary = white `#fff`, text `#0a0a0b`, shadow `0 8px 26px rgba(76,201,240,.22)`**; destructive = red tint |
| Pills | flat tones | `ok`: mint gradient fill; `info`: cyan tint + border; `neutral`: glass; `warn`: amber tint |
| Page pills (`.page-pill`) | outlined | Homestead segmented control: one container, selected = white pill |
| Sidebar item (`.tablink.active`) | cyan tint + inset bar | **white pill, dark text, cyan/mint underglow**; keep `.tablink` |
| Gauges (radial, bar, line) | accent | stroke/fill `--grad-signature` with a soft glow; regen/negative in mint; warning thresholds amber |
| Inputs, selects | | glass field, 12px radius, mono for numbers |
| Tables | | mono uppercase 10px headers, 1px row lines, row hover |
| Section labels | | mono, 10.5px, uppercase, `.11em` tracking, `--text3` |

Accessibility: honour `prefers-reduced-motion` (no hover lift, no glow animation) and `prefers-reduced-transparency`
(drop `backdrop-filter`); the blur is also costly on old phones.

Done when: every page matches the Blend artboards' components; screenshots regenerated (`npm run screenshots`).

## Phase 3: one widget system for Dashboard and Gauges

The dashboard becomes **a gauge page**. It uses the gauge grid, the gauge editor and the existing "add tile" flow; there is no
second widget catalog. Every tile type works on the Dashboard and on any gauge page.

### Data model: `gauges.json` v4

```json
{
  "v": 4,
  "autoPage": true,
  "dashboard": "home",
  "pages": [
    { "id": "home", "name": "Dashboard", "layout": "stack", "items": [ … ] },
    { "id": 1, "name": "Driving", "items": [ … ] }
  ]
}
```

- `dashboard`: the id of the page the Dashboard tab shows. That page is left out of the Gauges tab's page switcher and of
  auto-switching (`cond`), but its tiles are ordinary tiles.
- `layout` (new, per page): `"scale"` (today's behaviour: the 10-column grid scales, a page looks identical on a phone) or
  `"stack"` (on narrow screens tiles stack full-width in reading order, y then x, like Homestead's phone dashboard). Default
  `"scale"` for gauge pages, `"stack"` for the dashboard page.
- Tiles keep the v3 shape `{ id, name, type, color, min, max, invert?, x, y, w, h }` plus a per-type `opts` object for the new
  types below.
- **Migration v3 → v4** (in the existing `migrateGauges`): keep every page; if there is no `dashboard`, create the `home` page
  from today's fixed dashboard: status hero, one metric tile per entry in `dashMetrics` (uiprefs.json / localStorage), the
  messages tile and the actions tile. Old firmware/UIs ignore `dashboard` and simply show `home` as another gauge page.
- The settings export bundle and the update stash already carry `gauges.json` whole: nothing to add there.

### New tile types

Added to the same type list as `radial`, `bar`, `line`, `text`, `indicator`, `action`, `toggle`, `slider`:

| Type | Shows | Default size (w×h, 10 cols) | Options |
|---|---|---|---|
| `status` | opmode as the big word ("Running"), pills for direction/gear/errors, up to 3 inline figures | 10×3 | which figures (spot value names) |
| `metric` | one value, unit, sparkline of the last 60 samples, optional min/max footer | 3×2 | `spark: bool`, footer text |
| `battery` | SOC with signature bar, three sub-values (e.g. cell min/max, temp) | 5×3 | the SOC value name and three sub-value names |
| `terminal` | the current command terminal (input + output) | 5×4 | — |
| `messages` | inverter messages / errors list | 5×3 | — |
| `actions` | buttons: start (manual mode), stop, save/restore flash, reboot inverter, reboot ESP32 | 10×1 | which buttons |
| `cansend` | the raw CAN frame sender (CAN mode) | 5×2 | — |

The terminal, messages, actions and CAN sender move out of the `Dashboard` component (`data/app.js`, `const Dashboard`,
around line 860) into tile renderers; the logic is reused as-is. Action tiles honour the user's role (viewer: disabled) the
same way the existing action/toggle/slider tiles do, and editing never fires an action.

### Editing

- One editor for both tabs: the Gauges tab's "Edit layout" (GridStack, drag/resize, tile settings, page settings, the
  Starter Layout button). The Dashboard tab gets the same **Edit layout** button.
- From Homestead take only the look of edit mode: a sticky edit toolbar ("Editing · 9 widgets · Unsaved changes · Phone
  preview · Cancel · Save layout"), the dotted canvas background, a 2px outline on the selected tile.
- "Phone preview" in the toolbar shows a `stack` page as it will stack.
- "Move to page…" in tile settings, so a tile can go from the Dashboard to a gauge page and back.

Done when: a fresh device shows a Dashboard that looks like *Blend · Dashboard*, built entirely from tiles; any tile can
be added to either tab; an existing `gauges.json` (v1–v3) migrates without loss.

## Phase 4: app chrome (`data/app.js`)

- **Sidebar** (238px): brand block (mark = the two cyan/mint rings, name, `web vX · F/W Y`), groups **Monitor** (Dashboard,
  Gauges, Spot values, Plot, Data logger), **Configure** (Parameters, CAN mapping), **Device** (Files, Update, Settings, Support),
  footer with device name + live dot, link pill (`UART · FAST`, `CAN`, `SSE · 10 Hz`), refresh age, signed-in user and Sign out.
  Nav items become real `<a>`/`<button>` (today they are clickable `<div>`s the keyboard can't reach); keep `.tablink`.
- **Top bar** (74px glass): breadcrumb (`MONITOR / DASHBOARD`, mono, current crumb in cyan), page title (device name on the
  Dashboard, page name on Gauges), page actions right-aligned (Dashboard: Stop inverter, **Save to flash** primary; Gauges:
  Auto-switch, Record, Full screen, **Edit layout**).
- **Parameters**: category list beside the table (as *Desktop · Parameters*), and a sticky amber bar when values have been
  set but not saved: "N changes are live but not saved to flash" with Restore / **Save to flash**.
- **Phone (< 600px)**: the icon rail becomes the 5-tab bottom bar from *Blend · Phone dashboard*: Home, Gauges, Values,
  Params, More (More opens the rest of the nav); active tab has the small cyan→mint marker; 44px targets; compact top bar
  with a search icon button.

Done when: the chrome matches the Blend artboards at 1440px and 390px; `mobile.spec.mjs` updated for the bottom bar.

## Phase 5: polish

Number transitions on live values (respecting reduced motion), light-theme pass with a contrast check of every text/ground
pair, regenerate screenshots and the README hero, refresh the live demo (`demo/`), update `THIRD_PARTY_NOTICES.md`.

## Size budget

| | SPIFFS | App flash |
|---|---|---|
| Fonts | +90–110 KB | — |
| CSS (phases 1–2) | +8–11 KB raw, about +3 KB gzipped | — |
| `app.js` (phases 3–4) | +15–25 KB raw, about +5–8 KB gzipped | — |
| **Total** | **about +110–130 KB of 1.44 MB (≈57% → ≈66%)** | **0** |

If SPIFFS ever gets tight: stop shipping the uncompressed copy of files that have a `.gz` twin (the ESP serves the `.gz`);
that frees about 400 KB.

## Where things are

| What | Where |
|---|---|
| Design tokens | `data/style.css`, `:root`, `:root[data-theme="dark"]`, the `prefers-color-scheme` block |
| Card edge (today's signature) | `data/style.css`, `.dash-box::before` |
| Dashboard component | `data/app.js`, `const Dashboard` |
| Dashboard metrics setting | `data/app.js`, `getDashMetrics()` (localStorage + `uiprefs.json`) |
| Gauge grid, schema, migration | `data/app.js`, "Gauges (grid dashboard)" section, `GRID_COLS`, `migrateGauges` |
| Starter layout | `data/gauges-sample.json` |
| Mock data for tests/screenshots/demo | `tests/mock-esp/server.mjs`, `demo/demo-data.js` |
| Screenshots | `npm run screenshots` → `doc/screenshots/` |

After any change in `data/`: `python gzip_assets.py`, then `npm test`.
