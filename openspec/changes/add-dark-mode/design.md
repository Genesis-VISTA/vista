## Context

All UI styling lives in one stylesheet, `ui/app/globals.css` (about 5,300 lines, no Tailwind, no
CSS modules). Direction A put the palette on `:root` tokens, but the rest of the file was not
converted:

- About 77 lines still use literal colors: shadows (`rgba(17,42,77,.16)`), black-alpha hovers
  (`rgba(0,0,0,.04–.06)`), modal scrims, `#fff` button labels, `#b01000` danger text (three
  places), notice colors (`#fff6e5` / `#7a5c00`), and the debate-simulation row borders.
- Three tokens are used but never declared (`--surface-2`, `--accent`, `--ok`; 7 references),
  so their fallbacks are the real values.
- `--brand` (`#004573`) is used both as a text/link color and as the fill of 14 filled
  controls. On a dark surface it measures about 2:1, so one token cannot serve both roles.
- `DebateThread.tsx` hardcodes three status colors, and `SandboxedHtmlCard.tsx` an inline
  border and white background.
- The `--term-*` tokens are already a dark console palette used in both themes.

The settings modal (`UserSettingsModal.tsx`) opens from the rail's settings button, which stays
disabled until the user record loads. The rail already keeps a per-machine preference (collapsed
state) in `localStorage`. The Electron window is created with `show: false` and shown on
`ready-to-show`, with no `backgroundColor`.

## Goals / Non-Goals

**Goals:**
- One token set drives both themes. Components never branch on the theme.
- The light theme is unchanged, pixel for pixel.
- The theme is resolved before first paint, with no flash.

**Non-Goals:**
- Syncing the override to native Electron chrome (title bar, context menus) through
  `nativeTheme.themeSource`. The window follows the OS; the override is page-only.
- Theming agent-produced HTML or images (see the spec's requirement on agent-produced content).
- A per-account preference stored on the backend.
- Adopting `@amsc/amsc-theme`, or changing the light palette.
- High-contrast or additional themes. The structure allows them later.

## Decisions

### 1. Tokens first, as a separate step with no visual change

Every literal moves to a role token before any dark value exists. As landed:

- Brand: `--brand-fill` and `--brand-fill-hover`. These replace `--brand` / `--brand-dark` on
  the seven filled controls (buttons, composer send, jump-to-latest, active project child,
  active project badge, settings switch, debate buttons). `--brand` stays on dots, the resizer,
  the loader and the scrollbar thumb, where it is an accent. Also `--on-brand`, `--link-hover`,
  `--brand-wash` and `--brand-wash-strong`.
- Washes: `--wash-faint`, `--wash` and `--wash-strong` (black at 0.04 / 0.05 / 0.06). Also
  `--surface-2`, `--surface-2-mid` and `--surface-2-strong` (50% grey at 0.08 / 0.10 / 0.12).
  The old fallbacks had three different strengths, so one `--surface-2` would have moved
  pixels.
- Status: `--danger-ink`, `--danger-tint`, `--warning-tint`, `--warning-strong`,
  `--warning-text`, `--warning-mark` and `--ok`. Debate kinds and simulation rows:
  `--kind-finding`, `--kind-done` and `--sim-{done,waiting,stalled,failed}`.
- Elevation: `--shadow-menu`, `--shadow-popover`, `--shadow-float`, `--shadow-modal`,
  `--scrim` and `--scrim-strong`.
- Other: `--accent`, `--media-mat`. The legacy aliases (`--panel`, `--panel-2`,
  `--bg-accent`) now point at their role tokens instead of repeating hex values.

`--brand-dark` stays as the logo and output-card plate. The light values equal today's literals,
and screenshot comparison confirmed that this step changes nothing visible.

*Alternative:* write the dark overrides per selector (`[data-theme=dark] .foo { … }`). Rejected.
It doubles every rule, is easy to miss, and the light theme stays full of literals.

### 2. Theme selection by attribute plus media query

```css
:root { /* light values (today's) */ }
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) { /* Deep Navy values */ color-scheme: dark; }
}
:root[data-theme="dark"] { /* same Deep Navy values */ color-scheme: dark; }
```

System means no attribute, so the media query decides and OS changes apply live with no
JavaScript. Light and Dark set `data-theme` on `<html>`. `color-scheme: dark` makes native
inputs, the rjsf forms and the default scrollbars follow. The two dark blocks carry identical
values; a unit test asserts that they do.

*Alternative:* always set the attribute from JavaScript, including for System (listen to
`matchMedia` and write `dark`/`light`). Rejected. It makes System depend on script, and a
script failure leaves the page stuck in light.

### 3. Pre-paint script in the root layout

A small synchronous inline `<script>` in the `<head>` of `ui/app/layout.tsx` reads
`localStorage["vista.theme"]` inside `try/catch` and sets `data-theme` when the value is
`light` or `dark`. `<html>` gets `suppressHydrationWarning`, because the attribute differs
between the server render and the client.

*Alternative:* `next/script` with `strategy="beforeInteractive"`. The Next 16 docs
(`02-components/script.md`) say it does not block hydration and is "fetched" before first-party
code. That is earlier than other scripts, but not guaranteed to run before first paint. Use it
only if a plain inline head script is rejected by React or the CSP. Task 3.2 verifies that
there is no flash either way.

### 4. A small `useTheme()` hook, storage only in the browser

`ui/lib/theme.ts` exposes `{ choice, resolved, setChoice }`. `choice` is `system | light |
dark`. `resolved` comes from `matchMedia` when the choice is System. `setChoice` writes storage
and the attribute (removing it for System) and survives refused storage. A `storage` event
listener keeps other tabs in step. It follows `NavRail.tsx`'s pattern of reading storage after
mount to avoid hydration mismatches.

*Alternative:* store it on the user row via `PUT /users/me`. Rejected. The pre-paint script
cannot wait for an API call, it ties a display preference to the backend being up, and VISTA is
single-user per machine anyway.

### 5. Appearance control in the settings modal

A segmented control (System / Light / Dark) in a new Appearance section at the top of
`UserSettingsModal.tsx`, above Clusters. Its hint reads "System matches your computer's light or
dark setting." Theme is a set-once preference, and the rail footer is already dense.

*Alternative:* a quick-switch icon beside the rail's settings button. Not chosen for now. It
can be added later on top of the same hook with no spec change.

### 6. The Deep Navy palette

The starting values below were contrast-checked; adjust them only against screenshots and the
contrast test in task 2.3.

| Token | Light (unchanged) | Deep Navy |
|---|---|---|
| `--bg` | `#f4f4f2` | `#0a1524` |
| `--surface` | `#ffffff` | `#112235` |
| `--surface-sunken` | `#fbfbfa` | `#0e1c2d` |
| `--surface-active` | `#ececea` | `#1b3049` |
| `--surface-2` | today's fallback | `#16283d` |
| `--line` / `--line-soft` / `--line-strong` | `#e2e2de` / `#f0f0ec` / `#d8d8d2` | `#213852` / `#1a2e45` / `#2a4566` |
| `--ink` / `--ink-2` | `#1c1c1a` / `#3d3d39` | `#e8eef5` / `#c6d2de` |
| `--muted` / `--muted-2` | `#767671` / `#6b6b66` | `#97aabf` / `#8a9db3` |
| `--brand` (text, links) | `#004573` | `#92c6f2` |
| `--brand-fill` (filled controls) | `#004573` | `#3577b0` |
| `--on-brand` | `#ffffff` | `#ffffff` |
| `--brand-dark` (logo plate) | `#112a4d` | `#06101c` |
| `--brand-tint` / `--brand-line` / `--brand-line-strong` | `#f2f6fa` / `#dce6ef` / `#cfd8e2` | `#173352` / `#28496d` / `#335a82` |
| `--brand-2` | `#a6111b` | `#f0707a` |
| `--success` / `--success-tint` / `--success-line` | `#0b730b` / `#f0f6f1` / `#cde0d1` | `#78cc8f` / `#132c25` / `#2b5a45` |
| `--warning` (fill) / `--warning-ink` / `--warning-line` / `--warning-tint` | `#e6c500` / `#a06a00` / `#d8b04a` / `#fff6e5` | `#e8c15a` / `#e8c15a` / `#8a7330` / `#2a2a1e` |
| `--danger` / `--danger-ink` / `--danger-line` / `--danger-tint` | `#d61200` / `#b01000` / `#e8a4a0` / `#fdecea` | `#ff8a7a` / `#ff8a7a` / `#7a3a40` / `#2f1d24` |
| `--brand-fill-hover` / `--link-hover` | `#112a4d` / `#112a4d` | `#2a6699` / `#c4e0f8` |
| `--brand-wash` / `--brand-wash-strong` | `rgba(0,69,115,.07)` / `.08` | `rgba(146,198,242,.1)` / `.13` |
| `--accent` | `#4a7fb5` | `#6fa3d6` |
| `--scrollbar-thumb` / `-hover` | `#004573` / `#112a4d` | `#335a82` / `#4a76a3` |
| `--wash-faint` / `--wash` / `--wash-strong` | black at `.04` / `.05` / `.06` | white at `.04` / `.06` / `.08` |
| `--surface-2` / `-mid` / `-strong` | 50% grey at `.08` / `.10` / `.12` | unchanged (grey reads on both) |
| `--warning-strong` / `--warning-text` / `--warning-mark` | `#7a5c00` / `#5c4600` / `#d8a400` | `#d9b450` / `#f0d98f` / `#e8c15a` |
| `--ok` | `#1a7f4b` | `#6fcf97` |
| `--kind-finding` / `--kind-done` | `#0b6b53` / `#3b2f7a` | `#5fcfae` / `#b7a9f2` |
| `--sim-done` / `-waiting` / `-stalled` / `-failed` | `#3f9a5a` / `#b8862b` / `#b8532b` / `#a8353a` | `#5fb878` / `#d9a648` / `#e08050` / `#e06470` |
| `--scrim` / `--scrim-strong` | `rgba(28,26,23,.4)` / `.66` | `rgba(3,8,15,.6)` / `.82` |
| `--shadow-menu` / `-popover` / `-float` / `-modal` | navy- and ink-tinted, see `globals.css` | black at `.45` / `.45` / `.4` / `.55` |
| `--media-mat` | `#ffffff` | `#ffffff` (agent content keeps its page) |
| `--term-bg` / `--term-bg-2` | `#12100e` / `#1a1714` | `#08111c` / `#0d1826` |
| `--term-ink` / `--term-muted` / `--term-line` | `#c9c1b6` / `#8a8278` / `#2a2520` | `#c9d3de` / `#8193a8` / `#1a2a3d` |
| `--term-ok` / `--term-warn` / `--term-error` | `#5a9e6f` / `#d4a843` / `#c9503c` | `#6fb58a` / `#d4a843` / `#e0705c` |

Measured on `--surface`: ink 13.8:1, muted 6.8, muted-2 5.8, brand 8.9, brand-2 5.6,
warning-ink 9.4, danger-ink 7.0. White on `--brand-fill` is 4.75:1, and `--brand-fill` against
the surface is 3.4:1. Terminal ink on `--term-bg` is 12.5:1, term-muted 6.0. The terminal
islands are retinted toward navy in the dark theme so they sit darker than the cards instead of
reading as a brown patch.

### 7. Agent content on a light mat

`SandboxedHtmlCard` keeps `background: #fff` inside the iframe document, which is agent content,
and moves its frame's border to `--line`. Inline images and plots get a `--media-mat` token
(`#ffffff` in both themes) as a padded background. No `filter: invert()`, because inverted plots
misstate the data.

### 8. Electron background follows the OS

`createMainWindow` passes `backgroundColor` from `nativeTheme.shouldUseDarkColors` (`--bg` of
each theme), so resize and first-show repaints match. Chromium already passes the OS appearance
to `prefers-color-scheme`, so System needs no IPC.

### 9. Literal-color guard

`./scripts/ci-local.sh ui lint` gains a check that fails on a hex or `rgb(a)(` literal in
`globals.css` outside the token blocks, and on a hex color in an inline `style` in
`ui/components/`. Allowed exceptions are listed explicitly in the check (for example the
sandboxed iframe's own document).

## Risks / Trade-offs

- [A literal is missed in a 5,300-line file and shows as a light patch in dark mode] → the lint
  guard in decision 9, plus dark-scheme screenshots of every main screen and modal.
- [The Appearance control is unreachable while the user record fails to load, because the
  settings button is disabled] → System and any stored choice still apply. Accepted for now,
  since VISTA is largely unusable without the backend. Revisit if the rail quick switch is
  added.
- [Linux desktops that don't publish a color scheme report light] → explicit Dark covers them.
- [The pre-paint inline script conflicts with React 19 or a future CSP] → fall back to
  `beforeInteractive` (decision 3). Task 3.2 checks for a flash either way.
- [Deep Navy cools the app, while the light theme is warm] → a deliberate choice from colleague
  review. The two themes are siblings, not mirrors.
- [AmSC later publishes a different dark palette] → all values live in one token block, so
  adopting theirs is a value swap.

## Migration Plan

None needed. There is no stored data and no backend change. Existing users start on System,
which matches their OS. Rollback is a revert.
