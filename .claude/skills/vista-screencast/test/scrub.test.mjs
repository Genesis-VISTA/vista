import assert from "node:assert/strict";
import { mkdtempSync, writeFileSync, mkdirSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { test } from "node:test";
import { SCRUBBED, findSecret, isSecretName, scanDir, scrubHeaders, scrubValue } from "../lib/scrub.mjs";

// Shaped like an S3M token, but made up.
const FAKE_JWT = "eyJhbGciOiJFUzUxMiIsImtpZCI6ImZha2UifQ.eyJhdWQiOlsiZmFrZS5leGFtcGxlIl19.AbCdEfGhIjKlMnOpQrStUvWxYz012345";

test("credential-named fields are scrubbed, others kept", () => {
  const user = {
    email: "researcher@example.org",
    inference_api_key: "sk-not-a-real-key-1234567890",
    hpc: { frontier: { s3mToken: FAKE_JWT, account: "abc123" } },
    keys_set: { olcf: true },
    max_tokens: 4096,
  };
  const out = scrubValue(user);
  assert.equal(out.inference_api_key, SCRUBBED);
  assert.equal(out.hpc.frontier.s3mToken, SCRUBBED);
  assert.equal(out.hpc.frontier.account, "abc123");
  assert.deepEqual(out.keys_set, { olcf: true });
  assert.equal(out.max_tokens, 4096);
  assert.equal(out.email, "researcher@example.org");
});

test("a status object under a credential-ish name keeps its text", () => {
  const status = { checks: { credential: { ok: true, message: "S3M token valid", project: "chm123", expires_at: "2026-11-01" } } };
  assert.deepEqual(scrubValue(status), status);
  assert.deepEqual(scrubValue({ refresh_tokens: ["a", "b"] }), { refresh_tokens: [SCRUBBED, SCRUBBED] });
});

test("names are matched by part, not substring", () => {
  for (const n of ["api_key", "refreshToken", "S3M-Token", "globus_refresh_tokens", "Authorization"]) assert.ok(isSecretName(n), n);
  for (const n of ["monkey", "keystone_id", "tokenizer_name", "description"]) assert.ok(!isSecretName(n), n);
});

test("auth headers and cookies are dropped", () => {
  const h = scrubHeaders({ "Content-Type": "application/json", Cookie: "s=1", authorization: "Bearer x", "x-amzn-oidc-data": "y" });
  assert.deepEqual(h, { "Content-Type": "application/json" });
});

test("the scan finds JWTs and keys but not shas, uuids or images", () => {
  assert.match(findSecret(`token=${FAKE_JWT}`), /JWT/);
  assert.match(findSecret("use sk-proj-abcdefghijklmnop1234"), /key/);
  assert.equal(findSecret("commit 448eab7d2b6ec71f80eb1234567890abcdef1234"), null);
  assert.equal(findSecret("00000000-0000-4000-8000-0000000000a1"), null);
  assert.equal(findSecret("Based on the number of composition entries in the database"), null);
  // paths and file names from a real capture
  assert.equal(findSecret("/mnt/data/outputs/MSTDB-TP_BeF2-NaF-UF4_ternary_liquidus_phase_diagram.png"), null);
  assert.equal(findSecret("/projects/molten-salt-plots-2026/outputs/BeF2-LiF-ThF4-viscosity-fit.json"), null);
  // a bare random key, with no recognisable prefix
  assert.match(findSecret("key=Q7xZp2Lk9vRt4WmN8sYb3HcJ6dFg1AeU"), /key-like/);
});

test("a planted token fails the folder scan, naming file and line", () => {
  const dir = mkdtempSync(path.join(os.tmpdir(), "vista-screencast-scan-"));
  mkdirSync(path.join(dir, "capture", "runs"), { recursive: true });
  const image = { kind: "binary", media_type: "image/png", data: "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==" };
  writeFileSync(path.join(dir, "capture", "runs", "1.jsonl"), JSON.stringify({ event: "function_tool_result", data: { part: { content: [image] } } }) + "\n", "utf-8");
  writeFileSync(path.join(dir, "capture", "responses.jsonl"), ['{"body":{"ok":1}}', JSON.stringify({ body: { note: `pasted ${FAKE_JWT}` } })].join("\n") + "\n", "utf-8");
  const found = scanDir(dir);
  assert.equal(found.length, 1);
  assert.equal(found[0].file, path.join("capture", "responses.jsonl"));
  assert.equal(found[0].line, 2);
});
