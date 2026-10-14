// Recording what the researcher does, by meaning (design D6).
//
// RECORDER runs in the capture browser (page.addInitScript) and reports each
// action through the `__demoRecord` binding. Locators are worked out at
// pointerdown, before the click's own handler can re-render the page, in the
// order SCRIPT.md gives: role and name, placeholder or label, visible text,
// then the same scoped within the nearest card-like ancestor. Pointer
// positions are never sent.
//
// startActionRecorder (Node) turns the reports into actions.jsonl lines,
// merging keystrokes into one `type` per field.
import { appendFileSync } from "node:fs";

export function RECORDER() {
  if (window.__demoRecorder) return;
  window.__demoRecorder = true;

  const norm = (s) => (s || "").replace(/\s+/g, " ").trim();
  const visible = (el) => {
    const r = el.getBoundingClientRect();
    const cs = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && cs.visibility !== "hidden" && cs.display !== "none";
  };

  function roleOf(el) {
    const explicit = el.getAttribute("role");
    if (explicit) return explicit.split(" ")[0];
    const tag = el.tagName.toLowerCase();
    if (tag === "button") return "button";
    if (tag === "a" && el.hasAttribute("href")) return "link";
    if (tag === "textarea") return "textbox";
    if (tag === "select") return "combobox";
    if (tag === "input") {
      const t = (el.getAttribute("type") || "text").toLowerCase();
      if (["button", "submit", "reset"].includes(t)) return "button";
      if (t === "checkbox") return "checkbox";
      if (t === "radio") return "radio";
      return "textbox";
    }
    if (/^h[1-6]$/.test(tag)) return "heading";
    return null;
  }
  function nameOf(el) {
    const label = el.getAttribute("aria-label");
    if (label) return norm(label);
    const by = el.getAttribute("aria-labelledby");
    if (by) return norm(by.split(/\s+/).map((id) => document.getElementById(id)?.innerText).join(" "));
    if (el.labels?.length) return norm(el.labels[0].innerText);
    const tag = el.tagName.toLowerCase();
    if (tag === "input" && ["button", "submit"].includes(el.type)) return norm(el.value);
    const text = norm(el.innerText);
    return text || norm(el.getAttribute("title"));
  }

  const ROLE_SELECTOR = {
    button: 'button, [role="button"], input[type="button"], input[type="submit"]',
    link: 'a[href], [role="link"]',
    textbox: 'textarea, input:not([type]), input[type="text"], input[type="search"], input[type="email"], [role="textbox"]',
    combobox: 'select, [role="combobox"]',
  };
  const matches = (scope, spec) => {
    let list;
    if (spec.role) {
      const sel = ROLE_SELECTOR[spec.role] || `[role="${spec.role}"]`;
      const re = typeof spec.name === "string" && spec.name.match(/^\/(.*)\/([a-z]*)$/);
      const nameOk = re ? (n) => new RegExp(re[1], re[2]).test(n) : (n) => n === spec.name;
      list = [...scope.querySelectorAll(sel)].filter((el) => roleOf(el) === spec.role && nameOk(nameOf(el)));
    } else if (spec.placeholder) list = [...scope.querySelectorAll("[placeholder]")].filter((el) => el.getAttribute("placeholder") === spec.placeholder);
    else if (spec.label) list = [...scope.querySelectorAll("input, textarea, select")].filter((el) => el.labels?.[0] && norm(el.labels[0].innerText) === spec.label);
    else if (spec.css && spec.has) {
      const leafIs = (el, text) => [...el.querySelectorAll("*")].some((d) => d.children.length === 0 && norm(d.innerText) === text);
      list = [...scope.querySelectorAll(spec.css)].filter((el) => leafIs(el, spec.has.text));
    } else if (spec.css) list = [...scope.querySelectorAll(spec.css)].filter((el) => !spec.text || norm(el.innerText).includes(spec.text));
    else list = [...scope.querySelectorAll("*")].filter((el) => el.children.length === 0 && norm(el.innerText) === spec.text);
    return list.filter(visible);
  };

  const ACTIONABLE = 'button, a[href], input, textarea, select, [role="button"], [role="link"], [role="tab"], [role="menuitem"], [role="option"], [role="checkbox"], [role="switch"], [contenteditable="true"]';

  // The nearest ancestor that names a card or section, as { css, text }.
  function scopeOf(el) {
    for (let a = el.parentElement; a && a !== document.body; a = a.parentElement) {
      const cls = [...a.classList].find((c) => /(card|item|row|panel|section|tile|entry)$/i.test(c) && !/^(is|has)-/.test(c));
      if (!cls) continue;
      const heading = a.querySelector("h1, h2, h3, h4, h5, strong, [class*='title'], [class*='name']");
      const text = norm(heading?.innerText).slice(0, 60);
      // containing the title, else having an element that is exactly the title
      // (another card's description may mention it)
      const specs = text ? [{ css: `.${cls}`, text }, { css: `.${cls}`, has: { text } }] : [{ css: `.${cls}` }];
      for (const spec of specs) if (matches(document, spec).length === 1) return spec;
    }
    return null;
  }

  // A trailing count ("Activity 2", "Conversations (3)") changes as a session
  // goes on, so a name ending in one is matched by what comes before it.
  const COUNTED = /^(.*?\D)\s*\(?\d+\)?$/;
  const escapeRe = (s) => s.replace(/[.*+?^${}()|[\]\\/]/g, "\\$&");

  function candidates(el) {
    const out = [];
    const role = roleOf(el);
    const name = nameOf(el);
    const counted = name.match(COUNTED);
    if (role && counted && counted[1].trim()) out.push({ role, name: `/^${escapeRe(counted[1].trim())}/` });
    else if (role && name && name.length <= 80) out.push({ role, name });
    if (el.getAttribute("placeholder")) out.push({ placeholder: el.getAttribute("placeholder") });
    if (el.labels?.length) out.push({ label: norm(el.labels[0].innerText) });
    const text = norm(el.innerText);
    if (text && text.length <= 60 && el.children.length === 0) out.push({ text });
    return out;
  }

  /** The first candidate naming exactly `el`, scoped or with nth if it must be. */
  function locate(el) {
    const cands = candidates(el);
    for (const spec of cands) {
      const m = matches(document, spec);
      if (m.length === 1 && m[0] === el) return spec;
    }
    const scope = scopeOf(el);
    if (scope) {
      const root = matches(document, scope)[0];
      for (const spec of cands) {
        const m = matches(root, spec);
        if (m.length === 1 && m[0] === el) return { ...spec, within: scope };
      }
    }
    for (const spec of cands) {
      const m = matches(document, spec);
      const nth = m.indexOf(el);
      if (nth >= 0) return nth === 0 ? spec : { ...spec, nth };
    }
    return null;
  }

  // `at` is when the event happened (ms since the epoch), so requests it
  // triggered can be ordered after it even if they reach the server first.
  const send = (action) => window.__demoRecord?.({ ...action, at: performance.timeOrigin + performance.now() });
  const isField = (el) => el.matches?.("input:not([type=button]):not([type=submit]):not([type=checkbox]):not([type=radio]), textarea, [contenteditable='true']");

  addEventListener(
    "pointerdown",
    (e) => {
      const el = e.target.closest?.(ACTIONABLE) || e.target;
      const spec = locate(el);
      send(spec ? { kind: "click", spec, field: !!isField(el) } : { kind: "unlocated", tag: el.tagName.toLowerCase(), text: norm(el.innerText).slice(0, 40) });
    },
    true,
  );
  addEventListener(
    "input",
    (e) => {
      const el = e.target;
      if (!isField(el)) return;
      const spec = locate(el);
      if (spec) send({ kind: "type", spec, text: el.value ?? el.innerText });
    },
    true,
  );
  addEventListener(
    "keydown",
    (e) => {
      if (e.key === "Enter" && !e.shiftKey && isField(e.target)) send({ kind: "press", key: "Enter" });
    },
    true,
  );
}

const sameSpec = (a, b) => JSON.stringify(a) === JSON.stringify(b);

/**
 * Collect actions from `page` into `file` (actions.jsonl). `onAction(count)`
 * is called as each new action begins, before the browser acts on it, so the
 * capture server tags what follows with it. Returns { actions, goto, flush }.
 *
 * A click into a field is held back: if typing follows, the two become one
 * `type` step (the player clicks before typing).
 */
export async function startActionRecorder(page, file, { onAction = async () => {}, log = () => {} } = {}) {
  const actions = [];
  let pending = null; // a click into a field, or a field being typed into
  const write = ({ field, ...a }) => appendFileSync(file, JSON.stringify(a) + "\n", "utf-8");
  const flush = () => {
    if (pending) write(pending);
    pending = null;
  };
  const begin = async (a) => {
    actions.push(a);
    a.action = actions.length;
    await onAction(a.action);
    log(`  action ${a.action}: ${a.kind} ${JSON.stringify(a.spec ?? a.key ?? a.path ?? "")}`);
    return a;
  };

  await page.exposeBinding("__demoRecord", async (_source, a) => {
    if (a.kind === "unlocated") {
      log(`  (a click on <${a.tag}> ${JSON.stringify(a.text)} could not be named; it is left out of the script)`);
      return;
    }
    if (a.kind === "type") {
      if (pending && sameSpec(pending.spec, a.spec)) {
        pending.kind = "type"; // a click into this field becomes the type step
        pending.text = a.text;
        return;
      }
      if (!a.text) return; // a field emptied before anything was typed into it
      flush();
      pending = await begin({ kind: "type", spec: a.spec, text: a.text, at: Math.round(a.at) });
      return;
    }
    if (a.kind === "click" && a.field && pending && sameSpec(pending.spec, a.spec)) return; // back into the same field
    flush();
    if (a.kind === "press") return write(await begin({ kind: "press", key: a.key, at: Math.round(a.at) }));
    const click = await begin({ kind: "click", spec: a.spec, field: a.field, at: Math.round(a.at) });
    if (a.field) pending = click;
    else write(click);
  });

  return {
    actions,
    /** Record a navigation as a `goto` action (the capture's first page). */
    async goto(p) {
      flush();
      write(await begin({ kind: "goto", path: p, at: Date.now() }));
    },
    flush,
  };
}
