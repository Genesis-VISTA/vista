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

// The startup page declares a subset of the UI's tokens; each has to carry the
// UI's own value in both themes.
test('the startup page uses the UI palette, token for token, in both themes', () => {
  const startup = readFileSync(
    fileURLToPath(new URL('../src/startup.css', import.meta.url)),
    'utf-8',
  ).replace(/\/\*[\s\S]*?\*\//g, '');
  /** @param {string} block */
  const tokens = (block) => Object.fromEntries(
    [...block.matchAll(/(--[a-z0-9-]+):\s*([^;]+);/g)].map((m) => [m[1], m[2].trim().toLowerCase()]),
  );
  /** @param {string} source @param {RegExp} selector */
  const block = (source, selector) => {
    const hit = source.match(selector);
    assert.ok(hit, `no block matching ${selector}`);
    return tokens(hit[1]);
  };
  const pageLight = block(startup, /(?:^|\n):root \{([^}]*)\}/);
  const pageDark = block(startup, /@media \(prefers-color-scheme: dark\) \{\s*:root \{([^}]*)\}/);
  const uiLight = block(css, /(?:^|\n):root \{([^}]*)\}/);
  const uiDark = block(css, /:root:not\(\[data-theme="light"\]\) \{([^}]*)\}/);
  assert.ok(Object.keys(pageLight).length >= 8);
  assert.deepEqual(Object.keys(pageDark).sort(), Object.keys(pageLight).sort());
  for (const [name, value] of Object.entries(pageLight)) assert.equal(value, uiLight[name], `light ${name}`);
  for (const [name, value] of Object.entries(pageDark)) assert.equal(value, uiDark[name], `dark ${name}`);
});
