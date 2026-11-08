// Credentials out of captures (design D9): replace them before anything is
// written, then scan what was written for anything that still looks like one.
import { readdirSync, readFileSync, statSync } from "node:fs";
import path from "node:path";

export const SCRUBBED = "<scrubbed>";

// Name parts that mark a field as holding a credential. A field name is split
// on _ - . and camelCase, so `inference_api_key`, `refreshToken` and
// `S3M-Token` all match, while `keys_set` keeps its booleans (only strings
// are replaced) and `monkey` is left alone.
const SECRET_PARTS = new Set([
  "token", "tokens", "key", "keys", "apikey", "secret", "secrets", "password", "passwd",
  "credential", "credentials", "cookie", "cookies", "authorization", "jwt", "bearer",
]);
const DROP_HEADERS = new Set(["authorization", "cookie", "set-cookie", "x-amzn-oidc-data", "x-amzn-oidc-accesstoken", "x-amzn-oidc-identity"]);

export function isSecretName(name) {
  const parts = String(name)
    .replace(/([a-z0-9])([A-Z])/g, "$1_$2")
    .toLowerCase()
    .split(/[_\-.\s]+/);
  return parts.some((p) => SECRET_PARTS.has(p));
}

/**
 * A deep copy of `value` with each string held by a credential-named field
 * (directly, or in a list) replaced. An object under such a name is walked
 * with its own names: `checks.credential` is a status object whose `message`
 * is not a secret. A secret nested deeper than its field name is left to
 * scanDir, which fails the capture.
 */
export function scrubValue(value, secret = false) {
  if (typeof value === "string") return secret && value !== "" ? SCRUBBED : value;
  if (Array.isArray(value)) return value.map((v) => scrubValue(v, secret));
  if (value && typeof value === "object") {
    return Object.fromEntries(Object.entries(value).map(([k, v]) => [k, scrubValue(v, isSecretName(k))]));
  }
  return value;
}

export function scrubHeaders(headers) {
  return Object.fromEntries(Object.entries(headers).filter(([k]) => !DROP_HEADERS.has(k.toLowerCase())));
}

const JWT = /eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}/;
const PREFIXED = /\b(sk-[A-Za-z0-9_-]{16,}|ghp_[A-Za-z0-9]{20,}|glpat-[A-Za-z0-9_-]{16,}|xox[abpr]-[A-Za-z0-9-]{10,}|AKIA[A-Z0-9]{16})/;
// A long unbroken stretch mixing upper case, lower case and digits with high
// entropy, as random keys are. Runs are cut at separators first, so paths and
// file names (`BeF2-NaF-UF4_liquidus.png`, `/projects/molten-salt/...`) are
// short pieces of words; pure hex (commit shas, uuids) never qualifies.
const LONG = /[A-Za-z0-9+]{24,}/g;
function entropy(s) {
  const counts = {};
  for (const c of s) counts[c] = (counts[c] ?? 0) + 1;
  return Object.values(counts).reduce((h, n) => h - (n / s.length) * Math.log2(n / s.length), 0);
}
const looksRandom = (s) =>
  /[A-Z]/.test(s) && /[a-z]/.test(s) && /[0-9]/.test(s) && !/^[0-9a-f]+$/i.test(s) && entropy(s) >= 4;

/** What in `text` still looks like a credential, or null. */
export function findSecret(text) {
  const jwt = text.match(JWT);
  if (jwt) return `a JWT (${jwt[0].slice(0, 12)}…)`;
  const pre = text.match(PREFIXED);
  if (pre) return `a key (${pre[0].slice(0, 8)}…)`;
  for (const m of text.matchAll(LONG)) if (looksRandom(m[0])) return `a key-like string (${m[0].slice(0, 8)}…)`;
  return null;
}

// Image and file bytes are base64 and look random; they are skipped.
function strings(value, out, skip = false) {
  if (typeof value === "string") {
    if (!skip) out.push(value);
  } else if (Array.isArray(value)) value.forEach((v) => strings(v, out));
  else if (value && typeof value === "object") {
    const binary = "media_type" in value || value.kind === "binary" || value.encoding === "base64";
    for (const [k, v] of Object.entries(value)) strings(v, out, binary && (k === "data" || k === "body"));
  }
  return out;
}

/**
 * Scan every .jsonl, .json and .yaml file under `dir`. Returns
 * [{ file, line, what }] for each line holding something credential-shaped.
 */
export function scanDir(dir) {
  const found = [];
  const walk = (d) => {
    for (const name of readdirSync(d)) {
      const full = path.join(d, name);
      if (statSync(full).isDirectory()) {
        if (name !== "frames" && name !== "out") walk(full);
        continue;
      }
      if (!/\.(jsonl|json|ya?ml|txt)$/.test(name)) continue;
      readFileSync(full, "utf-8").split("\n").forEach((line, i) => {
        if (!line.trim()) return;
        let texts = [line];
        if (name.endsWith(".jsonl") || name.endsWith(".json")) {
          try {
            texts = strings(JSON.parse(line), []);
          } catch {
            // not one JSON value per line; scan it as text
          }
        }
        for (const t of texts) {
          const what = findSecret(t);
          if (what) {
            found.push({ file: path.relative(dir, full), line: i + 1, what });
            break;
          }
        }
      });
    }
  };
  walk(dir);
  return found;
}
