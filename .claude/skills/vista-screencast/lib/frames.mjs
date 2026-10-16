// Frames from Chrome's CDP screencast (design D8). Chrome sends a frame only when
// the page changes, so each frame keeps its timestamp and the ffconcat list gives
// it the duration until the next one.
import { mkdirSync, rmSync, writeFileSync } from "node:fs";
import path from "node:path";

export async function startFrames(page, dir, { quality = 92, width = 1920, height = 1080 } = {}) {
  rmSync(dir, { recursive: true, force: true });
  mkdirSync(dir, { recursive: true });
  const cdp = await page.context().newCDPSession(page);
  const frames = [];
  const marks = [];
  let firstArrival = null; // wall clock at the first frame, which is 0 s in the video
  let held = false;
  let markNextFrame = false; // resume(): the latest mark moves to the next frame kept
  cdp.on("Page.screencastFrame", ({ data, metadata, sessionId }) => {
    cdp.send("Page.screencastFrameAck", { sessionId }).catch(() => {});
    if (held) return; // before the first frame too, so a video can open on its first card
    firstArrival ??= Date.now() / 1000;
    const file = `f${String(frames.length).padStart(6, "0")}.jpg`;
    writeFileSync(path.join(dir, file), Buffer.from(data, "base64"));
    frames.push({ file, t: metadata.timestamp });
    if (markNextFrame && marks.length) marks[marks.length - 1].frame = frames.length - 1;
    markNextFrame = false;
  });
  await cdp.send("Page.startScreencast", { format: "jpeg", quality, maxWidth: width, maxHeight: height, everyNthFrame: 1 });

  return {
    /** Name the current moment (a step's start, readme bounds); seconds into the video. */
    mark(name) {
      const now = Date.now() / 1000;
      marks.push({ name, at: Number((now - (firstArrival ?? now)).toFixed(2)) });
    },
    /** Drop frames until resume(): the last frame before holds while a page loads out of sight. */
    hold() {
      held = true;
    },
    /** Keep frames again; the latest mark moves to the first one kept, where the step is first seen. */
    resume() {
      held = false;
      markNextFrame = true;
    },
    async stop() {
      await cdp.send("Page.stopScreencast").catch(() => {});
      if (frames.length === 0) throw new Error("The screencast produced no frames.");
      let list = "ffconcat version 1.0\n";
      const startsAt = []; // each frame's start on the video's timeline: the list's own rounded durations
      let elapsed = 0;
      frames.forEach((f, i) => {
        const next = frames[i + 1];
        const dur = (next ? Math.max(0.001, next.t - f.t) : 0.5).toFixed(4);
        startsAt.push(elapsed);
        elapsed += Number(dur);
        list += `file '${f.file}'\nduration ${dur}\n`;
      });
      list += `file '${frames[frames.length - 1].file}'\n`; // ffconcat honours the last duration only when repeated
      // Every mark names the first frame it applies to: the one resume() kept, else the
      // first to start at or after it. A mark tied by resume() starts where its frame does.
      for (const m of marks) {
        if (m.frame === undefined) {
          const i = startsAt.findIndex((s) => s >= m.at - 1e-9);
          m.frame = i < 0 ? frames.length : i;
        } else {
          m.at = Math.round(startsAt[m.frame] * 1e6) / 1e6;
        }
      }
      writeFileSync(path.join(dir, "frames.ffconcat"), list, "utf-8");
      writeFileSync(path.join(dir, "marks.json"), JSON.stringify(marks, null, 2) + "\n", "utf-8");
      return { count: frames.length, seconds: frames[frames.length - 1].t - frames[0].t, marks };
    },
  };
}
