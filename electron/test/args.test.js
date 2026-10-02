// @ts-check
import { test } from 'node:test';
import assert from 'node:assert/strict';

import { parseArgs } from '../src/args.js';

test('preserves the existing URL, development, smoke-test and profile flags', () => {
  assert.deepEqual(
    parseArgs(
      [
        'electron',
        '.',
        '--dev',
        '--smoke-test',
        '--url=http://127.0.0.1:3000/projects',
        '--user-data-dir=/tmp/vista-profile',
      ],
      'linux',
    ),
    {
      url: 'http://127.0.0.1:3000/projects',
      dev: true,
      smokeTest: true,
      userDataDir: '/tmp/vista-profile',
      startup: false,
      launcher: null,
    },
  );
});

test('a macOS app launch without a URL selects startup mode', () => {
  assert.equal(parseArgs(['VISTA'], 'darwin').startup, true);
});

test('other platforms retain URL-required window mode', () => {
  assert.equal(parseArgs(['VISTA'], 'linux').startup, false);
  assert.equal(parseArgs(['VISTA'], 'win32').startup, false);
});

test('an explicit launcher selects startup mode for development and tests', () => {
  const args = parseArgs(['electron', '.', '--launcher=/tmp/fake-launcher'], 'darwin');
  assert.equal(args.startup, true);
  assert.equal(args.launcher, '/tmp/fake-launcher');
});
