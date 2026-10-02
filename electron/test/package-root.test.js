// @ts-check
import assert from 'node:assert/strict';
import { chmodSync, mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { test } from 'node:test';

import { PackageLayoutError, resolveMacPackage } from '../src/package-root.js';

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
