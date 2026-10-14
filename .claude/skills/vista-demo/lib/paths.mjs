// Where things live. Everything a video writes goes under DEMO_HOME, never the repo.
import { existsSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

export const SKILL_DIR = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
export const REPO_ROOT = path.resolve(SKILL_DIR, "..", "..", "..");
export const DEMO_HOME = path.resolve(
  process.env.VISTA_DEMO_HOME || path.join(os.homedir(), ".vista", "demo-captures"),
);

export const PORTS = {
  ui: Number(process.env.VISTA_DEMO_UI_PORT || 3100),
  server: Number(process.env.VISTA_DEMO_SERVER_PORT || 8701),
};

const inside = (child, parent) => {
  const rel = path.relative(parent, child);
  return rel === "" || (!rel.startsWith("..") && !path.isAbsolute(rel));
};

/**
 * A video's folders. `src` holds script.yaml, capture/ and assets/; `out` gets
 * everything a run writes. The self-test reads its source from the skill but
 * writes under DEMO_HOME like any other video.
 */
export function videoDirs(name) {
  if (!name) throw new Error("Name a video: a folder under " + DEMO_HOME + ", or `selftest`.");
  const src =
    name === "selftest"
      ? path.join(SKILL_DIR, "selftest")
      : path.isAbsolute(name)
        ? name
        : path.join(DEMO_HOME, name);
  const out = name === "selftest" ? path.join(DEMO_HOME, "_selftest", "out") : path.join(src, "out");
  if (inside(out, REPO_ROOT)) {
    throw new Error(`Refusing to write ${out}: videos and captures never go inside the repository.`);
  }
  if (!existsSync(path.join(src, "script.yaml"))) {
    throw new Error(`No script.yaml in ${src}.`);
  }
  return { src, out, name: path.basename(src) };
}
