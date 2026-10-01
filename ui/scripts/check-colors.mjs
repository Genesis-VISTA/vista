#!/usr/bin/env node
/**
 * Fails when a color is written as a literal instead of a token.
 *
 * Both themes come from the token blocks at the top of app/globals.css. A
 * literal anywhere else paints the same in light and dark, which is how a
 * light patch ends up in the dark theme. So:
 *
 *   - globals.css may hold hex / rgb() / rgba() values only inside the light
 *     `:root` block and the two dark blocks;
 *   - .ts / .tsx under app/, components/ and lib/ may hold none at all.
 *
 * A deliberate exception goes in ALLOWED as "path:line-substring", with the
 * reason beside it. Runs as part of `npm run lint`.
 */
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import { fileURLToPath } from "node:url";

const UI = fileURLToPath(new URL("..", import.meta.url));

/** "relative/path:text that appears on the line" — keep each one justified. */
const ALLOWED = [];

const COLOR = /#[0-9a-f]{3,8}\b|\brgba?\(/gi;
const TOKEN_BLOCKS = [
  /(?:^|\n):root \{[^}]*\}/g,
  /:root:not\(\[data-theme="light"\]\) \{[^}]*\}/g,
  /:root\[data-theme="dark"\] \{[^}]*\}/g,
];

function allowed(file, line) {
  return ALLOWED.some((entry) => {
    const at = entry.indexOf(":");
    return entry.slice(0, at) === file && line.includes(entry.slice(at + 1));
  });
}

/** Blanks out comments and the given ranges, keeping offsets and line numbers. */
function blank(text, patterns) {
  let out = text.replace(/\/\*[\s\S]*?\*\//g, (m) => m.replace(/[^\n]/g, " "));
  for (const pattern of patterns) out = out.replace(pattern, (m) => m.replace(/[^\n]/g, " "));
  return out;
}

function findings(file, text, scanned) {
  const lines = text.split("\n");
  const out = [];
  scanned.split("\n").forEach((line, i) => {
    if (COLOR.test(line) && !allowed(file, lines[i])) out.push(`${file}:${i + 1}: ${lines[i].trim()}`);
    COLOR.lastIndex = 0;
  });
  return out;
}

function sources(dir) {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) return sources(path);
    return /\.tsx?$/.test(name) ? [path] : [];
  });
}

const problems = [];

const cssFile = "app/globals.css";
const css = readFileSync(join(UI, cssFile), "utf-8");
problems.push(...findings(cssFile, css, blank(css, TOKEN_BLOCKS)));

for (const path of ["app", "components", "lib"].flatMap((d) => sources(join(UI, d)))) {
  const file = relative(UI, path);
  const text = readFileSync(path, "utf-8");
  // Line comments too: prose that quotes a hex value is not a color.
  const scanned = blank(text, []).replace(/(^|[^:])\/\/.*$/gm, (m, p) => p + m.slice(p.length).replace(/./g, " "));
  problems.push(...findings(file, text, scanned));
}

if (problems.length) {
  console.error("Literal colors outside the theme tokens (use a var(--token) from app/globals.css):\n");
  for (const p of problems) console.error(`  ${p}`);
  console.error(`\n${problems.length} found. A deliberate exception goes in ALLOWED in scripts/check-colors.mjs.`);
  process.exit(1);
}
console.log("check-colors: every color is a theme token.");
