// @ts-check
//
// The ZIP that Open Logs leaves in the logs folder, so a researcher can send
// one file rather than select and compress the folder themselves. Written
// here rather than by a dependency or the OS: Windows, macOS and Linux have no
// one zip command in common, and the format needs only zlib's deflate and CRC.

import { readdirSync, readFileSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { crc32, deflateRawSync } from 'node:zlib';

const ARCHIVE_PREFIX = 'vista-logs-';
// General purpose flag bit 11: names are UTF-8.
const UTF8_NAMES = 0x0800;
const DEFLATE = 8;
const ZIP_VERSION = 20;

/** @param {number} n */
const pad = (n) => String(n).padStart(2, '0');

/** @param {Date} date */
function archiveName(date) {
  return `${ARCHIVE_PREFIX}${date.getFullYear()}${pad(date.getMonth() + 1)}${pad(date.getDate())}`
    + `-${pad(date.getHours())}${pad(date.getMinutes())}${pad(date.getSeconds())}.zip`;
}

/** MS-DOS time and date, the only timestamps a plain ZIP entry carries. @param {Date} date */
function dosTime(date) {
  return {
    time: (date.getHours() << 11) | (date.getMinutes() << 5) | (date.getSeconds() >> 1),
    date: ((Math.max(date.getFullYear(), 1980) - 1980) << 9) | ((date.getMonth() + 1) << 5) | date.getDate(),
  };
}

/**
 * A ZIP of `entries`, each deflated.
 * @param {{ name: string, data: Buffer }[]} entries
 * @param {Date} date
 */
export function zip(entries, date) {
  const { time, date: day } = dosTime(date);
  /** @type {Buffer[]} */
  const local = [];
  /** @type {Buffer[]} */
  const central = [];
  let offset = 0;
  for (const entry of entries) {
    const name = Buffer.from(entry.name, 'utf8');
    const data = deflateRawSync(entry.data);
    const crc = crc32(entry.data);

    const header = Buffer.alloc(30);
    header.writeUInt32LE(0x04034b50, 0);
    header.writeUInt16LE(ZIP_VERSION, 4);
    header.writeUInt16LE(UTF8_NAMES, 6);
    header.writeUInt16LE(DEFLATE, 8);
    header.writeUInt16LE(time, 10);
    header.writeUInt16LE(day, 12);
    header.writeUInt32LE(crc, 14);
    header.writeUInt32LE(data.length, 18);
    header.writeUInt32LE(entry.data.length, 22);
    header.writeUInt16LE(name.length, 26);
    local.push(header, name, data);

    const record = Buffer.alloc(46);
    record.writeUInt32LE(0x02014b50, 0);
    record.writeUInt16LE(ZIP_VERSION, 4);
    record.writeUInt16LE(ZIP_VERSION, 6);
    record.writeUInt16LE(UTF8_NAMES, 8);
    record.writeUInt16LE(DEFLATE, 10);
    record.writeUInt16LE(time, 12);
    record.writeUInt16LE(day, 14);
    record.writeUInt32LE(crc, 16);
    record.writeUInt32LE(data.length, 20);
    record.writeUInt32LE(entry.data.length, 24);
    record.writeUInt16LE(name.length, 28);
    record.writeUInt32LE(offset, 42);
    central.push(record, name);

    offset += header.length + name.length + data.length;
  }
  const directory = Buffer.concat(central);
  const end = Buffer.alloc(22);
  end.writeUInt32LE(0x06054b50, 0);
  end.writeUInt16LE(entries.length, 8);
  end.writeUInt16LE(entries.length, 10);
  end.writeUInt32LE(directory.length, 12);
  end.writeUInt32LE(offset, 16);
  return Buffer.concat([...local, directory, end]);
}

/**
 * Write a ZIP of every log in `logsDirectory`, plus `diagnostics` as
 * diagnostics.txt, into that folder; returns its path. Earlier archives are
 * left out, so they do not nest.
 *
 * @param {string} logsDirectory
 * @param {string} diagnostics
 * @param {Date} [date]
 */
export function writeLogArchive(logsDirectory, diagnostics, date = new Date()) {
  const entries = readdirSync(logsDirectory, { withFileTypes: true })
    .filter((entry) => entry.isFile() && !entry.name.startsWith(ARCHIVE_PREFIX))
    .map((entry) => ({ name: entry.name, data: readFileSync(path.join(logsDirectory, entry.name)) }));
  entries.push({ name: 'diagnostics.txt', data: Buffer.from(`${diagnostics}\n`, 'utf8') });
  const archive = path.join(logsDirectory, archiveName(date));
  writeFileSync(archive, zip(entries, date));
  return archive;
}
