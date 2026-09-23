# assets/

## Overview <!-- ref: overall -->
The `assets/` directory contains the entire frontend for the gms-ma web viewer: two single-page HTML pages (timeline and library), four CSS stylesheets, and a shared JS module tree. The viewer HTTP server (`gms_ma/viewer.py`) serves these files directly from disk with no build step, bundling, or transpilation.

## Data Flow <!-- ref: overall -->
```
Browser ──GET /──> viewer.py:do_GET ──> _serve_asset(timeline.html)
Browser ──GET /library──> viewer.py:do_GET ──> _serve_asset(library.html)
Browser ──GET /assets/css/*──> viewer.py:do_GET ──> _serve_static()
Browser ──GET /assets/js/*──> viewer.py:do_GET ──> _serve_static()
Browser ──GET /api/songs──> viewer.py:do_GET ──> repo.all_songs()
Browser ──GET /api/song?name=X──> viewer.py:do_GET ──> build_manifest()
Browser ──GET /api/library?…──> viewer.py:do_GET ──> lib.query_page()
Browser ──GET /api/midi-ports──> viewer.py:do_GET ──> midi_out + clipboard
Browser ──GET /api/about──> viewer.py:do_GET ──> static _ABOUT dict
Browser ──GET /license──> viewer.py:do_GET ──> _serve_license()
Browser ──POST /api/play──> viewer.py:do_POST ──> _Player.play() ──> midi_out.WinMidiOut
Browser ──POST /api/library/play──> viewer.py:do_POST ──> _Player.play()
Browser ──POST /api/library/clipboard──> viewer.py:do_POST ──> clipboard.copy_file_to_clipboard()
```

## assets/timeline.html <!-- ref: assets/timeline.html:1-67 -->
**Purpose**: The song-structure timeline viewer — the primary visualisation page showing arrangement blocks, motif hits, harmony, and repetition view.
**Why**: This is the main user-facing page; it loads `timeline/app.js` as an ES module and provides the full interactive timeline with playback, zoom, and view switching.
**Data Flow**: HTML loads CSS → JS modules → API calls → renders on `<canvas>`.
**Relationships**: Served at `/` and `/index.html` by `viewer.py._serve_asset()`.

## assets/library.html <!-- ref: assets/library.html:1-58 -->
**Purpose**: The loop library browser — a paginated, filterable list of all indexed patterns with facet search, tree/flat view toggle, and detail panel.
**Why**: Provides the secondary entry point for browsing and auditioning individual patterns; links back to the timeline via the top nav.
**Data Flow**: HTML loads CSS → JS modules → API calls → renders DOM list.
**Relationships**: Served at `/library` and `/library.html` by `viewer.py._serve_asset()`.

## assets/css/tokens.css <!-- ref: assets/css/tokens.css:1-169 -->
**Purpose**: Design-token definitions using CSS custom properties, with `:root` defaults (dark), `[data-theme=light]` overrides, and `[data-theme=night]` overrides.
**Why**: Centralises all colour values so the theme system (`theme.js`) can switch themes by setting `data-theme` on `<html>`. The palette keys (e.g. `--s0f1115`) are consumed by `theme.js` which reads them into a JS object for canvas drawing.
**Data Flow**: CSS custom property → `theme.js.refreshPalette()` → `PAL_KEYS` map → canvas drawing.
**Relationships**: Loaded first by both HTML pages; all other CSS files reference these tokens.

## assets/css/timeline.css <!-- ref: assets/css/timeline.css:1-64 -->
**Purpose**: Layout and styling for the timeline viewer page (canvas, scroller, side panel, hover tooltip, legend, mix/mute controls).
**Why**: Page-specific styles for the timeline's unique layout (flex row with canvas main area and 300px side panel).
**Data Flow**: CSS rules → browser rendering.
**Relationships**: Loaded by `timeline.html`.

## assets/css/library.css <!-- ref: assets/css/library.css:1-92 -->
**Purpose**: Layout and styling for the library browser page (filters sidebar, pattern list, pagination, tree view, detail panel).
**Why**: Page-specific styles for the library's unique layout (236px filters + flex main + 318px side panel).
**Data Flow**: CSS rules → browser rendering.
**Relationships**: Loaded by `library.html`.

## assets/css/components.css <!-- ref: assets/css/components.css:1-49 -->
**Purpose**: Shared UI components: settings popover (`#ma-setpanel`), about popover (`#ma-aboutpanel`), bottom transport bar (`.mbar`), and toast (`#toast`).
**Why**: These components are mounted by both pages via the shared JS modules (`settingsPanel.js`, `aboutPanel.js`, `miniPlayer.js`, `toast.js`), so their CSS is shared.
**Data Flow**: CSS rules → browser rendering.
**Relationships**: Loaded by both HTML pages.

## assets/js/common/api.js <!-- ref: assets/js/common/api.js:1-34 -->
**Purpose**: Thin API client wrapping `fetch` for all viewer JSON endpoints.
**Why**: Centralising fetch calls means view modules depend on `ApiClient` methods (e.g. `api.songs()`, `api.library()`) rather than raw URL strings, making the code testable and easy to mock.
**Data Flow**: `path` → `fetch()` → `r.json()` → parsed JSON or thrown error.
**Relationships**: Imported by `timeline/app.js` and `library/app.js`.

## assets/js/common/dom.js <!-- ref: assets/js/common/dom.js:1-20 -->
**Purpose**: Tiny DOM helpers (`$`, `esc`, `on`, `make`) shared by every page module.
**Why**: Eliminates repeated `document.getElementById` and `innerHTML` boilerplate; `esc()` provides XSS-safe HTML escaping.
**Data Flow**: `id` → DOM element; `string` → escaped string.
**Relationships**: Imported by all common modules and both app entry points.

## assets/js/common/format.js <!-- ref: assets/js/common/format.js:1-25 -->
**Purpose**: Pure formatting/colour helpers with no DOM or state dependencies.
**Why**: `fmtSec` formats seconds as `M:SS`; `hashHue` deterministically maps a string to a hue angle (used for pattern colour coding); `clamp`/`clamp01` are used by `settings.js` for input validation.
**Data Flow**: `number/string` → formatted string or hue integer.
**Relationships**: Imported by `settings.js`, `theme.js`, `miniPlayer.js`, `timeline/app.js`, `library/app.js`.

## assets/js/common/settings.js <!-- ref: assets/js/common/settings.js:1-82 -->
**Purpose**: Observable settings store persisted to `localStorage`, shared between both pages.
**Why**: Both the timeline and library pages use the same settings key (`gms_ma.settings`), so user preferences (theme, opacities, motif layout) persist across page navigation. The `SettingsStore` class uses a `Set` of subscribers to notify views of changes.
**Data Flow**: `localStorage` ↔ `SettingsStore.state` → subscriber callbacks → UI re-render.
**Relationships**: Imported by `timeline/app.js`, `library/app.js`, `theme.js`.

## assets/js/common/theme.js <!-- ref: assets/js/common/theme.js:1-56 -->
**Purpose**: Resolves the effective theme (auto/light/dark/night), applies it to the document, and reads CSS custom properties into a JS palette object for canvas drawing.
**Why**: The canvas rendering in `timeline/app.js` needs the resolved colour palette to draw blocks, labels, and hover highlights. `theme.js` bridges CSS custom properties and JS by reading `getComputedStyle` for each `PAL_KEYS` entry.
**Data Flow**: `settings.get("theme")` → `document.documentElement.setAttribute("data-theme", ...)` → `refreshPalette()` → `this.palette` object.
**Relationships**: Imported by `timeline/app.js` and `library/app.js`.

## assets/js/common/storage.js <!-- ref: assets/js/common/storage.js:1-17 -->
**Purpose**: Safe `localStorage` JSON wrapper that never throws in private/blocked contexts.
**Why**: `localStorage` can throw in Safari private mode or when storage is full; this module swallows all errors so the app degrades gracefully.
**Data Flow**: `key` → `localStorage.getItem/Item` → parsed JSON or `null`.
**Relationships**: Imported by `settings.js`.

## assets/js/common/gm.js <!-- ref: assets/js/common/gm.js:1-60 -->
**Purpose**: General MIDI instrument name lookup and track display-name logic.
**Why**: Used for rendering instrument labels in both the timeline and library. `gmLabel()` maps a program number to a GM patch name; `trackDisplayName()` prefers a real composer-provided name over the GM patch default.
**Data Flow**: `program, isDrums` → display string.
**Relationships**: Imported by `timeline/app.js` and `library/app.js`.

## assets/js/common/exportActions.js <!-- ref: assets/js/common/exportActions.js:1-62 -->
**Purpose**: Shared pattern download/clipboard actions used by both the library and timeline pages.
**Why**: Both pages need the same export logic (download MIDI, copy to clipboard, DAW ch1 export). This module centralises the fetch calls to `/api/library/download`, `/api/library/clipboard`, `/api/library/copy-ch1`, and `/api/library/sanitize`.
**Data Flow**: `{pattern_id, render, bpm, mode}` → `fetch()` → download or toast feedback.
**Relationships**: Imported by `timeline/app.js` and `library/app.js`.

## assets/js/common/toast.js <!-- ref: assets/js/common/components/toast.js:1-14 -->
**Purpose**: Shared transient toast notification (3-second auto-fade).
**Why**: Both pages need the same toast UX; this module creates a toast function bound to the `#toast` element.
**Data Flow**: `msg, isErr` → `#toast` text + CSS class + auto-clear.
**Relationships**: Imported by `timeline/app.js` and `library/app.js`.

## assets/js/common/miniPlayer.js <!-- ref: assets/js/common/components/miniPlayer.js:1-183 -->
**Purpose**: Shared compact transport bar (play/pause/stop/seek/speed) mounted at the bottom of both pages.
**Why**: Both the timeline and library need a transport bar for auditioning patterns and songs. The miniPlayer owns its DOM and local position clock; the host supplies callbacks for tick/end/error/speed events. It supports two modes: pattern playback (`/api/library/play`) and full-song playback (`/api/play`).
**Data Flow**: `{pattern_id|song, render, device, speed}` → `fetch()` → live MIDI via `_Player` → `onTick`/`onEnded` callbacks → UI update.
**Relationships**: Imported by `timeline/app.js` and `library/app.js`.

## assets/js/common/components/aboutPanel.js <!-- ref: assets/js/common/components/aboutPanel.js:1-87 -->
**Purpose**: Shared About popover (info button + panel) with project metadata fetched from `/api/about`.
**Why**: Both pages get the same About UI from one source; version/license are fetched dynamically with a static fallback.
**Data Flow**: `fetch("/api/about")` → `{name, tagline, version, license, license_url, repo}` → render panel.
**Relationships**: Imported by `timeline/app.js` and `library/app.js`.

## assets/js/common/components/settingsPanel.js <!-- ref: assets/js/common/components/settingsPanel.js:1-109 -->
**Purpose**: Shared settings popover (gear button + panel) with theme, opacity, and motif layout controls.
**Why**: Both pages get the same settings UI from one source; the host app reacts to changes via `onChange`.
**Data Flow**: `settings.set(key, value)` → `theme.apply()` + `sync()` + `onChange(key)`.
**Relationships**: Imported by `timeline/app.js` and `library/app.js`.

## assets/js/timeline/app.js <!-- ref: assets/js/timeline/app.js:1-55051 -->
**Purpose**: Composition root for the song-structure timeline viewer. Imports all shared modules and `layout.js`, then wires up the canvas rendering, playback, zoom, view switching, and export actions.
**Why**: This is the largest JS file (~55K lines) and the most complex frontend module. It owns the canvas rendering loop, the arrangement/motifs/harmony/repetition views, the mix/mute/solo transport, and the pattern detail side panel.
**Data Flow**: `api.*` → state → `render()` → canvas 2D context; user events → state updates → `render()`.
**Relationships**: Entry point loaded by `timeline.html`. Flagged as a complexity concern due to size.

## assets/js/timeline/layout.js <!-- ref: assets/js/timeline/layout.js:1-~100 -->
**Purpose**: Pure layout math for the timeline's Motifs view — interval packing and layer grouping.
**Why**: No DOM or state dependencies, so the layout algorithms are trivially unit-testable. `packLanes()` uses greedy interval colouring; `largestPerRegion()` keeps only the biggest non-overlapping motifs.
**Data Flow**: `[hit[]]` → `[[hit, ...], ...]` (lanes) or `[hit[]]` (largest).
**Relationships**: Imported by `timeline/app.js`.

## assets/js/library/app.js <!-- ref: assets/js/library/app.js:1-35638 -->
**Purpose**: Composition root for the loop library browser. Imports all shared modules plus `forest.js` and `pager.js`, then wires up the pattern list, facets, pagination, and detail panel.
**Why**: This is the second-largest JS file (~35K lines). It owns the library's flat/tree views, facet filtering, pagination with a sliding window cache, and the pattern detail/audition side panel.
**Data Flow**: `api.library()` → `data` → render list + facets + pager; user events → filter/page → API call.
**Relationships**: Entry point loaded by `library.html`. Flagged as a complexity concern due to size.

## assets/js/library/forest.js <!-- ref: assets/js/library/forest.js:1-~100 -->
**Purpose**: Pure building-block containment algorithm — determines which shorter loops sit inside longer ones (bar-aligned).
**Why**: No DOM/state dependencies, so the algorithm is trivially unit-testable and reusable. Returns a `{childMap, roots}` forest structure used by the library's tree view.
**Data Flow**: `[catalog rows]` → `{childMap: Map, roots: []}`.
**Relationships**: Imported by `library/app.js`.

## assets/js/library/pager.js <!-- ref: assets/js/library/pager.js:1-~70 -->
**Purpose**: Pure pagination math for the library list (no DOM, no state).
**Why**: Decouples pagination logic from rendering so it can be tested independently. Supports page sizes 10/25/50/100/200 and 0 (all).
**Data Flow**: `page, size, total` → `{start, end, pages, label}`.
**Relationships**: Imported by `library/app.js`.