// @ts-check
// Hermetic: no Electron binary, no display. Runs in PR CI.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

import { WINDOW_BACKGROUND, windowBackground } from '../src/appearance.js';

const css = readFileSync(
  fileURLToPath(new URL('../../ui/app/globals.css', import.meta.url)),
  'utf-8',
).replace(/\/\*[\s\S]*?\*\//g, '');

/**
 * The `--bg` declared in the first block matching `selector`.
 * @param {RegExp} selector
 */
function bg(selector) {
  const block = css.match(selector);
  assert.ok(block, `no block matching ${selector}`);
  const value = block[1].match(/--bg:\s*([^;]+);/);
  assert.ok(value, `no --bg in ${selector}`);
  return value[1].trim().toLowerCase();
}

test('the light window matches the page ground in light', () => {
  assert.equal(WINDOW_BACKGROUND.light, bg(/(?:^|\n):root \{([^}]*)\}/));
});

test('the dark window matches the page ground in both dark blocks', () => {
  assert.equal(WINDOW_BACKGROUND.dark, bg(/:root:not\(\[data-theme="light"\]\) \{([^}]*)\}/));
  assert.equal(WINDOW_BACKGROUND.dark, bg(/:root\[data-theme="dark"\] \{([^}]*)\}/));
});

test('follows the OS appearance', () => {
  assert.equal(windowBackground(true), WINDOW_BACKGROUND.dark);
  assert.equal(windowBackground(false), WINDOW_BACKGROUND.light);
});
