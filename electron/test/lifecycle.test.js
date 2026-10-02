// @ts-check
import { test } from 'node:test';
import assert from 'node:assert/strict';

import { acquireSingleInstanceLock, focusWindow } from '../src/lifecycle.js';

test('visible modes acquire the single-instance lock', () => {
  let calls = 0;
  const application = { requestSingleInstanceLock: () => (calls += 1) === 1 };
  assert.equal(acquireSingleInstanceLock(application, { smokeTest: false }), true);
  assert.equal(acquireSingleInstanceLock(application, { smokeTest: false }), false);
  assert.equal(calls, 2);
});

test('smoke tests remain independent of the single-instance lock', () => {
  const application = {
    requestSingleInstanceLock: () => {
      throw new Error('smoke test requested the lock');
    },
  };
  assert.equal(acquireSingleInstanceLock(application, { smokeTest: true }), true);
});

test('a second launch restores and focuses the active window', () => {
  /** @type {string[]} */
  const calls = [];
  const window = {
    isMinimized: () => true,
    restore: () => calls.push('restore'),
    show: () => calls.push('show'),
    focus: () => calls.push('focus'),
  };
  focusWindow(window);
  assert.deepEqual(calls, ['restore', 'show', 'focus']);
});
