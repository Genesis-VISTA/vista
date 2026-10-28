// @ts-check
import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  existsSync, mkdirSync, mkdtempSync, readdirSync, readFileSync, renameSync, rmSync, writeFileSync,
} from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';

import { backupData, KEEP, resetData, restorePromptLines, writeReadme } from '../src/data-reset.js';

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
    'storage', 'vista-data', 'vista.db', 'vista.db-wal', 'volumes',
  ]);
  assert.deepEqual([...moved].sort(), names(backupDir));
  assert.equal(readFileSync(path.join(backupDir, 'vista.db'), 'utf8'), 'vista.db');
  assert.ok(existsSync(path.join(dir, 'logs', 'window.log')));
  assert.ok(existsSync(path.join(dir, 'huggingface', 'model.bin')));
  rmSync(dir, { recursive: true, force: true });
});

test('the keep-list is frozen', () => {
  assert.ok(Object.isFrozen(KEEP));
  assert.deepEqual([...KEEP].sort(), ['backups', 'huggingface', 'logs', 'microsandbox']);
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

/** A state directory whose index marks some entries as VISTA's own. */
function makeIndexed() {
  const { dir, put } = makeState();
  put('knowledge-bases/molten-salt-papers/pdfs/b.pdf');
  put('storage/aaaa/SKILL.md', '---\nname: salt-analysis\n---\n');
  put('volumes/p-vista/out.txt');
  put('incompatible-data-index.json', JSON.stringify({
    database: 'vista.db',
    projects: [
      { id: 'p-vista', name: 'molten-salt', files: 'volumes/p-vista', comes_with_vista: true },
      { id: 'p-1', name: 'Salt study', files: 'volumes/p-1', comes_with_vista: false },
      { id: 'p-gone', name: 'Never had files', files: 'volumes/p-gone', comes_with_vista: false },
    ],
    skills: [
      { name: 'salt-analysis', path: 'storage/aaaa', comes_with_vista: true },
      { name: 'salt-density', path: 'storage/9f1c', comes_with_vista: false },
    ],
    knowledge_bases: [
      { slug: 'molten-salt-papers', name: 'Molten Salt Papers', pdfs: 'knowledge-bases/molten-salt-papers/pdfs', comes_with_vista: true },
      { slug: 'molten-salts', name: 'Molten salts', pdfs: 'knowledge-bases/molten-salts/pdfs', comes_with_vista: false },
      { slug: 'kb-gone', name: 'Gone', pdfs: 'knowledge-bases/kb-gone/pdfs', comes_with_vista: false },
    ],
  }));
  return dir;
}

test('the README lists the researcher\'s own work first, VISTA\'s apart, from the backend index', () => {
  const dir = makeIndexed();
  const { backupDir, readme } = resetData(dir, { now: NOW });
  assert.equal(readme, path.join(backupDir, 'README.txt'));
  const text = readFileSync(String(readme), 'utf8');
  for (const expected of [
    'Database: vista.db', 'Molten salts (molten-salts): knowledge-bases/molten-salts/pdfs',
    'salt-density: storage/9f1c', 'Salt study (p-1): volumes/p-1',
    'install the earlier VISTA release', 'can be deleted', 'Paths are relative to this folder',
  ]) assert.ok(text.includes(expected), `README lacks "${expected}"`);

  const work = text.indexOf('\nYour work\n');
  const comes = text.indexOf('\nComes back with VISTA (nothing to restore)\n');
  assert.ok(work > 0 && comes > work, 'own work, then VISTA\'s');
  const own = text.slice(work, comes);
  const vista = text.slice(comes, text.indexOf('\nDatabase:'));
  assert.ok(own.includes('salt-density') && own.includes('Salt study') && own.includes('Molten salts'));
  assert.ok(!own.includes('salt-analysis') && !own.includes('molten-salt-papers') && !own.includes('(p-vista)'));
  assert.ok(vista.includes('Skills: salt-analysis'));
  assert.ok(vista.includes('Knowledge bases: Molten Salt Papers'));
  assert.ok(vista.includes('Projects: molten-salt'));
  assert.ok(!vista.includes('salt-density'));
  assert.ok(text.indexOf('Database:') > comes, 'the database line follows the lists');
  assert.ok(existsSync(path.join(backupDir, 'incompatible-data-index.json')), 'the index moves too');
  rmSync(dir, { recursive: true, force: true });
});

test('a listed folder that is not in the backup is left out', () => {
  const dir = makeIndexed();
  const { readme } = resetData(dir, { now: NOW });
  const text = readFileSync(String(readme), 'utf8');
  assert.ok(!text.includes('Never had files') && !text.includes('p-gone'));
  assert.ok(!text.includes('kb-gone') && !text.includes('Gone'));
  rmSync(dir, { recursive: true, force: true });
});

test('without an index everything found on disk is the researcher\'s: skills by SKILL.md, projects by id', () => {
  const { dir } = makeState();
  mkdirSync(path.join(dir, 'storage', 'no-skill-file'));
  const { readme } = resetData(dir, { now: NOW });
  const text = readFileSync(String(readme), 'utf8');
  assert.ok(text.includes('salt-density: storage/9f1c'));
  assert.ok(text.includes('no-skill-file: storage/no-skill-file'));
  assert.ok(text.includes('p-1: volumes/p-1'));
  assert.ok(text.includes('molten-salts: knowledge-bases/molten-salts/pdfs'));
  assert.ok(!text.includes('Comes back with VISTA'));
  rmSync(dir, { recursive: true, force: true });
});

test('an index with empty lists, and an unreadable one, read as none found / the disk', () => {
  const dir = mkdtempSync(path.join(tmpdir(), 'vista-reset-'));
  writeFileSync(path.join(dir, 'incompatible-data-index.json'),
    '{"database":"vista.db","projects":[],"skills":[],"knowledge_bases":[]}', 'utf8');
  const text = readFileSync(writeReadme(dir), 'utf8');
  assert.equal((text.match(/^ {2}- none found$/gm) ?? []).length, 0);
  assert.equal((text.match(/^ {2}none found$/gm) ?? []).length, 3);
  assert.ok(!text.includes('Comes back with VISTA'));
  rmSync(dir, { recursive: true, force: true });
});

test('the README begins with the restore prompt, naming the API and carrying each rule', () => {
  const { dir } = makeState();
  const { readme } = resetData(dir, { now: NOW });
  const text = readFileSync(String(readme), 'utf8');
  const prompt = text.indexOf('This folder is a backup that VISTA made');
  assert.ok(prompt > 0, 'the prompt is in the README');
  assert.ok(prompt < text.indexOf('Your work'), 'the prompt comes before the lists');
  assert.ok(text.indexOf('coding agent') < prompt, 'a short paragraph opens it');
  assert.ok(text.includes('http://127.0.0.1:8001 (its schema is at'));
  assert.ok(text.includes('/openapi.json'));
  for (const rule of [
    'Never change, move or delete anything in this backup folder',
    'Write only through the running VISTA\'s HTTP API',
    'Restore only the items README.txt lists as mine, not the ones it says come back with VISTA',
    'knowledge bases: create each and upload its PDFs',
    'skills: import each skill folder',
    'projects: recreate each with its description, instructions, skills and knowledge bases,',
    'then upload the files from its volumes folder to the project.',
    'Don\'t restore conversations, settings, API keys or credentials',
    'show me what you plan to restore and wait for my go-ahead',
    'remind me to reload the VISTA',
  ]) assert.ok(text.includes(rule), `the prompt lacks "${rule}"`);
  assert.ok(!text.includes('conversations: recreate'), 'conversations are not restored');
  const notRestored = text.indexOf('Not restored: your inference provider, API keys and cluster connections.');
  assert.ok(notRestored > text.indexOf('Your work'), 'the README tells the researcher to reconnect, after their work');
  assert.ok(text.includes('Set them up again in Settings.'));
  rmSync(dir, { recursive: true, force: true });
});

test('the prompt takes the backend port it is given', () => {
  const { dir } = makeState();
  const { readme } = resetData(dir, { now: NOW, port: 18001 });
  const text = readFileSync(String(readme), 'utf8');
  assert.ok(text.includes('http://127.0.0.1:18001 (its schema'));
  assert.ok(!text.includes(':8001'));
  assert.ok(restorePromptLines().join('\n').includes('http://127.0.0.1:8001'));
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
