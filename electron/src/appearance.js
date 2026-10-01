// @ts-check
//
// The window's own background, painted before the page has drawn and around
// it while a resize catches up. It follows the OS appearance, as the page does
// by default, so opening VISTA on a dark Mac never flashes a light frame
// (ui-theme: "No flash of the wrong theme"). Kept free of any Electron import
// so it can be tested with plain `node --test`.
//
// The values are the page's own `--bg` for each theme; test/appearance.test.js
// reads them back out of ui/app/globals.css so the two cannot drift apart.
// A researcher's explicit Light/Dark choice is the page's alone: native chrome
// keeps following the OS (add-dark-mode design, non-goals).

export const WINDOW_BACKGROUND = Object.freeze({
  light: '#f4f4f2',
  dark: '#0a1524',
});

/**
 * @param {boolean} osIsDark `nativeTheme.shouldUseDarkColors`
 * @returns {string}
 */
export function windowBackground(osIsDark) {
  return osIsDark ? WINDOW_BACKGROUND.dark : WINDOW_BACKGROUND.light;
}
