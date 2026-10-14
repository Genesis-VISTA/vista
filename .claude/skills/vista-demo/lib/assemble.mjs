// ffmpeg steps (design D8). Group 1: the plain MP4 and the rehearsal contact
// sheet; the window frame and README clip come with task group 4.
import { execFileSync, spawnSync } from "node:child_process";
import { existsSync, readdirSync } from "node:fs";
import path from "node:path";

function ffmpeg(args) {
  const r = spawnSync("ffmpeg", ["-hide_banner", "-loglevel", "error", "-y", ...args], { encoding: "utf-8" });
  if (r.error?.code === "ENOENT") throw new Error("ffmpeg is not installed (macOS: `brew install ffmpeg`).");
  if (r.status !== 0) throw new Error(`ffmpeg failed:\n${r.stderr}`);
}

export function probe(file) {
  const json = execFileSync(
    "ffprobe",
    ["-v", "error", "-show_entries", "format=duration:stream=codec_type,codec_name,width,height", "-of", "json", file],
    { encoding: "utf-8" },
  );
  const d = JSON.parse(json);
  return {
    duration: Number(d.format.duration),
    streams: d.streams.map((s) => ({ type: s.codec_type, codec: s.codec_name, width: s.width, height: s.height })),
  };
}

/** out/frames → out/video.mp4: 30 fps H.264, 1920×1080, no audio. */
export function assembleVideo(out) {
  const list = path.join(out, "frames", "frames.ffconcat");
  if (!existsSync(list)) throw new Error(`No frames in ${path.dirname(list)}; run \`record\` first.`);
  const file = path.join(out, "video.mp4");
  ffmpeg([
    "-f", "concat", "-safe", "0", "-i", list,
    "-vf", "fps=30,scale=1920:1080:flags=lanczos,format=yuv420p",
    "-an", "-c:v", "libx264", "-preset", "slow", "-crf", "17", "-tune", "animation", "-movflags", "+faststart",
    file,
  ]);
  return file;
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
