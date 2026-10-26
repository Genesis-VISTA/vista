// @ts-check
import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  existsSync, mkdirSync, mkdtempSync, readdirSync, readFileSync, renameSync, rmSync, writeFileSync,
} from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';

import { backupData, KEEP, resetData, writeReadme } from '../src/data-reset.js';

const NOW = new Date('2026-10-26T14:03:09.123Z');

/** A state directory shaped like a real install. */
function makeState() {
  const dir = mkdtempSync(path.join(tmpdir(), 'vista-reset-'));
  const put = (/** @type {string} */ rel, text = rel) => {
    mkdirSync(path.dirname(path.join(dir, rel)), { recursive: true });
    writeFileSync(path.join(dir, rel), text, 'utf8');
  };
  put('vista.db');
  put('vista.db-wal');
  put('knowledge-bases/molten-salts/pdfs/a.pdf');
  put('storage/9f1c/SKILL.md', '---\nname: salt-density\ndescription: x\n---\nbody\n');
  put('volumes/p-1/inputs/in.csv');
  put('forum-git/p-1/repo.git/HEAD');
  put('hpc_job_registry.json');
  put('some-future-folder/x.txt');
  put('huggingface/model.bin');
  put('microsandbox/runtime');
  put('vista-data/payload');
  put('logs/window.log');
  return { dir, put };
}

/** @param {string} dir */
const names = (dir) => readdirSync(dir).sort();

test('moves everything except the keep-list, unknown entries included', () => {
  const { dir } = makeState();
  const { backupDir, moved } = backupData(dir, { now: NOW });
  assert.equal(path.basename(backupDir), '20261026T140309Z');
  assert.equal(path.dirname(backupDir), path.join(dir, 'backups'));
  assert.deepEqual(names(dir), [...KEEP].sort());
  assert.deepEqual(names(backupDir), [
    'forum-git', 'hpc_job_registry.json', 'knowledge-bases', 'some-future-folder',
    'storage', 'vista.db', 'vista.db-wal', 'volumes',
  ]);
  assert.deepEqual([...moved].sort(), names(backupDir));
  assert.equal(readFileSync(path.join(backupDir, 'vista.db'), 'utf8'), 'vista.db');
  assert.ok(existsSync(path.join(dir, 'logs', 'window.log')));
  assert.ok(existsSync(path.join(dir, 'huggingface', 'model.bin')));
  rmSync(dir, { recursive: true, force: true });
});

test('the keep-list is frozen', () => {
  assert.ok(Object.isFrozen(KEEP));
  assert.deepEqual([...KEEP].sort(), ['backups', 'huggingface', 'logs', 'microsandbox', 'vista-data']);
});

test('a second reset is made beside the first, even in the same second', () => {
  const { dir, put } = makeState();
  const first = backupData(dir, { now: NOW });
  put('vista.db');
  put('volumes/p-2/out.txt');
  const second = backupData(dir, { now: NOW });
  put('vista.db');
  const third = backupData(dir, { now: new Date('2026-10-27T01:00:00Z') }).backupDir;
  assert.equal(path.basename(second.backupDir), '20261026T140309Z-2');
  assert.equal(path.basename(third), '20261027T010000Z');
  assert.ok(existsSync(path.join(first.backupDir, 'volumes', 'p-1', 'inputs', 'in.csv')), 'first untouched');
  assert.ok(existsSync(path.join(second.backupDir, 'volumes', 'p-2', 'out.txt')));
  assert.deepEqual(names(path.join(dir, 'backups')), [
    '20261026T140309Z', '20261026T140309Z-2', '20261027T010000Z',
  ]);
  rmSync(dir, { recursive: true, force: true });
});

test('a failed rename moves everything back and says which entry failed', () => {
  const { dir } = makeState();
  const before = names(dir);
  let calls = 0;
  /** @type {typeof renameSync} */
  const flaky = (from, to) => {
    calls += 1;
    if (String(from).endsWith('storage')) throw new Error('EBUSY: resource busy');
    renameSync(from, to);
  };
  assert.throws(
    () => backupData(dir, { now: NOW, rename: flaky }),
    /Could not move "storage" into the backup \(EBUSY/,
  );
  assert.ok(calls > 2, 'some entries moved before the failure');
  assert.deepEqual(names(dir), [...before, 'backups'].sort());
  assert.deepEqual(names(path.join(dir, 'backups')), [], 'the empty backup folder is removed');
  assert.equal(readFileSync(path.join(dir, 'vista.db'), 'utf8'), 'vista.db');
  assert.ok(existsSync(path.join(dir, 'volumes', 'p-1', 'inputs', 'in.csv')));
  rmSync(dir, { recursive: true, force: true });
});

test('nothing to reset is an error, not an empty backup', () => {
  const missing = path.join(tmpdir(), `vista-reset-missing-${process.pid}`);
  assert.throws(() => backupData(missing), /nothing to reset/);
  const dir = mkdtempSync(path.join(tmpdir(), 'vista-reset-'));
  mkdirSync(path.join(dir, 'logs'));
  mkdirSync(path.join(dir, 'huggingface'));
  assert.throws(() => backupData(dir), /nothing to reset/);
  assert.equal(existsSync(path.join(dir, 'backups')), false);
  rmSync(dir, { recursive: true, force: true });
});

test('the README names everything and where it is, from the backend index', () => {
  const { dir, put } = makeState();
  put('incompatible-data-index.json', JSON.stringify({
    database: 'vista.db',
    projects: [{ id: 'p-1', name: 'Salt study', files: 'volumes/p-1' }],
    skills: [{ name: 'salt-density', path: 'storage/9f1c' }],
    knowledge_bases: [{ slug: 'molten-salts', name: 'Molten salts', pdfs: 'knowledge-bases/molten-salts/pdfs' }],
  }));
  const { backupDir, readme } = resetData(dir, { now: NOW });
  assert.equal(readme, path.join(backupDir, 'README.txt'));
  const text = readFileSync(String(readme), 'utf8');
  for (const expected of [
    'Database: vista.db', 'Molten salts (molten-salts): knowledge-bases/molten-salts/pdfs',
    'salt-density: storage/9f1c', 'Salt study (p-1): volumes/p-1',
    'install the earlier VISTA release', 'can be deleted',
  ]) assert.ok(text.includes(expected), `README lacks "${expected}"`);
  assert.ok(existsSync(path.join(backupDir, 'incompatible-data-index.json')), 'the index moves too');
  rmSync(dir, { recursive: true, force: true });
});

test('without an index the README reads the disk: skills by SKILL.md, projects by id', () => {
  const { dir } = makeState();
  mkdirSync(path.join(dir, 'storage', 'no-skill-file'));
  const { readme } = resetData(dir, { now: NOW });
  const text = readFileSync(String(readme), 'utf8');
  assert.ok(text.includes('salt-density: storage/9f1c'));
  assert.ok(text.includes('no-skill-file: storage/no-skill-file'));
  assert.ok(text.includes('p-1: volumes/p-1'));
  assert.ok(text.includes('molten-salts: knowledge-bases/molten-salts/pdfs'));
  rmSync(dir, { recursive: true, force: true });
});

test('an index with an empty list reads as none', () => {
  const dir = mkdtempSync(path.join(tmpdir(), 'vista-reset-'));
  writeFileSync(path.join(dir, 'incompatible-data-index.json'),
    '{"database":"vista.db","projects":[],"skills":[],"knowledge_bases":[]}', 'utf8');
  const text = readFileSync(writeReadme(dir), 'utf8');
  assert.equal((text.match(/^ {2}none$/gm) ?? []).length, 3);
  rmSync(dir, { recursive: true, force: true });
});

test('a README that cannot be written leaves the data moved and is reported, not thrown', () => {
  const { dir } = makeState();
  /** @type {typeof writeFileSync} */
  const full = () => { throw new Error('ENOSPC: no space left on device'); };
  const result = resetData(dir, { now: NOW, write: full });
  assert.equal(result.readme, null);
  assert.match(result.readmeError ?? '', /ENOSPC/);
  assert.ok(existsSync(path.join(result.backupDir, 'vista.db')));
  assert.equal(existsSync(path.join(dir, 'vista.db')), false);
  rmSync(dir, { recursive: true, force: true });
});
