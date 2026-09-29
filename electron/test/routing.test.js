// @ts-check
// Hermetic: no Electron binary, no display. Runs in PR CI (design T1).
import { test } from 'node:test';
import assert from 'node:assert/strict';

import { classify, originOf } from '../src/routing.js';

const ORIGIN = 'http://127.0.0.1:3000';

/** @type {Array<[string, string, 'in-app' | 'external' | 'deny']>} */
const CASES = [
  // VISTA's own pages and files. The same-origin shapes found in ui/.
  ['relative page', 'http://127.0.0.1:3000/projects', 'in-app'],
  ['knowledge-base PDF', 'http://127.0.0.1:3000/api/knowledge-bases/kb/publications/a%20b.pdf', 'in-app'],
  ['dataset file', 'http://127.0.0.1:3000/api/files/datasets/x.csv', 'in-app'],
  ['with query and hash', 'http://127.0.0.1:3000/?project=1#msg', 'in-app'],
  ['default-port spelling', 'http://127.0.0.1:3000/', 'in-app'],

  // Other sites. The external shapes found in ui/.
  ['DOI', 'https://doi.org/10.1000/xyz', 'external'],
  ['Globus authorize', 'https://auth.globus.org/v2/oauth2/authorize?client_id=x&code_challenge=y', 'external'],
  ['token source', 'https://docs.olcf.ornl.gov/services_and_applications/s3m/', 'external'],
  ['skill repo', 'https://github.com/org/skill', 'external'],
  ['plain http elsewhere', 'http://example.org/', 'external'],

  // Near misses that must not pass for VISTA's origin.
  ['other port, same host', 'http://127.0.0.1:8001/openapi.json', 'external'],
  ['longer port', 'http://127.0.0.1:30001/', 'external'],
  ['host suffix (not a valid port, so not a URL)', 'http://127.0.0.1:3000.evil.example/', 'deny'],
  ['host suffix without a port', 'http://127.0.0.1.evil.example:3000/', 'external'],
  ['localhost is another origin', 'http://localhost:3000/', 'external'],
  ['https on the same host and port', 'https://127.0.0.1:3000/', 'external'],
  ['userinfo trick', 'http://127.0.0.1:3000@evil.example/', 'external'],

  // Schemes that are never a destination.
  ['dropped file', 'file:///Users/someone/Desktop/data.csv', 'deny'],
  ['javascript', 'javascript:alert(1)', 'deny'],
  ['data', 'data:text/html,<p>hi</p>', 'deny'],
  ['blob', 'blob:http://127.0.0.1:3000/uuid', 'deny'],
  ['mailto', 'mailto:someone@example.org', 'deny'],
  ['about:blank', 'about:blank', 'deny'],
  ['custom app scheme', 'vscode://file/x', 'deny'],

  // Not URLs at all.
  ['empty', '', 'deny'],
  ['relative path (never passed absolute by Electron, but refuse anyway)', '/projects', 'deny'],
  ['garbage', 'http://', 'deny'],
];

for (const [name, url, expected] of CASES) {
  test(`${expected}: ${name}`, () => {
    assert.equal(classify(ORIGIN, url), expected);
  });
}

test('the origin is taken from the start URL, not matched as a prefix', () => {
  assert.equal(originOf('http://127.0.0.1:3000/projects?x=1'), ORIGIN);
  assert.equal(classify('http://127.0.0.1:3000/projects', 'http://127.0.0.1:3000/skills'), 'in-app');
});

test('the dev server origin works the same way', () => {
  const dev = 'http://localhost:3000';
  assert.equal(classify(dev, 'http://localhost:3000/datasets'), 'in-app');
  assert.equal(classify(dev, 'http://127.0.0.1:3000/datasets'), 'external');
});
