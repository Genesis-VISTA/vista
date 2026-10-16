import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import { mkdtempSync, readFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { test } from "node:test";
import { startFrames } from "../lib/frames.mjs";

/** A page whose CDP session emits the screencast frames the test sends it. */
function fakePage() {
  const cdp = new EventEmitter();
  cdp.send = async () => {};
  let t = 0;
  const frame = () => cdp.emit("Page.screencastFrame", { data: "", metadata: { timestamp: (t += 1) }, sessionId: 1 });
  return { page: { context: () => ({ newCDPSession: async () => cdp }) }, frame };
}

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

test("restamp moves the latest mark to now", async () => {
  const dir = path.join(mkdtempSync(path.join(os.tmpdir(), "frames-")), "frames");
  const { page, frame } = fakePage();
  const frames = await startFrames(page, dir);
  frame();
  frames.mark("step 1 card");
  frames.mark("step 2 goto");
  await new Promise((r) => setTimeout(r, 120));
  frames.restamp();
  frame();
  const { marks } = await frames.stop();
  assert.equal(marks[1].name, "step 2 goto");
  assert.ok(marks[1].at >= 0.1, `restamped to ${marks[1].at}`);
  assert.ok(marks[0].at < 0.1);
});
