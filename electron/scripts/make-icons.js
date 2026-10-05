// @ts-check
//
// Make every icon file from assets/icon.svg. Run it after changing the SVG:
//
//   npm run icons
//
// It runs inside Electron, which is already a devDependency, so it needs no
// image tool: Chromium draws the SVG onto a canvas at each size, and the PNG,
// ICO and ICNS containers are written here. Its output is committed, so builds
// never run it.
//
//   assets/icon.png      1024 px, the Linux window icon (main.js)
//   assets/icon.ico      Windows: VISTA.exe and the Start menu entry
//   assets/icon.icns     macOS: VISTA.app
//   ../ui/app/favicon.ico and ../ui/app/icon.svg, the UI's own
import { app, BrowserWindow } from 'electron';
import { copyFileSync, readFileSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const APP_DIR = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const ASSETS = path.join(APP_DIR, 'assets');
const UI_APP = path.join(APP_DIR, '..', 'ui', 'app');
const SVG = path.join(ASSETS, 'icon.svg');

// macOS draws app icons on a fixed grid: the shape fills 824 of 1024 px and
// the rest is margin, so a full-bleed tile would sit larger than every other
// icon in the Dock. Windows and Linux icons fill their canvas.
const MAC_SCALE = 824 / 1024;

/**
 * One rendering: a PNG, and the raw RGBA pixels for the ICO's small sizes.
 * @typedef {{ size: number, png: Buffer, rgba: Buffer }} Rendering
 */

/**
 * @param {BrowserWindow} win
 * @param {number} size
 * @param {number} scale
 * @returns {Promise<Rendering>}
 */
async function render(win, size, scale) {
  const svg = readFileSync(SVG, 'utf8');
  const url = `data:image/svg+xml;base64,${Buffer.from(svg).toString('base64')}`;
  /** @type {{ png: string, rgba: string }} */
  const out = await win.webContents.executeJavaScript(`(async () => {
    const img = new Image();
    img.src = ${JSON.stringify(url)};
    await img.decode();
    const canvas = document.createElement('canvas');
    canvas.width = canvas.height = ${size};
    const ctx = canvas.getContext('2d');
    ctx.imageSmoothingQuality = 'high';
    const drawn = ${size} * ${scale};
    const inset = (${size} - drawn) / 2;
    ctx.drawImage(img, inset, inset, drawn, drawn);
    const pixels = ctx.getImageData(0, 0, ${size}, ${size}).data;
    let binary = '';
    for (let i = 0; i < pixels.length; i += 0x8000) {
      binary += String.fromCharCode.apply(null, pixels.subarray(i, i + 0x8000));
    }
    return { png: canvas.toDataURL('image/png').split(',')[1], rgba: btoa(binary) };
  })()`);
  return { size, png: Buffer.from(out.png, 'base64'), rgba: Buffer.from(out.rgba, 'base64') };
}

/**
 * An ICO image as a 32-bit DIB: bottom-up BGRA rows, then an all-clear AND mask
 * (the alpha channel does the masking). Small sizes are stored this way rather
 * than as PNG, which some Windows resource tools still mishandle below 256 px.
 * @param {Rendering} r
 */
function dib({ size, rgba }) {
  const header = Buffer.alloc(40);
  header.writeUInt32LE(40, 0);
  header.writeInt32LE(size, 4);
  header.writeInt32LE(size * 2, 8); // the XOR image and the AND mask, stacked
  header.writeUInt16LE(1, 12);
  header.writeUInt16LE(32, 14);
  const pixels = Buffer.alloc(size * size * 4);
  for (let y = 0; y < size; y++) {
    for (let x = 0; x < size; x++) {
      const from = (y * size + x) * 4;
      const to = ((size - 1 - y) * size + x) * 4;
      pixels[to] = rgba[from + 2];
      pixels[to + 1] = rgba[from + 1];
      pixels[to + 2] = rgba[from];
      pixels[to + 3] = rgba[from + 3];
    }
  }
  const mask = Buffer.alloc(Math.ceil(size / 32) * 4 * size);
  return Buffer.concat([header, pixels, mask]);
}

/** @param {Rendering[]} renderings */
function ico(renderings) {
  const images = renderings.map((r) => (r.size >= 256 ? r.png : dib(r)));
  const header = Buffer.alloc(6);
  header.writeUInt16LE(1, 2);
  header.writeUInt16LE(renderings.length, 4);
  let offset = 6 + 16 * renderings.length;
  const entries = renderings.map((r, i) => {
    const entry = Buffer.alloc(16);
    entry.writeUInt8(r.size >= 256 ? 0 : r.size, 0); // 0 means 256
    entry.writeUInt8(r.size >= 256 ? 0 : r.size, 1);
    entry.writeUInt16LE(1, 4);
    entry.writeUInt16LE(32, 6);
    entry.writeUInt32LE(images[i].length, 8);
    entry.writeUInt32LE(offset, 12);
    offset += images[i].length;
    return entry;
  });
  return Buffer.concat([header, ...entries, ...images]);
}

// ICNS element types that hold a PNG, by pixel size. The @2x types repeat a
// size under a second name so Retina displays pick the sharper one. There is
// no 1x 16 or 32: Apple stores those as run-length ARGB (ic04, ic05), and the
// PNG types meant for them (icp4, icp5) come back garbled from iconutil, so
// macOS scales them down from ic11 and ic12 instead.
const ICNS_TYPES = /** @type {const} */ ([
  ['ic07', 128], ['ic08', 256], ['ic09', 512], ['ic10', 1024],
  ['ic11', 32], ['ic12', 64], ['ic13', 256], ['ic14', 512],
]);

/** @param {Map<number, Rendering>} bySize */
function icns(bySize) {
  const elements = ICNS_TYPES.map(([type, size]) => {
    const png = /** @type {Rendering} */ (bySize.get(size)).png;
    const head = Buffer.alloc(8);
    head.write(type, 0, 'ascii');
    head.writeUInt32BE(8 + png.length, 4);
    return Buffer.concat([head, png]);
  });
  const head = Buffer.alloc(8);
  head.write('icns', 0, 'ascii');
  head.writeUInt32BE(8 + elements.reduce((n, e) => n + e.length, 0), 4);
  return Buffer.concat([head, ...elements]);
}

app.dock?.hide();

// Not a top-level await: in an ES module main script Electron emits `ready`
// only after the module has finished evaluating, so awaiting it there hangs.
app.whenReady().then(async () => {
  const win = new BrowserWindow({ show: false, width: 64, height: 64 });
  await win.loadURL('data:text/html,<!doctype html><title>icons</title>');

  /** @param {number[]} sizes @param {number} scale */
  const renderAll = async (sizes, scale) => {
    /** @type {Map<number, Rendering>} */
    const bySize = new Map();
    for (const size of sizes) bySize.set(size, await render(win, size, scale));
    return bySize;
  };

  const full = await renderAll([16, 24, 32, 48, 64, 128, 256, 1024], 1);
  const mac = await renderAll([...new Set(ICNS_TYPES.map(([, size]) => size))], MAC_SCALE);
  /** @param {number[]} sizes */
  const pick = (sizes) => sizes.map((s) => /** @type {Rendering} */ (full.get(s)));

  /** @type {[string, Buffer][]} */
  const outputs = [
    [path.join(ASSETS, 'icon.png'), pick([1024])[0].png],
    [path.join(ASSETS, 'icon.ico'), ico(pick([16, 24, 32, 48, 64, 128, 256]))],
    [path.join(ASSETS, 'icon.icns'), icns(mac)],
    [path.join(UI_APP, 'favicon.ico'), ico(pick([16, 32, 48]))],
  ];
  for (const [file, data] of outputs) {
    writeFileSync(file, data);
    console.log(`${path.relative(path.join(APP_DIR, '..'), file)}  ${data.length} bytes`);
  }
  copyFileSync(SVG, path.join(UI_APP, 'icon.svg'));
  console.log('ui/app/icon.svg  (copy of the master)');
  app.exit(0);
}).catch((error) => {
  console.error(error);
  app.exit(1);
});
