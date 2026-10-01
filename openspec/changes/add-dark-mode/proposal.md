## Why

VISTA only has a light theme, so researchers who run their OS in dark mode, or who watch long
HPC jobs in a dark room, get a bright white window that ignores their system setting.
`ui-modernization-direction-a` deferred dark mode because AmSC has not defined one. Colleagues
have since reviewed three candidate palettes side by side, and the team picked Deep Navy, a
dark theme built from the AmSC brand-dark blue.

## What Changes

- VISTA follows the operating system's light or dark appearance by default, and switches live
  when the OS setting changes.
- A new **Appearance** setting with three choices (System, Light, Dark) lets a researcher
  override the OS on that machine. The choice persists across reloads and restarts and applies
  before the first frame is painted, so the window never flashes the wrong theme.
- A Deep Navy dark palette covers every surface: rail, top bar, cards, modals, forms, status
  pills, notices, scrollbars and the terminal/log islands.
- The existing light theme does not change. To make that safe, every literal color in
  `globals.css` and in component inline styles moves onto a named token first. A lint check
  then keeps new literals out.
- Agent-produced content that assumes a white page (sandboxed HTML cards, PNG plots and images)
  keeps a light ground inside the dark UI. It is never color-inverted.
- The Electron window's native background follows the OS appearance, so the frame matches the
  page.

## Capabilities

### New Capabilities
- `ui-theme`: how VISTA chooses between its light and dark appearance, how a researcher
  overrides that choice, and the legibility guarantees both themes must meet.

### Modified Capabilities
(none — no existing main spec covers UI appearance; `ui-modernization-direction-a`'s `ui-shell`
delta is about navigation and layout, and is unaffected)

## Impact

- UI: `ui/app/globals.css` (token pass over ~77 literal colors, three undefined tokens defined,
  one dark token set), `ui/app/layout.tsx` (pre-paint theme script), a new `ui/lib/theme.ts`
  hook, an Appearance section in `ui/components/UserSettingsModal.tsx`, and token fixes in
  `ui/components/DebateThread.tsx` and `ui/components/SandboxedHtmlCard.tsx`.
- Electron: `electron/src/main.js` sets the window background from the OS appearance.
- Tests and CI: Vitest coverage for the theme hook and the Appearance control; a hermetic
  Playwright spec that runs key screens under an emulated dark scheme; a literal-color check in
  `./scripts/ci-local.sh ui lint`.
- No backend, MCP, database or API changes. The preference is stored in the browser, not on the
  user row.
- AmSC: this is a VISTA-side proposal ahead of an AmSC dark theme, like the warm neutral ramp
  before it, and should be raised with AmSC when design-system conformance is scoped.
