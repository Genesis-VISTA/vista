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
 * @returns {{ database: string, projects: Array<{ id: string, name?: string, files: string }>,
 *   skills: Array<{ name: string, path: string }>,
 *   knowledgeBases: Array<{ slug: string, name?: string, pdfs: string }> }}
 */
function inventory(backupDir) {
  const indexPath = path.join(backupDir, INDEX_NAME);
  if (existsSync(indexPath)) {
    try {
      const index = JSON.parse(readFileSync(indexPath, 'utf8'));
      const list = (/** @type {unknown} */ value) => (Array.isArray(value) ? value : []);
      return {
        database: typeof index.database === 'string' ? index.database : 'vista.db',
        projects: list(index.projects).map((p) => ({ id: String(p.id), name: p.name, files: String(p.files) })),
        skills: list(index.skills).map((s) => ({ name: String(s.name), path: String(s.path) })),
        knowledgeBases: list(index.knowledge_bases).map((k) => ({
          slug: String(k.slug), name: k.name, pdfs: String(k.pdfs),
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

/**
 * Write README.txt into a backup folder, saying where each thing is.
 *
 * @param {string} backupDir
 * @param {{ write?: typeof writeFileSync }} [options] write is for tests
 * @returns {string} the README's path
 */
export function writeReadme(backupDir, { write = writeFileSync } = {}) {
  const { database, projects, skills, knowledgeBases } = inventory(backupDir);
  const lines = [
    'VISTA backup',
    '============',
    '',
    'VISTA moved your local data here when it found it was incompatible with the',
    'version you installed. Nothing was deleted.',
    '',
    `Database: ${database}`,
    '  Your conversations, projects, settings and connections. It opens only with the',
    '  VISTA release that wrote it.',
    '',
    'Knowledge bases (upload the PDFs again from Knowledge Bases):',
    ...(knowledgeBases.length
      ? knowledgeBases.map((k) => `  - ${k.name ? `${k.name} (${k.slug})` : k.slug}: ${k.pdfs}`)
      : ['  none']),
    '',
    'Skills (import again from Skills):',
    ...(skills.length ? skills.map((s) => `  - ${s.name}: ${s.path}`) : ['  none']),
    '',
    'Projects (inputs and outputs):',
    ...(projects.length
      ? projects.map((p) => `  - ${p.name ? `${p.name} (${p.id})` : p.id}: ${p.files}`)
      : ['  none']),
    '',
    'Paths are relative to this folder.',
    '',
    'To go back: install the earlier VISTA release, then move the contents of this',
    'folder back into the VISTA data folder (the folder that holds "backups").',
    '',
    'This folder can be deleted once nothing in it is needed.',
    '',
  ];
  const readme = path.join(backupDir, 'README.txt');
  write(readme, lines.join('\n'), 'utf8');
  return readme;
}

/**
 * The data is moved before the README is written, so a README that cannot be
 * written is reported beside the result and never thrown: the reset happened.
 *
 * @param {string} stateDir
 * @param {{ now?: Date, rename?: typeof renameSync, write?: typeof writeFileSync }} [options]
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
