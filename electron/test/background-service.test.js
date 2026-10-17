// @ts-check
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import http from 'node:http';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';

import { findRunningService, loopbackUrl } from '../src/background-service.js';

/** @param {unknown} body */
const described = (body) => ({ read: async () => JSON.stringify(body) });
const alive = { isAlive: () => true, isAnswering: async () => true };

test('a live, answering service is opened', async () => {
  const url = await findRunningService('/state', {
    ...described({ pid: 4242, url: 'http://127.0.0.1:3000', version: '1' }),
    ...alive,
  });
  assert.equal(url, 'http://127.0.0.1:3000');
});

test('no file, or one that is not JSON, starts VISTA as usual', async () => {
  const missing = { read: async () => { throw Object.assign(new Error('x'), { code: 'ENOENT' }); } };
  assert.equal(await findRunningService('/state', { ...missing, ...alive }), null);
  assert.equal(await findRunningService('/state', { read: async () => '{', ...alive }), null);
});

test('a file left by a dead process is ignored', async () => {
  /** @type {number[]} */
  const pids = [];
  const url = await findRunningService('/state', {
    ...described({ pid: 4242, url: 'http://127.0.0.1:3000' }),
    isAlive: (pid) => { pids.push(pid); return false; },
    isAnswering: async () => assert.fail('a dead service is not asked'),
  });
  assert.equal(url, null);
  assert.deepEqual(pids, [4242]);
});

test('a live process whose address does not answer is ignored', async () => {
  const url = await findRunningService('/state', {
    ...described({ pid: 4242, url: 'http://127.0.0.1:3000' }),
    isAlive: () => true,
    isAnswering: async () => false,
  });
  assert.equal(url, null);
});

test('only a loopback http address is believed', async () => {
  assert.equal(loopbackUrl('http://127.0.0.1:3000/x'), 'http://127.0.0.1:3000');
  assert.equal(loopbackUrl('http://localhost:3000'), 'http://localhost:3000');
  for (const url of [
    'https://127.0.0.1:3000',
    'http://evil.example:3000',
    'http://user:pw@127.0.0.1:3000',
    'file:///etc/passwd',
    'javascript:alert(1)',
    42,
    null,
  ]) {
    assert.equal(loopbackUrl(url), null, String(url));
  }
  for (const pid of [0, -1, 1.5, '4242', null]) {
    assert.equal(
      await findRunningService('/state', { ...described({ pid, url: 'http://127.0.0.1:3000' }), ...alive }),
      null,
      `pid ${pid}`,
    );
  }
});

test('the real checks: this process, and a server that answers', async (t) => {
  const dir = mkdtempSync(path.join(os.tmpdir(), 'vista-service-'));
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  const server = http.createServer((_, res) => { res.writeHead(307, { location: '/x' }); res.end(); });
  await new Promise((resolve) => server.listen(0, '127.0.0.1', () => resolve(undefined)));
  t.after(() => server.close());
  const address = /** @type {import('node:net').AddressInfo} */ (server.address());
  const url = `http://127.0.0.1:${address.port}`;

  writeFileSync(path.join(dir, 'service.json'), JSON.stringify({ pid: process.pid, url }));
  assert.equal(await findRunningService(dir), url);

  server.close();
  await new Promise((resolve) => server.on('close', () => resolve(undefined)));
  assert.equal(await findRunningService(dir), null, 'the server is gone');
});
