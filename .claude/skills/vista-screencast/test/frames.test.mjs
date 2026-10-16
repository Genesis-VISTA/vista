import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import { mkdtempSync, readFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { test } from "node:test";
import { startFrames } from "../lib/frames.mjs";

/** A page whose CDP session emits the screencast frames the test sends it, `step` seconds apart. */
function fakePage(step = 1) {
  const cdp = new EventEmitter();
  cdp.send = async () => {};
  let t = 0;
  const frame = () => cdp.emit("Page.screencastFrame", { data: "", metadata: { timestamp: (t += step) }, sessionId: 1 });
  return { page: { context: () => ({ newCDPSession: async () => cdp }) }, frame };
}

test("a hold before the first frame drops it: the video opens on what follows", async () => {
  const dir = path.join(mkdtempSync(path.join(os.tmpdir(), "frames-")), "frames");
  const { page, frame } = fakePage();
  const frames = await startFrames(page, dir);
  frames.mark("step 1 card");
  frames.hold();
  frame(); // the blank start page
  frames.resume();
  frame(); // the card
  const { count, marks } = await frames.stop();
  assert.equal(count, 1);
  assert.deepEqual(marks, [{ name: "step 1 card", at: 0 }]);
});

test("a resumed mark starts where its frame does on the list's rounded timeline", async () => {
  const dir = path.join(mkdtempSync(path.join(os.tmpdir(), "frames-")), "frames");
  const { page, frame } = fakePage(0.01666666); // each duration rounds up to 0.0167
  const frames = await startFrames(page, dir);
  for (let i = 0; i < 3000; i++) frame();
  frames.mark("step 9 card");
  frames.hold();
  frames.resume();
  frame(); // frame 3000
  const { marks } = await frames.stop();
  assert.equal(marks[0].at, Math.floor(3000 * 0.0167 * 1000) / 1000); // 50.1 s, not the 49.99 s of the frames' clock
});

test("frames that arrive while held are dropped, so the last one before holds", async () => {
  const dir = path.join(mkdtempSync(path.join(os.tmpdir(), "frames-")), "frames");
  const { page, frame } = fakePage();
  const frames = await startFrames(page, dir);
  frame(); // t=1: the faded card
  frames.hold();
  frame(); // t=2, t=3: the UI loading
  frame();
  frames.resume();
  frame(); // t=4: the settled UI
  const { count } = await frames.stop();
  assert.equal(count, 2);
  const list = readFileSync(path.join(dir, "frames.ffconcat"), "utf-8");
  assert.match(list, /file 'f000000\.jpg'\nduration 3\.0000\nfile 'f000001\.jpg'/);
});

test("resume moves the latest mark to the first frame kept", async () => {
  const dir = path.join(mkdtempSync(path.join(os.tmpdir(), "frames-")), "frames");
  const { page, frame } = fakePage();
  const frames = await startFrames(page, dir);
  frame(); // t=1, 0 s into the video
  frames.mark("step 1 card");
  frames.mark("step 2 goto");
  frames.hold();
  frame(); // t=2, dropped
  frames.resume();
  frame(); // t=3: the goto's first visible frame, 2 s into the video
  frame(); // t=4: later frames leave the mark alone
  const { marks } = await frames.stop();
  assert.deepEqual(marks.map((m) => [m.name, m.at]), [["step 1 card", 0], ["step 2 goto", 2]]);
});
