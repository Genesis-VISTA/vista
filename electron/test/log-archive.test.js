// @ts-check
import assert from 'node:assert/strict';
import { mkdirSync, mkdtempSync, readdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { test } from 'node:test';
import { crc32, inflateRawSync } from 'node:zlib';

import { writeLogArchive } from '../src/log-archive.js';

/** Read a ZIP back through its central directory. @param {Buffer} zip */
function unzip(zip) {
  const end = zip.length - 22;
  assert.equal(zip.readUInt32LE(end), 0x06054b50);
  const count = zip.readUInt16LE(end + 10);
  let at = zip.readUInt32LE(end + 16);
  /** @type {Record<string, string>} */
  const files = {};
  for (let i = 0; i < count; i++) {
    assert.equal(zip.readUInt32LE(at), 0x02014b50);
    const size = zip.readUInt32LE(at + 20);
    const nameLength = zip.readUInt16LE(at + 28);
    const offset = zip.readUInt32LE(at + 42);
    const name = zip.toString('utf8', at + 46, at + 46 + nameLength);
    const dataStart = offset + 30 + zip.readUInt16LE(offset + 26) + zip.readUInt16LE(offset + 28);
    const data = inflateRawSync(zip.subarray(dataStart, dataStart + size));
    assert.equal(crc32(data), zip.readUInt32LE(at + 16), `${name} checksum`);
    files[name] = data.toString('utf8');
    at += 46 + nameLength;
  }
  return files;
}

test('archives every log and the diagnostics into one ZIP in the logs folder', () => {
  const logs = mkdtempSync(path.join(tmpdir(), 'vista-log-archive-'));
  writeFileSync(path.join(logs, 'mcp.log'), 'RAG: loading embedding model\n'.repeat(500), 'utf8');
  writeFileSync(path.join(logs, 'window.log'), 'startup: mcp failed\n', 'utf8');
  writeFileSync(path.join(logs, 'vista-logs-20260101-000000.zip'), 'an earlier archive', 'utf8');
  mkdirSync(path.join(logs, 'nested'));

  const archive = writeLogArchive(logs, 'VISTA 0.1.4\nCode: health-timeout', new Date(2026, 9, 28, 14, 5, 9));

  assert.equal(archive, path.join(logs, 'vista-logs-20261028-140509.zip'));
  assert.deepEqual(unzip(readFileSync(archive)), {
    'mcp.log': 'RAG: loading embedding model\n'.repeat(500),
    'window.log': 'startup: mcp failed\n',
    'diagnostics.txt': 'VISTA 0.1.4\nCode: health-timeout\n',
  });
  assert.equal(readdirSync(logs).filter((name) => name.endsWith('.zip')).length, 2);
  rmSync(logs, { recursive: true, force: true });
});

test('a missing logs folder throws, so Open Logs falls back to the folder', () => {
  assert.throws(() => writeLogArchive(path.join(tmpdir(), 'vista-no-such-logs'), ''));
});
