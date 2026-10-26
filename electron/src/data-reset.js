// @ts-check
// Back up and reset (incompatible-data-reset D5, D6): move a researcher's local
// data out of the state directory into backups/<UTC>/, deleting nothing. Pure
// Node, so it is tested on a temp directory without Electron.
import {
  existsSync, mkdirSync, readdirSync, readFileSync, renameSync, rmdirSync, statSync, writeFileSync,
} from 'node:fs';
import path from 'node:path';

/**
 * What a reset leaves in the state directory: caches that are rebuilt for free
 * or not the researcher's, and the logs. Anything else, known or not, is moved.
 * Where each is configured:
 *   huggingface   HF_HOME (backend config.py)
 *   microsandbox  MSB_HOME (scripts/package_launcher.sh)
 *   vista-data    VISTA_DATA_PAYLOAD_DIR (scripts/package_launcher.sh)
 *   logs          the launchers and main.js (window.log is open during a reset)
 *   backups       this module
 */
export const KEEP = Object.freeze(['backups', 'logs', 'huggingface', 'microsandbox', 'vista-data']);

const INDEX_NAME = 'incompatible-data-index.json';

/**
 * yyyymmddThhmmssZ, in UTC.
 * @param {Date} date
 */
function stamp(date) {
  return date.toISOString().replace(/[-:]/g, '').replace(/\.\d+Z$/, 'Z');
}

/**
 * Move every top-level entry of the state directory except KEEP into a new
 * backups/<UTC>/ folder. On a failed rename, moves back what it moved and throws.
 *
 * @param {string} stateDir
 * @param {{ now?: Date, rename?: typeof renameSync }} [options] rename is for tests
 * @returns {{ backupDir: string, moved: string[] }}
 */
export function backupData(stateDir, { now = new Date(), rename = renameSync } = {}) {
  if (!existsSync(stateDir) || !statSync(stateDir).isDirectory()) {
    throw new Error(`There is nothing to reset: ${stateDir} does not exist.`);
  }
  const entries = readdirSync(stateDir).filter((name) => !KEEP.includes(name));
  if (entries.length === 0) throw new Error(`There is nothing to reset: ${stateDir} holds no data.`);

  const backups = path.join(stateDir, 'backups');
  mkdirSync(backups, { recursive: true });
  const base = stamp(now);
  let backupDir = path.join(backups, base);
  for (let n = 2; ; n += 1) {
    try {
      mkdirSync(backupDir);
      break;
    } catch (error) {
      if (/** @type {NodeJS.ErrnoException} */ (error).code !== 'EEXIST') throw error;
      backupDir = path.join(backups, `${base}-${n}`);
    }
  }

  /** @type {string[]} */
  const moved = [];
  for (const name of entries) {
    try {
      rename(path.join(stateDir, name), path.join(backupDir, name));
      moved.push(name);
    } catch (error) {
      const reason = error instanceof Error ? error.message : String(error);
      /** @type {string[]} */
      const stuck = [];
      for (const done of [...moved].reverse()) {
        try {
          rename(path.join(backupDir, done), path.join(stateDir, done));
        } catch {
          stuck.push(done);
        }
      }
      try {
        rmdirSync(backupDir);
      } catch { /* not empty, or already gone: leave it */ }
      const left = stuck.length ? ` Could not move back: ${stuck.join(', ')}, which are in ${backupDir}.` : '';
      throw new Error(`Could not move "${name}" into the backup (${reason}).${left}`);
    }
  }
  return { backupDir, moved };
}

/**
 * @param {string} dir
 * @returns {string[]} sorted names of the subdirectories, [] when there are none
 */
function subdirectories(dir) {
  try {
    return readdirSync(dir, { withFileTypes: true }).filter((e) => e.isDirectory()).map((e) => e.name).sort();
  } catch {
    return [];
  }
}

/**
 * A skill's name from SKILL.md's front matter.
 * @param {string} skillDir
 * @returns {string} '' when there is none
 */
function skillName(skillDir) {
  try {
    const text = readFileSync(path.join(skillDir, 'SKILL.md'), 'utf8');
    return /^name:\s*(.+?)\s*$/m.exec(text)?.[1]?.replace(/^["']|["']$/g, '') ?? '';
  } catch {
    return '';
  }
}

/**
 * What the backup holds, from the backend's index or else from the disk.
 *
 * @param {string} backupDir
 * @returns {{ database: string,
 *   projects: Array<{ id: string, name?: string, files: string, vista?: boolean }>,
 *   skills: Array<{ name: string, path: string, vista?: boolean }>,
 *   knowledgeBases: Array<{ slug: string, name?: string, pdfs: string, vista?: boolean }> }}
 */
function inventory(backupDir) {
  const indexPath = path.join(backupDir, INDEX_NAME);
  if (existsSync(indexPath)) {
    try {
      const index = JSON.parse(readFileSync(indexPath, 'utf8'));
      const list = (/** @type {unknown} */ value) => (Array.isArray(value) ? value : []);
      const vista = (/** @type {{ comes_with_vista?: unknown }} */ entry) => entry.comes_with_vista === true;
      return {
        database: typeof index.database === 'string' ? index.database : 'vista.db',
        projects: list(index.projects).map((p) => ({
          id: String(p.id), name: p.name, files: String(p.files), vista: vista(p),
        })),
        skills: list(index.skills).map((s) => ({ name: String(s.name), path: String(s.path), vista: vista(s) })),
        knowledgeBases: list(index.knowledge_bases).map((k) => ({
          slug: String(k.slug), name: k.name, pdfs: String(k.pdfs), vista: vista(k),
        })),
      };
    } catch { /* an unreadable index: fall back to the disk */ }
  }
  return {
    database: 'vista.db',
    projects: subdirectories(path.join(backupDir, 'volumes')).map((id) => ({ id, files: `volumes/${id}` })),
    skills: subdirectories(path.join(backupDir, 'storage')).map((id) => ({
      name: skillName(path.join(backupDir, 'storage', id)) || id,
      path: `storage/${id}`,
    })),
    knowledgeBases: subdirectories(path.join(backupDir, 'knowledge-bases')).map((slug) => ({
      slug, pdfs: `knowledge-bases/${slug}/pdfs`,
    })),
  };
}

export const DEFAULT_BACKEND_PORT = 8001;

/**
 * The prompt a researcher pastes into a coding agent to restore their work
 * (incompatible-data-reset D6). It is written to the README verbatim, with the
 * running backend's port filled in.
 *
 * @param {number} [port]
 * @returns {string[]} its lines
 */
export function restorePromptLines(port = DEFAULT_BACKEND_PORT) {
  return [
    'This folder is a backup that VISTA made when it found my local data incompatible with the',
    'version now installed. Read README.txt in this folder first. Then help me restore my own',
    `work into the running VISTA, whose backend API is at http://127.0.0.1:${port} (its schema is at`,
    '/openapi.json).',
    '',
    'Rules:',
    '- Never change, move or delete anything in this backup folder. Read vista.db only from a copy',
    '  you make outside it, with sqlite3.',
    '- Write only through the running VISTA\'s HTTP API. Never edit its database or data folder',
    '  directly.',
    '- Restore only the items README.txt lists as mine, not the ones it says come back with VISTA.',
    '- In this order:',
    '  - knowledge bases: create each and upload its PDFs;',
    '  - skills: import each skill folder;',
    '  - projects: recreate each with its description, instructions, skills and knowledge bases,',
    '    then upload the files from its volumes folder to the project.',
    '- Don\'t restore conversations, settings, API keys or credentials. I\'ll reconnect those in',
    '  Settings.',
    '- Before writing anything, show me what you plan to restore and wait for my go-ahead. At the',
    '  end, tell me what was restored, what wasn\'t, and why.',
  ];
}

/**
 * Write README.txt into a backup folder, saying where each thing is: the
 * researcher's own work first, then what comes back with VISTA.
 *
 * Only entries whose folder is in the backup are listed. An entry is VISTA's
 * only if the backend's index marked it `comes_with_vista`; without an index
 * nothing is marked, and everything found on disk is the researcher's.
 *
 * @param {string} backupDir
 * @param {{ write?: typeof writeFileSync, port?: number }} [options] write is for tests
 * @returns {string} the README's path
 */
export function writeReadme(backupDir, { write = writeFileSync, port = DEFAULT_BACKEND_PORT } = {}) {
  const { database, projects, skills, knowledgeBases } = inventory(backupDir);
  const present = (/** @type {string} */ rel) => existsSync(path.join(backupDir, rel));
  const kbs = knowledgeBases.filter((k) => present(k.pdfs));
  const sks = skills.filter((s) => present(s.path));
  const prjs = projects.filter((p) => present(p.files));
  const mine = (/** @type {{ vista?: boolean }} */ e) => !e.vista;
  const theirs = (/** @type {{ vista?: boolean }} */ e) => e.vista === true;

  const section = (
    /** @type {string} */ title, /** @type {string[]} */ items,
  ) => [title, ...(items.length ? items.map((i) => `  - ${i}`) : ['  none found'])];
  const kbLine = (/** @type {typeof kbs[number]} */ k) => `${k.name ? `${k.name} (${k.slug})` : k.slug}: ${k.pdfs}`;
  const skLine = (/** @type {typeof sks[number]} */ s) => `${s.name}: ${s.path}`;
  const prjLine = (/** @type {typeof prjs[number]} */ p) => `${p.name ? `${p.name} (${p.id})` : p.id}: ${p.files}`;

  const vistaNames = [
    ['Knowledge bases', kbs.filter(theirs).map((k) => k.name || k.slug)],
    ['Skills', sks.filter(theirs).map((s) => s.name)],
    ['Projects', prjs.filter(theirs).map((p) => p.name || p.id)],
  ].filter(([, names]) => names.length);

  const lines = [
    'VISTA backup',
    '============',
    '',
    'VISTA moved your local data here when it found it was incompatible with the',
    'version you installed. Nothing was deleted. A coding agent such as Claude Code or',
    'Codex can help restore your work: paste the prompt below into it.',
    '',
    'Prompt for a coding agent:',
    '',
    ...restorePromptLines(port).map((line) => (line ? `    ${line}` : '')),
    '',
    'Your work',
    '---------',
    ...section('Knowledge bases (upload the PDFs again from Knowledge Bases):', kbs.filter(mine).map(kbLine)),
    ...section('Skills (import again from Skills):', sks.filter(mine).map(skLine)),
    ...section('Projects (inputs and outputs):', prjs.filter(mine).map(prjLine)),
  ];
  if (vistaNames.length) {
    lines.push(
      '',
      'Comes back with VISTA (nothing to restore)',
      '------------------------------------------',
      ...vistaNames.map(([title, names]) => `  ${title}: ${[...names].join(', ')}`),
    );
  }
  lines.push(
    '',
    `Database: ${database}`,
    '  Your conversations, projects, settings and connections. It opens only with the',
    '  VISTA release that wrote it.',
    '',
    'Paths are relative to this folder.',
    '',
    'To go back: install the earlier VISTA release, then move the contents of this',
    'folder back into the VISTA data folder (the folder that holds "backups").',
    '',
    'This folder can be deleted once nothing in it is needed.',
    '',
  );
  const readme = path.join(backupDir, 'README.txt');
  write(readme, lines.join('\n'), 'utf8');
  return readme;
}

/**
 * The data is moved before the README is written, so a README that cannot be
 * written is reported beside the result and never thrown: the reset happened.
 *
 * @param {string} stateDir
 * @param {{ now?: Date, rename?: typeof renameSync, write?: typeof writeFileSync, port?: number }} [options]
 * @returns {{ backupDir: string, moved: string[], readme: string | null, readmeError?: string }}
 */
export function resetData(stateDir, options) {
  const { backupDir, moved } = backupData(stateDir, options);
  try {
    return { backupDir, moved, readme: writeReadme(backupDir, options) };
  } catch (error) {
    return { backupDir, moved, readme: null, readmeError: error instanceof Error ? error.message : String(error) };
  }
}
