// @ts-check
//
// Package the VISTA window for one platform, for build_local_package.sh
// (design B1). Prints the path of what it made on its last line.
//
//   node scripts/package.js --platform darwin --arch arm64 --out DIR
//
// Signing is deliberately not done here: on macOS the build signs the bundle
// itself, ad hoc and scoped to this one path (B2), so nothing else in the
// package -- `msb` above all -- is ever re-signed.
import { packager } from '@electron/packager';
import { existsSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const APP_DIR = path.dirname(path.dirname(fileURLToPath(import.meta.url)));

/** @param {string} name */
function arg(name) {
  const i = process.argv.indexOf(`--${name}`);
  const value = i >= 0 ? process.argv[i + 1] : undefined;
  if (!value) {
    console.error(`usage: node scripts/package.js --platform <darwin|linux|win32> --arch <arm64|x64> --out <dir>`);
    process.exit(2);
  }
  return value;
}

const PLATFORMS = /** @type {const} */ (['darwin', 'linux', 'win32']);
const ARCHES = /** @type {const} */ (['arm64', 'x64']);

/**
 * @template {string} T
 * @param {string} name
 * @param {readonly T[]} allowed
 * @returns {T}
 */
function oneOf(name, allowed) {
  const value = arg(name);
  if (!(/** @type {readonly string[]} */ (allowed)).includes(value)) {
    console.error(`--${name} must be one of ${allowed.join(', ')} (got "${value}")`);
    process.exit(2);
  }
  return /** @type {T} */ (value);
}

const platform = oneOf('platform', PLATFORMS);
const arch = oneOf('arch', ARCHES);
const out = path.resolve(arg('out'));

/** @type {import('@electron/packager').Options} */
const options = {
  dir: APP_DIR,
  out,
  name: 'VISTA',
  platform,
  arch,
  asar: true,
  overwrite: true,
  quiet: true,
  // Only src/ and package.json are the app; everything here is a devDependency,
  // so prune leaves no node_modules at all.
  prune: true,
  // linux/ is for the launchers, which the build copies next to the window, not
  // part of the app itself. Of assets/, only icon.png is: Linux has no icon in
  // the executable, so main.js hands it to each window.
  ignore: [/^\/test/, /^\/scripts/, /^\/test-results/, /^\/playwright/, /^\/tsconfig/, /^\/assets\/(?!icon\.png$)/, /^\/linux/],
};

if (platform === 'darwin') {
  options.appBundleId = 'gov.ornl.vista';
  // No `osxSign`: leaving it unset is what keeps packager from signing.
  const icon = path.join(APP_DIR, 'assets', 'icon.icns');
  if (existsSync(icon)) options.icon = icon;
}

if (platform === 'win32') {
  // Shown in Task Manager and on the SmartScreen prompt an unsigned VISTA.exe
  // meets, in place of Electron's own.
  options.win32metadata = { CompanyName: 'ORNL', FileDescription: 'VISTA', ProductName: 'VISTA' };
  const icon = path.join(APP_DIR, 'assets', 'icon.ico');
  if (existsSync(icon)) options.icon = icon;
}

const [dir] = await packager(options);
console.log(platform === 'darwin' ? path.join(dir, 'VISTA.app') : dir);
