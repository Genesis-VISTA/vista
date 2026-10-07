// @ts-check
// Hermetic: no Electron binary, no display. Runs in PR CI.
import { test } from 'node:test';
import assert from 'node:assert/strict';

import { allowPermission } from '../src/permissions.js';

const ORIGIN = 'http://127.0.0.1:3000';

test('VISTA may write to the clipboard, asked by page or by origin', () => {
  // The request handler passes the page's URL, the check handler its origin.
  assert.equal(allowPermission(ORIGIN, 'clipboard-sanitized-write', 'http://127.0.0.1:3000/projects'), true);
  assert.equal(allowPermission(ORIGIN, 'clipboard-sanitized-write', 'http://127.0.0.1:3000'), true);
});

test('reading the clipboard stays refused', () => {
  assert.equal(allowPermission(ORIGIN, 'clipboard-read', 'http://127.0.0.1:3000/'), false);
});

test('every other permission stays refused, even for VISTA', () => {
  for (const permission of ['media', 'notifications', 'geolocation', 'midi', 'openExternal', 'fullscreen']) {
    assert.equal(allowPermission(ORIGIN, permission, 'http://127.0.0.1:3000/'), false, permission);
  }
});

test('another origin may not write to the clipboard', () => {
  for (const url of [
    'https://auth.globus.org/v2/oauth2/authorize',
    'http://127.0.0.1:8001/',
    'http://localhost:3000/',
    'file:///tmp/x.html',
    'blob:http://127.0.0.1:3000/uuid',
  ]) {
    assert.equal(allowPermission(ORIGIN, 'clipboard-sanitized-write', url), false, url);
  }
});

test('no requesting URL is refused', () => {
  assert.equal(allowPermission(ORIGIN, 'clipboard-sanitized-write', undefined), false);
  assert.equal(allowPermission(ORIGIN, 'clipboard-sanitized-write', ''), false);
});
