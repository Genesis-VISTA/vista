// ffmpeg steps (design D8): the framed MP4, the README clip, and the
// rehearsal contact sheet.
import { execFileSync, spawnSync } from "node:child_process";
import { existsSync, mkdirSync, readFileSync, readdirSync, rmSync, symlinkSync } from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { chromium } from "playwright";
import { REPO_ROOT, SKILL_DIR } from "./paths.mjs";

/** Where the UI sits in the 1920×1080 frame: a 16:9 content area under a title bar. */
export const FRAME = { x: 110, y: 40, w: 1700, h: 956, title: 44, r: 16 };
const README = { maxSeconds: 15, defaultSeconds: 12, width: 960 };

function ffmpeg(args) {
  const r = spawnSync("ffmpeg", ["-hide_banner", "-loglevel", "error", "-y", ...args], { encoding: "utf-8" });
  if (r.error?.code === "ENOENT") throw new Error("ffmpeg is not installed (macOS: `brew install ffmpeg`).");
  if (r.status !== 0) throw new Error(`ffmpeg failed:\n${r.stderr}`);
}

export function probe(file) {
  const json = execFileSync(
    "ffprobe",
    ["-v", "error", "-show_entries", "format=duration,size:stream=codec_type,codec_name,width,height", "-of", "json", file],
    { encoding: "utf-8" },
  );
  const d = JSON.parse(json);
  return {
    duration: Number(d.format.duration),
    size: Number(d.format.size),
    streams: d.streams.map((s) => ({ type: s.codec_type, codec: s.codec_name, width: s.width, height: s.height })),
  };
}

/** Render the frame's backdrop and content mask (templates/frame.html) into `dir`. */
export async function renderFrame(dir) {
  mkdirSync(dir, { recursive: true });
  const bg = path.join(dir, "frame-bg.png");
  const mask = path.join(dir, "frame-mask.png");
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
    const base = pathToFileURL(path.join(SKILL_DIR, "templates", "frame.html")).href;
    const icon = pathToFileURL(path.join(REPO_ROOT, "electron", "assets", "icon.svg")).href;
    for (const [mode, file] of [["bg", bg], ["mask", mask]]) {
      await page.goto(`${base}?${new URLSearchParams({ mode, icon, ...FRAME })}`);
      await page.waitForTimeout(150);
      await page.screenshot({ path: file });
    }
  } finally {
    await browser.close();
  }
  return { bg, mask };
}

const readMarks = (out) => {
  const file = path.join(out, "frames", "marks.json");
  return existsSync(file) ? JSON.parse(readFileSync(file, "utf-8")) : [];
};
const stepMarks = (marks) =>
  marks
    .filter((m) => m.name.startsWith("step "))
    .map((m) => ({ ...m, kind: m.name.split(" ")[2] }));

/**
 * [first, end) recorded frames of every card and chapter step: shown full-bleed,
 * not framed. A step's `frame` is the first recorded frame it applies to.
 */
export function cardFrames(marks, frameCount) {
  const steps = stepMarks(marks);
  return steps
    .map((s, i) => (s.kind === "card" || s.kind === "chapter" ? [s.frame, steps[i + 1]?.frame ?? frameCount] : null))
    .filter(Boolean);
}

const FPS = 30;

/** The recorded frames and their durations, from frames.ffconcat. */
export function readList(list) {
  const entries = [];
  for (const line of readFileSync(list, "utf-8").split("\n")) {
    const file = line.match(/^file '(.+)'$/);
    if (file) entries.push({ file: file[1], dur: null });
    const dur = line.match(/^duration (.+)$/);
    if (dur) entries[entries.length - 1].dur = Number(dur[1]);
  }
  return entries.filter((e) => e.dur !== null); // the last file is repeated without one
}

/**
 * Which recorded frame each output frame shows: the one on screen at n/fps.
 * Doing this here, rather than with ffmpeg's fps filter, is what lets a card
 * boundary fall on exactly the output frame where the step's first frame appears.
 */
export function resample(durations, fps = FPS) {
  const total = durations.reduce((s, d) => s + d, 0);
  const count = Math.max(1, Math.round(total * fps));
  const shown = [];
  let src = 0;
  let end = durations[0];
  for (let n = 0; n < count; n++) {
    const t = n / fps;
    while (src < durations.length - 1 && t >= end - 1e-9) end += durations[++src];
    shown.push(src);
  }
  return shown;
}

/** `between(n,a,b)` terms for the runs of output frames whose recorded frame is in a card. */
export function cardEnable(shown, cards) {
  const inCard = (src) => cards.some(([a, b]) => src >= a && src < b);
  const runs = [];
  shown.forEach((src, n) => {
    if (!inCard(src)) return;
    const last = runs[runs.length - 1];
    if (last && last[1] === n - 1) last[1] = n;
    else runs.push([n, n]);
  });
  return runs.map(([a, b]) => `between(n,${a},${b})`).join("+");
}

/** Link the output frames, in order, as seq/000000.jpg…: ffmpeg reads them at exactly 30 fps. */
function writeSequence(framesDir, entries, shown) {
  const dir = path.join(framesDir, "seq");
  rmSync(dir, { recursive: true, force: true });
  mkdirSync(dir);
  shown.forEach((src, n) => symlinkSync(path.join("..", entries[src].file), path.join(dir, `${String(n).padStart(6, "0")}.jpg`)));
  return path.join(dir, "%06d.jpg");
}

/**
 * out/frames → out/video.mp4: 30 fps H.264, 1920×1080, no audio. The UI sits
 * in a neutral window on a gradient backdrop; cards play full-bleed.
 * `framed: false` writes the UI full-bleed throughout.
 */
export async function assembleVideo(out, { framed = true } = {}) {
  const list = path.join(out, "frames", "frames.ffconcat");
  if (!existsSync(list)) throw new Error(`No frames in ${path.dirname(list)}; run \`record\` first.`);
  const file = path.join(out, "video.mp4");
  const encode = ["-an", "-c:v", "libx264", "-preset", "slow", "-crf", "17", "-tune", "animation", "-pix_fmt", "yuv420p", "-movflags", "+faststart", file];
  const entries = readList(list);
  const shown = resample(entries.map((e) => e.dur));
  const seq = ["-framerate", String(FPS), "-i", writeSequence(path.dirname(list), entries, shown)];
  if (!framed) {
    ffmpeg([...seq, "-vf", "scale=1920:1080:flags=lanczos", ...encode]);
    return file;
  }
  const duration = (shown.length / FPS).toFixed(3);
  const { bg, mask } = await renderFrame(path.join(out, "frame"));
  const on = cardEnable(shown, cardFrames(readMarks(out), entries.length));
  const F = FRAME;
  const graph = [
    `[0:v]scale=1920:1080:flags=lanczos,format=rgba,split=2[full][ui0]`,
    `[ui0]scale=${F.w}:${F.h}:flags=lanczos,pad=1920:1080:${F.x}:${F.y + F.title}:color=black@0,format=rgba[ui]`,
    `[2:v]format=gray[m]`,
    `[ui][m]alphamerge[uia]`,
    `[1:v]format=rgba[bg]`,
    `[bg][uia]overlay=0:0:shortest=1${on ? "[framed]" : ",format=yuv420p[v]"}`,
  ];
  if (on) graph.push(`[framed][full]overlay=0:0:enable='${on}',format=yuv420p[v]`);
  ffmpeg([
    ...seq,
    "-loop", "1", "-framerate", String(FPS), "-t", duration, "-i", bg,
    "-loop", "1", "-framerate", String(FPS), "-t", duration, "-i", mask,
    "-filter_complex", graph.join(";"), "-map", "[v]", ...encode,
  ]);
  return file;
}

/**
 * The README clip's [start, end]: the `readme: start`/`readme: end` marks, or
 * by default the first scene: from the first step after the opening cards,
 * up to the next card or 12 s. Never longer than 15 s.
 */
export function readmeWindow(marks, duration) {
  const at = (name) => marks.find((m) => m.name === name)?.at;
  let start = at("readme-start");
  let end = at("readme-end");
  if (start === undefined) {
    const steps = stepMarks(marks);
    const first = steps.findIndex((s) => s.kind !== "card" && s.kind !== "chapter");
    start = first >= 0 ? steps[first].at : 0;
    const nextCard = steps.slice(first + 1).find((s) => s.kind === "card" || s.kind === "chapter");
    end ??= Math.min(nextCard?.at ?? duration, start + README.defaultSeconds);
  }
  end ??= Math.min(duration, start + README.defaultSeconds);
  end = Math.min(end, start + README.maxSeconds, duration);
  if (!(end > start)) throw new Error(`The README clip would be empty (start ${start}s, end ${end}s); check the readme: marks.`);
  return [start, end];
}

/** out/video.mp4 → out/readme.webm (VP9) and out/readme.gif, 960 px wide, ≤ 15 s. */
export function assembleReadme(out) {
  const video = path.join(out, "video.mp4");
  if (!existsSync(video)) throw new Error(`No ${video}; assemble the video first.`);
  const [start, end] = readmeWindow(readMarks(out), probe(video).duration);
  const cut = ["-ss", start.toFixed(3), "-t", (end - start).toFixed(3), "-i", video];
  const webm = path.join(out, "readme.webm");
  const gif = path.join(out, "readme.gif");
  ffmpeg([...cut, "-an", "-vf", `scale=${README.width}:-2:flags=lanczos`, "-c:v", "libvpx-vp9", "-crf", "36", "-b:v", "0", "-row-mt", "1", webm]);
  ffmpeg([
    ...cut,
    "-vf", `fps=12,scale=${README.width}:-1:flags=lanczos,split[a][b];[a]palettegen=stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=4:diff_mode=rectangle`,
    "-loop", "0", gif,
  ]);
  return { webm, gif, start, end };
}

/** One image of every rehearsal screenshot, to check a script at a glance. */
export function contactSheet(shotsDir, file) {
  const shots = readdirSync(shotsDir).filter((f) => f.endsWith(".png")).sort();
  if (!shots.length) throw new Error(`No screenshots in ${shotsDir}.`);
  const cols = 4;
  const rows = Math.ceil(shots.length / cols);
  const inputs = shots.flatMap((s) => ["-i", path.join(shotsDir, s)]);
  const scaled = shots.map((_, i) => `[${i}:v]scale=480:-1[s${i}]`).join(";");
  const pad = rows * cols - shots.length;
  const blanks = Array.from({ length: pad }, (_, i) => `color=c=white:s=480x270:d=1[b${i}]`).join(";");
  const all = [...shots.map((_, i) => `[s${i}]`), ...Array.from({ length: pad }, (_, i) => `[b${i}]`)].join("");
  const graph = [scaled, blanks, `${all}xstack=inputs=${rows * cols}:layout=${layout(cols, rows)}:fill=white[out]`]
    .filter(Boolean)
    .join(";");
  ffmpeg([...inputs, "-filter_complex", graph, "-map", "[out]", "-frames:v", "1", file]);
  return file;
}

function layout(cols, rows) {
  const cells = [];
  for (let r = 0; r < rows; r++) for (let c = 0; c < cols; c++) cells.push(`${c * 480}_${r * 270}`);
  return cells.join("|");
}
