// Where things live. Everything a video writes goes under SCREENCAST_HOME, never the repo.
import { existsSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

export const SKILL_DIR = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
export const REPO_ROOT = path.resolve(SKILL_DIR, "..", "..", "..");
export const SCREENCAST_HOME = path.resolve(
  process.env.VISTA_SCREENCAST_HOME || path.join(os.homedir(), ".vista", "screencasts"),
);

export const PORTS = {
  ui: Number(process.env.VISTA_SCREENCAST_UI_PORT || 3100),
  server: Number(process.env.VISTA_SCREENCAST_SERVER_PORT || 8701),
};

const inside = (child, parent) => {
  const rel = path.relative(parent, child);
  return rel === "" || (!rel.startsWith("..") && !path.isAbsolute(rel));
};

/** The folder a capture writes into: created if missing, never inside the repo. */
export function captureDirOf(name) {
  if (!name || name === "selftest") throw new Error("Name the video to capture (not `selftest`).");
  const src = path.isAbsolute(name) ? name : path.join(SCREENCAST_HOME, name);
  if (inside(src, REPO_ROOT)) throw new Error(`Refusing to capture into ${src}: captures never go inside the repository.`);
  return src;
}

/**
 * A video's folders. `src` holds script.yaml, capture/ and assets/; `out` gets
 * everything a run writes. The self-test reads its source from the skill but
 * writes under SCREENCAST_HOME like any other video.
 */
export function videoDirs(name) {
  if (!name) throw new Error("Name a video: a folder under " + SCREENCAST_HOME + ", or `selftest`.");
  const src =
    name === "selftest"
      ? path.join(SKILL_DIR, "selftest")
      : path.isAbsolute(name)
        ? name
        : path.join(SCREENCAST_HOME, name);
  const out = name === "selftest" ? path.join(SCREENCAST_HOME, "_selftest", "out") : path.join(src, "out");
  if (inside(out, REPO_ROOT)) {
    throw new Error(`Refusing to write ${out}: videos and captures never go inside the repository.`);
  }
  if (!existsSync(path.join(src, "script.yaml"))) {
    throw new Error(`No script.yaml in ${src}.`);
  }
  return { src, out, name: path.basename(src) };
}
