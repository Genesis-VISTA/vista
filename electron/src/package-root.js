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
  const manifestPath = path.join(packageRoot, 'manifest.json');
  let manifest;
  try {
    manifest = JSON.parse(readFileSync(manifestPath, 'utf8'));
  } catch {
    throw new PackageLayoutError('manifest.json is missing or invalid');
  }
  if (
    manifest?.target?.os !== 'macos'
    || manifest?.entrypoint !== appName
    || manifest?.diagnostic_launcher !== 'vista'
    || manifest?.window?.exe !== `${appName}/Contents/MacOS/${appName.slice(0, -4)}`
  ) {
    throw new PackageLayoutError('manifest.json does not describe this macOS application');
  }

  const launcherPath = path.join(packageRoot, 'vista');
  try {
    accessSync(launcherPath, constants.X_OK);
  } catch {
    throw new PackageLayoutError('the diagnostic launcher is missing or is not executable');
  }
  return { packageRoot, launcherPath };
}
