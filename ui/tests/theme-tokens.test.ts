// @vitest-environment node
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

/**
 * The theme lives entirely in globals.css tokens, so its contract can be read
 * straight off the stylesheet: the two dark blocks (one for System on a dark
 * OS, one for an explicit Dark choice) must agree, every dark token must
 * exist in light, and the text pairs the ui-theme spec names must stay
 * legible in both themes. A palette tweak that breaks any of that fails here
 * rather than in a screenshot review.
 */

const css = readFileSync(fileURLToPath(new URL("../app/globals.css", import.meta.url)), "utf-8")
  .replace(/\/\*[\s\S]*?\*\//g, "");

function block(selector: RegExp): string {
  const m = css.match(selector);
  if (!m) throw new Error(`no block matching ${selector}`);
  return m[1];
}

function tokens(body: string): Map<string, string> {
  const out = new Map<string, string>();
  for (const [, name, value] of body.matchAll(/--([\w-]+):\s*([^;]+);/g)) out.set(name, value.trim());
  return out;
}

const light = tokens(block(/(?:^|\n):root \{([^}]*)\}/));
const systemDark = tokens(
  block(/@media \(prefers-color-scheme: dark\) \{\s*:root:not\(\[data-theme="light"\]\) \{([^}]*)\}/),
);
const forcedDark = tokens(block(/:root\[data-theme="dark"\] \{([^}]*)\}/));

/** A theme is light's tokens with that theme's overrides on top. */
const themes = {
  light,
  dark: new Map([...light, ...forcedDark]),
};

function resolve(theme: Map<string, string>, name: string): string {
  let value = theme.get(name);
  for (let hops = 0; value?.startsWith("var(") && hops < 5; hops++) {
    value = theme.get(value.slice(6, -1));
  }
  if (!value || !/^#[0-9a-f]{6}$/i.test(value)) throw new Error(`--${name} is not a 6-digit hex: ${value}`);
  return value;
}

function luminance(hex: string): number {
  const [r, g, b] = [1, 3, 5].map((i) => {
    const c = parseInt(hex.slice(i, i + 2), 16) / 255;
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

function contrast(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

/** [foreground, background, minimum ratio] — text at 4.5, control boundaries at 3. */
const PAIRS: [string, string, number][] = [
  ["ink", "surface", 4.5],
  ["ink-2", "surface", 4.5],
  ["muted", "surface", 4.5],
  ["muted-2", "surface", 4.5],
  ["ink", "surface-active", 4.5],
  ["brand", "surface", 4.5],
  ["brand", "brand-tint", 4.5],
  ["brand-2", "surface", 4.5],
  ["warning-ink", "surface", 4.5],
  ["warning-strong", "surface", 4.5],
  ["warning-text", "warning-tint", 4.5],
  ["danger-ink", "surface", 4.5],
  ["danger-ink", "danger-tint", 4.5],
  ["success", "success-tint", 4.5],
  ["ok", "surface", 4.5],
  ["kind-finding", "surface", 4.5],
  ["kind-done", "surface", 4.5],
  ["on-brand", "brand-fill", 4.5],
  ["on-brand", "brand-fill-hover", 4.5],
  ["on-brand", "brand-dark", 4.5],
  ["surface", "ink", 4.5],
  ["term-ink", "term-bg", 4.5],
  ["term-muted", "term-bg", 4.5],
  ["brand-fill", "surface", 3],
];

describe("theme tokens", () => {
  it("defines the same dark values for System and for an explicit Dark choice", () => {
    expect(Object.fromEntries(systemDark)).toEqual(Object.fromEntries(forcedDark));
  });

  it("declares every dark token in light :root first", () => {
    const missing = [...forcedDark.keys()].filter((name) => !light.has(name));
    expect(missing).toEqual([]);
  });

  it("switches native controls and scrollbars to dark in both dark blocks", () => {
    const dark = /color-scheme:\s*dark/;
    expect(block(/:root:not\(\[data-theme="light"\]\) \{([^}]*)\}/)).toMatch(dark);
    expect(block(/:root\[data-theme="dark"\] \{([^}]*)\}/)).toMatch(dark);
  });

  for (const [themeName, theme] of Object.entries(themes)) {
    describe(`${themeName} contrast`, () => {
      it.each(PAIRS)("--%s on --%s is at least %s:1", (fg, bg, min) => {
        expect(contrast(resolve(theme, fg), resolve(theme, bg))).toBeGreaterThanOrEqual(min);
      });
    });
  }

  // Dark only: the light theme's --muted on the page gutter is 4.1:1, and this
  // change leaves the light theme exactly as it was. Raised separately.
  it("keeps --muted legible on the page gutter in dark", () => {
    const dark = themes.dark;
    expect(contrast(resolve(dark, "muted"), resolve(dark, "bg"))).toBeGreaterThanOrEqual(4.5);
  });

  it("keeps terminal surfaces darker than the cards around them in dark", () => {
    const dark = themes.dark;
    expect(luminance(resolve(dark, "term-bg"))).toBeLessThan(luminance(resolve(dark, "surface")));
  });
});
