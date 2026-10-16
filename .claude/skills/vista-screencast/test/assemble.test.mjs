import assert from "node:assert/strict";
import { test } from "node:test";
import { cardIntervals, onFrameGrid, readmeWindow } from "../lib/assemble.mjs";
import { checkPin, checkUnchanged, pins } from "../lib/selftest.mjs";

const marks = [
  { name: "step 1 card", at: 0 },
  { name: "step 2 chapter", at: 5.2 },
  { name: "step 3 goto", at: 9.1 },
  { name: "step 4 click", at: 11 },
  { name: "step 9 run", at: 20 },
  { name: "step 30 card", at: 55.5 },
];

test("cards and chapters play full-bleed, from their start to the next step", () => {
  assert.deepEqual(cardIntervals(marks, 60), [[0, 5.2], [5.2, 9.1], [55.5, 60]]);
});

test("a boundary falls just before the output frame that first shows its step", () => {
  // A card frame starting at 85.743 s is first shown on output frame 2573 (round up).
  const at = onFrameGrid(85.743);
  assert.ok(at > 2572 / 30 && at < 2573 / 30, `at ${at}`);
  // A frame exactly on the grid is shown on that frame, not the next.
  const on = onFrameGrid(10.2);
  assert.ok(on > 305 / 30 && on < 306 / 30, `at ${on}`);
  assert.equal(onFrameGrid(0), 0);
});

test("the README clip defaults to the first scene, 12 s", () => {
  assert.deepEqual(readmeWindow(marks, 60), [9.1, 21.1]);
});

test("readme marks choose the clip, never longer than 15 s", () => {
  const m = [...marks, { name: "readme-start", at: 20 }, { name: "readme-end", at: 50 }];
  assert.deepEqual(readmeWindow(m, 60), [20, 35]);
  assert.deepEqual(readmeWindow([...marks, { name: "readme-start", at: 30 }], 60), [30, 42]);
});

test("the default clip stops at the next card", () => {
  const short = [{ name: "step 1 card", at: 0 }, { name: "step 2 goto", at: 4 }, { name: "step 3 card", at: 9 }];
  assert.deepEqual(readmeWindow(short, 12), [4, 9]);
});

test("a Playwright pin that differs from ui/ fails, saying what to set", () => {
  assert.equal(checkPin({ skill: "1.62.1", ui: "1.62.1" }), "1.62.1");
  assert.throws(() => checkPin({ skill: "1.63.0", ui: "1.62.1" }), /pinned to 1\.63\.0 .* ui\/ locks 1\.62\.1\. Set the skill's pin to "1\.62\.1"/);
  const real = pins();
  assert.equal(real.skill, real.ui, "this checkout's pins agree");
});

test("a change to the checkout is named", () => {
  assert.doesNotThrow(() => checkUnchanged(" M a\n", " M a\n"));
  assert.throws(() => checkUnchanged(" M a\n", " M a\n?? ui/.next/\n"), /\?\? ui\/\.next\//);
});
