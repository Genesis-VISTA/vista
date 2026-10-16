// A private build of the UI (design D5): a copy of ui/ under SCREENCAST_HOME, built
// standalone and run with its own backend URL, so the checkout's ui/.next and
// any running ./launch.sh stack are never touched.
import { spawn, execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import { cpSync, existsSync, mkdirSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import path from "node:path";
import { SCREENCAST_HOME, PORTS, REPO_ROOT } from "./paths.mjs";

const UI = path.join(REPO_ROOT, "ui");
const git = (...args) => execFileSync("git", ["-C", REPO_ROOT, ...args], { encoding: "utf-8" });

/** Names the build after ui/'s committed tree plus any uncommitted edits in it. */
export function uiTreeHash() {
  const tree = git("rev-parse", "HEAD:ui").trim();
  const dirty = git("status", "--porcelain", "--untracked-files=all", "--", "ui");
  if (!dirty.trim()) return tree.slice(0, 12);
  const diff = git("diff", "HEAD", "--", "ui");
  const digest = createHash("sha256").update(dirty).update(diff).digest("hex");
  return `${tree.slice(0, 8)}-dirty-${digest.slice(0, 8)}`;
}

function run(cmd, args, opts) {
  return new Promise((resolve, reject) => {
    const child = spawn(cmd, args, { stdio: ["ignore", "inherit", "inherit"], ...opts });
    child.on("exit", (code) => (code === 0 ? resolve() : reject(new Error(`${cmd} ${args.join(" ")} exited ${code}`))));
  });
}

/** Build (or reuse) the private UI and return the directory holding server.js. */
export async function ensureUiBuild({ log = console.log } = {}) {
  if (!existsSync(path.join(UI, "node_modules", "next"))) {
    throw new Error(`ui/node_modules is missing; run \`npm ci\` in ${UI} first.`);
  }
  const hash = uiTreeHash();
  const root = path.join(SCREENCAST_HOME, "_ui", hash);
  const standalone = path.join(root, "ui", ".next", "standalone");
  const done = path.join(root, "BUILT");
  if (existsSync(done)) {
    log(`UI build ${hash} reused`);
    return standalone;
  }

  log(`Building the UI (${hash}) in ${root}`);
  rmSync(root, { recursive: true, force: true });
  mkdirSync(root, { recursive: true });
  const copy = path.join(root, "ui");
  cpSync(UI, copy, {
    recursive: true,
    filter: (src) => {
      const rel = path.relative(UI, src);
      const top = rel.split(path.sep)[0];
      return top !== "node_modules" && top !== ".next" && top !== "test-results";
    },
  });
  symlinkSync(path.join(UI, "node_modules"), path.join(copy, "node_modules"), "dir");
  // Webpack, not Turbopack: Turbopack refuses a node_modules symlink that points
  // outside the project ("points out of the filesystem root").
  await run(path.join(copy, "node_modules", ".bin", "next"), ["build", "--webpack"], {
    cwd: copy,
    env: { ...process.env, NEXT_TELEMETRY_DISABLED: "1" },
  });

  const server = path.join(standalone, "server.js");
  const staticDir = path.join(copy, ".next", "static");
  if (!existsSync(server) || !existsSync(staticDir)) {
    throw new Error(
      `The build did not produce ${existsSync(server) ? staticDir : server}; ` +
        "has ui/next.config.mjs stopped using output: 'standalone'?",
    );
  }
  cpSync(staticDir, path.join(standalone, ".next", "static"), { recursive: true });
  if (existsSync(path.join(copy, "public"))) {
    cpSync(path.join(copy, "public"), path.join(standalone, "public"), { recursive: true });
  }
  writeFileSync(done, new Date().toISOString() + "\n", "utf-8");
  return standalone;
}

async function waitForHttp(url, ms) {
  const until = Date.now() + ms;
  while (Date.now() < until) {
    try {
      const res = await fetch(url);
      if (res.status < 500) return;
    } catch {
      // not up yet
    }
    await new Promise((r) => setTimeout(r, 300));
  }
  throw new Error(`${url} did not come up within ${ms / 1000} s`);
}

/** Start the private UI against `backendUrl`. Resolves to { url, stop }. */
export async function startUi({ backendUrl, port = PORTS.ui, log = console.log } = {}) {
  const dir = await ensureUiBuild({ log });
  const child = spawn(process.execPath, ["server.js"], {
    cwd: dir,
    env: {
      ...process.env,
      PORT: String(port),
      HOSTNAME: "127.0.0.1",
      NEXT_TELEMETRY_DISABLED: "1",
      VISTA_BACKEND_URL: backendUrl,
      VISTA_MCP_URL: `${backendUrl}/mcp`,
    },
    stdio: ["ignore", "ignore", "inherit"],
  });
  const url = `http://127.0.0.1:${port}`;
  const exited = new Promise((_, reject) =>
    child.on("exit", (code) => reject(new Error(`UI server exited ${code} (is port ${port} taken?)`))),
  );
  await Promise.race([waitForHttp(url + "/projects", 60_000), exited]);
  return {
    url,
    stop: () => {
      child.removeAllListeners("exit");
      child.kill();
    },
  };
}
