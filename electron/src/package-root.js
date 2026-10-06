// @ts-check
import { accessSync, constants, readFileSync } from 'node:fs';
import path from 'node:path';

export class PackageLayoutError extends Error {
  /** @param {string} detail */
  constructor(detail) {
    super(detail);
    this.name = 'PackageLayoutError';
  }
}

/**
 * Where the application sits in a Linux or Windows package, and what it starts:
 * the window is app/window/, two levels below the package root (design D6).
 * `supervisor` is the script run in supervised mode; on Windows that is
 * vista.ps1 rather than its vista.cmd front, which only a terminal needs.
 */
const WINDOW_LAYOUTS = Object.freeze({
  linux: {
    os: 'linux',
    entrypoint: 'app/window/vista-app',
    window: 'app/window/VISTA',
    diagnosticLauncher: 'vista',
    supervisor: 'vista',
  },
  win32: {
    os: 'windows',
    entrypoint: 'app/window/VISTA.exe',
    window: 'app/window/VISTA.exe',
    diagnosticLauncher: 'vista.cmd',
    supervisor: 'vista.ps1',
  },
});

/** @param {string} packageRoot */
function readManifest(packageRoot) {
  try {
    return JSON.parse(readFileSync(path.join(packageRoot, 'manifest.json'), 'utf8'));
  } catch {
    throw new PackageLayoutError('manifest.json is missing or invalid');
  }
}

/**
 * @param {string} file
 * @param {number} mode
 * @param {string} what
 */
function requireAccess(file, mode, what) {
  try {
    accessSync(file, mode);
  } catch {
    throw new PackageLayoutError(`${what} is missing or cannot be run`);
  }
}

/**
 * Resolve the relocatable macOS package that contains the app. The app is
 * intentionally small; the launcher and runtime remain siblings so a package
 * upgrade is still a replaceable directory rather than an installer.
 *
 * @param {string} resourcesPath
 * @returns {{ packageRoot: string, launcherPath: string }}
 */
export function resolveMacPackage(resourcesPath) {
  const appBundle = path.resolve(resourcesPath, '..', '..');
  const appName = path.basename(appBundle);
  if (!appName.endsWith('.app')) {
    throw new PackageLayoutError('the application bundle has no .app name');
  }

  const packageRoot = path.dirname(appBundle);
  const manifest = readManifest(packageRoot);
  if (
    manifest?.target?.os !== 'macos'
    || manifest?.entrypoint !== appName
    || manifest?.diagnostic_launcher !== 'vista'
    || manifest?.window?.exe !== `${appName}/Contents/MacOS/${appName.slice(0, -4)}`
  ) {
    throw new PackageLayoutError('manifest.json does not describe this macOS application');
  }

  const launcherPath = path.join(packageRoot, 'vista');
  requireAccess(launcherPath, constants.X_OK, 'the diagnostic launcher');
  return { packageRoot, launcherPath };
}

/**
 * Resolve the Linux or Windows package around the window executable at
 * `execPath`, which has to be the one its manifest names.
 *
 * @param {string} execPath
 * @param {'linux' | 'win32'} platform
 * @returns {{ packageRoot: string, launcherPath: string }}
 */
export function resolveWindowPackage(execPath, platform) {
  const layout = WINDOW_LAYOUTS[platform];
  const windowDir = path.dirname(execPath);
  if (path.basename(windowDir) !== 'window' || path.basename(path.dirname(windowDir)) !== 'app') {
    throw new PackageLayoutError('the VISTA window is not in the package\'s app/window folder');
  }
  const packageRoot = path.dirname(path.dirname(windowDir));
  const manifest = readManifest(packageRoot);
  const exe = path.relative(packageRoot, execPath).split(path.sep).join('/');
  if (
    manifest?.target?.os !== layout.os
    || manifest?.entrypoint !== layout.entrypoint
    || manifest?.diagnostic_launcher !== layout.diagnosticLauncher
    || manifest?.window?.exe !== layout.window
    || exe !== layout.window
  ) {
    throw new PackageLayoutError(`manifest.json does not describe this ${layout.os} application`);
  }

  const launcherPath = path.join(packageRoot, layout.supervisor);
  // Windows has no execute bit: vista.ps1 only has to be readable.
  requireAccess(launcherPath, platform === 'win32' ? constants.R_OK : constants.X_OK, 'the launcher');
  return { packageRoot, launcherPath };
}

/**
 * The package around this running application, on any platform.
 *
 * @param {{ platform: NodeJS.Platform, execPath: string, resourcesPath: string }} where
 * @returns {{ packageRoot: string, launcherPath: string }}
 */
export function resolvePackage({ platform, execPath, resourcesPath }) {
  if (platform === 'darwin') return resolveMacPackage(resourcesPath);
  if (platform === 'linux' || platform === 'win32') return resolveWindowPackage(execPath, platform);
  throw new PackageLayoutError(`VISTA has no package layout for ${platform}`);
}
