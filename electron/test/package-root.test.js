// @ts-check
import assert from 'node:assert/strict';
import { chmodSync, mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { test } from 'node:test';

import {
  PackageLayoutError,
  resolveMacPackage,
  resolvePackage,
  resolveWindowPackage,
} from '../src/package-root.js';

function fixture() {
  const root = mkdtempSync(path.join(tmpdir(), 'vista-package-root-'));
  const resources = path.join(root, 'VISTA.app', 'Contents', 'Resources');
  mkdirSync(resources, { recursive: true });
  writeFileSync(path.join(root, 'vista'), '#!/bin/sh\n', 'utf8');
  chmodSync(path.join(root, 'vista'), 0o755);
  writeFileSync(path.join(root, 'manifest.json'), JSON.stringify({
    target: { os: 'macos' },
    entrypoint: 'VISTA.app',
    diagnostic_launcher: 'vista',
    window: { exe: 'VISTA.app/Contents/MacOS/VISTA' },
  }), 'utf8');
  return { root, resources };
}

test('resolves the launcher beside a valid top-level macOS app', () => {
  const { root, resources } = fixture();
  assert.deepEqual(resolveMacPackage(resources), {
    packageRoot: root,
    launcherPath: path.join(root, 'vista'),
  });
  rmSync(root, { recursive: true, force: true });
});

test('resolves a separately named development app described by its manifest', () => {
  const root = mkdtempSync(path.join(tmpdir(), 'vista-dev-package-root-'));
  const resources = path.join(root, 'VISTA Dev.app', 'Contents', 'Resources');
  mkdirSync(resources, { recursive: true });
  writeFileSync(path.join(root, 'vista'), '#!/bin/sh\n', 'utf8');
  chmodSync(path.join(root, 'vista'), 0o755);
  writeFileSync(path.join(root, 'manifest.json'), JSON.stringify({
    target: { os: 'macos' },
    entrypoint: 'VISTA Dev.app',
    diagnostic_launcher: 'vista',
    window: { exe: 'VISTA Dev.app/Contents/MacOS/VISTA Dev' },
  }), 'utf8');
  assert.deepEqual(resolveMacPackage(resources), {
    packageRoot: root,
    launcherPath: path.join(root, 'vista'),
  });
  rmSync(root, { recursive: true, force: true });
});

test('refuses an app separated from its package', () => {
  const { root, resources } = fixture();
  rmSync(path.join(root, 'manifest.json'));
  assert.throws(() => resolveMacPackage(resources), PackageLayoutError);
  rmSync(root, { recursive: true, force: true });
});

test('refuses a manifest that points at another entrypoint', () => {
  const { root, resources } = fixture();
  writeFileSync(path.join(root, 'manifest.json'), JSON.stringify({
    target: { os: 'macos' },
    entrypoint: 'app/window/VISTA.app',
    diagnostic_launcher: 'vista',
    window: { exe: 'app/window/VISTA.app/Contents/MacOS/VISTA' },
  }), 'utf8');
  assert.throws(() => resolveMacPackage(resources), /does not describe/);
  rmSync(root, { recursive: true, force: true });
});

/**
 * A Linux or Windows package: the window at app/window/, the launchers at the root.
 * @param {'linux' | 'win32'} platform
 */
function windowFixture(platform) {
  const windows = platform === 'win32';
  const root = mkdtempSync(path.join(tmpdir(), `vista-${platform}-package-root-`));
  const exe = path.join(root, 'app', 'window', windows ? 'VISTA.exe' : 'VISTA');
  mkdirSync(path.dirname(exe), { recursive: true });
  writeFileSync(exe, '', 'utf8');
  const launcher = path.join(root, windows ? 'vista.ps1' : 'vista');
  writeFileSync(launcher, '', 'utf8');
  chmodSync(launcher, 0o755);
  writeFileSync(path.join(root, 'manifest.json'), JSON.stringify({
    target: { os: windows ? 'windows' : 'linux' },
    entrypoint: windows ? 'app/window/VISTA.exe' : 'app/window/vista-app',
    diagnostic_launcher: windows ? 'vista.cmd' : 'vista',
    window: { exe: windows ? 'app/window/VISTA.exe' : 'app/window/VISTA' },
  }), 'utf8');
  return { root, exe, launcher };
}

test('resolves a Linux package two levels above app/window, starting vista', () => {
  const { root, exe, launcher } = windowFixture('linux');
  assert.deepEqual(resolvePackage({ platform: 'linux', execPath: exe, resourcesPath: '' }), {
    packageRoot: root,
    launcherPath: launcher,
  });
  rmSync(root, { recursive: true, force: true });
});

test('resolves a Windows package, starting vista.ps1 rather than vista.cmd', () => {
  const { root, exe, launcher } = windowFixture('win32');
  assert.deepEqual(resolvePackage({ platform: 'win32', execPath: exe, resourcesPath: '' }), {
    packageRoot: root,
    launcherPath: launcher,
  });
  assert.equal(path.basename(launcher), 'vista.ps1');
  rmSync(root, { recursive: true, force: true });
});

test('refuses a Linux window that is not in app/window', () => {
  const { root } = windowFixture('linux');
  const moved = path.join(root, 'VISTA');
  writeFileSync(moved, '', 'utf8');
  assert.throws(() => resolveWindowPackage(moved, 'linux'), /app\/window folder/);
  rmSync(root, { recursive: true, force: true });
});

test('refuses a manifest from another platform', () => {
  const { root, exe } = windowFixture('win32');
  assert.throws(() => resolveWindowPackage(exe, 'linux'), /does not describe this linux/);
  rmSync(root, { recursive: true, force: true });
});

test('refuses a package whose launcher is missing', () => {
  const { root, exe, launcher } = windowFixture('linux');
  rmSync(launcher);
  assert.throws(() => resolveWindowPackage(exe, 'linux'), /launcher is missing/);
  rmSync(root, { recursive: true, force: true });
});
