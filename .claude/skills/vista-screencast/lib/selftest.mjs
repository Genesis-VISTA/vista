// The self-test's own checks (task 4.4): the Playwright pin, and that a run
// leaves the checkout untouched.
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import path from "node:path";
import { REPO_ROOT, SKILL_DIR } from "./paths.mjs";

/** The Playwright versions pinned here and locked in ui/. */
export function pins({ skillPkg, uiLock } = {}) {
  skillPkg ??= JSON.parse(readFileSync(path.join(SKILL_DIR, "package.json"), "utf-8"));
  uiLock ??= JSON.parse(readFileSync(path.join(REPO_ROOT, "ui", "package-lock.json"), "utf-8"));
  const pkgs = uiLock.packages ?? {};
  return {
    skill: skillPkg.dependencies?.playwright ?? null,
    ui: pkgs["node_modules/@playwright/test"]?.version ?? pkgs["node_modules/playwright"]?.version ?? null,
  };
}

/** Throws, saying which to change, when the two Playwright versions differ. */
export function checkPin(p = pins()) {
  if (!p.ui) throw new Error("ui/package-lock.json locks no Playwright; the skill can't tell which browsers ui/ installs.");
  if (p.skill !== p.ui) {
    throw new Error(
      `Playwright is pinned to ${p.skill} in .claude/skills/vista-screencast/package.json but ui/ locks ${p.ui}. ` +
        `Set the skill's pin to "${p.ui}" and run npm install there, so both use the same browsers.`,
    );
  }
  return p.ui;
}

/** `git status --porcelain` of the checkout, ignored files excluded. */
export const gitStatus = () => execFileSync("git", ["-C", REPO_ROOT, "status", "--porcelain"], { encoding: "utf-8" });

/** Throws, listing the paths, when the checkout's status changed. */
export function checkUnchanged(before, after = gitStatus()) {
  if (before === after) return;
  const lines = (s) => new Set(s.split("\n").filter(Boolean));
  const was = lines(before);
  const changed = [...lines(after)].filter((l) => !was.has(l));
  throw new Error(`The self-test changed the checkout:\n${changed.map((l) => "  " + l).join("\n") || after}`);
}
