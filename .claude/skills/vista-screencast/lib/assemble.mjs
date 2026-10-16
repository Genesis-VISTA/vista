// ffmpeg steps (design D8): the framed MP4, the README clip, and the
// rehearsal contact sheet.
import { execFileSync, spawnSync } from "node:child_process";
import { existsSync, mkdirSync, readFileSync, readdirSync } from "node:fs";
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

/** [start, end] seconds of every card and chapter step: shown full-bleed, not framed. */
export function cardIntervals(marks, duration) {
  const steps = stepMarks(marks);
  return steps
    .map((s, i) => (s.kind === "card" || s.kind === "chapter" ? [s.at, steps[i + 1]?.at ?? duration] : null))
    .filter(Boolean);
}

/** fps=30 told to round up, so a source frame starting at t is first shown on output frame ceil(t·30). */
const FPS = "fps=30:round=up";

/**
 * A card boundary, moved to halfway before the output frame that first shows
 * the frame at t, so that output frame is drawn with the step that starts at t.
 * In whole microseconds, the concat demuxer's unit, so no float lands t on the
 * wrong side of a frame.
 */
export function onFrameGrid(t, fps = 30) {
  const first = Math.ceil((Math.round(t * 1e6) * fps) / 1e6);
  return Math.max(0, (first - 0.5) / fps);
}

function concatDuration(list) {
  return readFileSync(list, "utf-8")
    .split("\n")
    .filter((l) => l.startsWith("duration "))
    .reduce((s, l) => s + Number(l.slice(9)), 0);
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
  if (!framed) {
    ffmpeg(["-f", "concat", "-safe", "0", "-i", list, "-vf", `${FPS},scale=1920:1080:flags=lanczos`, ...encode]);
    return file;
  }
  const duration = concatDuration(list);
  const { bg, mask } = await renderFrame(path.join(out, "frame"));
  const cards = cardIntervals(readMarks(out), duration);
  const F = FRAME;
  const graph = [
    `[0:v]${FPS},scale=1920:1080:flags=lanczos,format=rgba,split=2[full][ui0]`,
    `[ui0]scale=${F.w}:${F.h}:flags=lanczos,pad=1920:1080:${F.x}:${F.y + F.title}:color=black@0,format=rgba[ui]`,
    `[2:v]format=gray[m]`,
    `[ui][m]alphamerge[uia]`,
    `[1:v]format=rgba[bg]`,
    `[bg][uia]overlay=0:0:shortest=1${cards.length ? "[framed]" : ",format=yuv420p[v]"}`,
  ];
  if (cards.length) {
    const on = cards.map(([a, b]) => `gte(t,${onFrameGrid(a).toFixed(4)})*lt(t,${onFrameGrid(b).toFixed(4)})`).join("+");
    graph.push(`[framed][full]overlay=0:0:enable='${on}',format=yuv420p[v]`);
  }
  ffmpeg([
    "-f", "concat", "-safe", "0", "-i", list,
    "-loop", "1", "-framerate", "30", "-t", duration.toFixed(3), "-i", bg,
    "-loop", "1", "-framerate", "30", "-t", duration.toFixed(3), "-i", mask,
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
