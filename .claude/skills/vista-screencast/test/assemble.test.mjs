import assert from "node:assert/strict";
import { test } from "node:test";
import { cardEnable, cardFrames, readmeWindow, resample } from "../lib/assemble.mjs";
import { checkPin, checkUnchanged, pins } from "../lib/selftest.mjs";

const marks = [
  { name: "step 1 card", at: 0, frame: 0 },
  { name: "step 2 chapter", at: 5.2, frame: 40 },
  { name: "step 3 goto", at: 9.1, frame: 80 },
  { name: "step 4 click", at: 11, frame: 95 },
  { name: "step 9 run", at: 20, frame: 300 },
  { name: "step 30 card", at: 55.5, frame: 900 },
];

test("cards and chapters play full-bleed, from their first recorded frame to the next step's", () => {
  assert.deepEqual(cardFrames(marks, 1000), [[0, 40], [40, 80], [900, 1000]]);
});

test("each output frame shows the recorded frame on screen at n/30 s", () => {
  // Frames of 0.05, 0.02 and 0.1 s: 0 s → 0, 1/30 → 0, 2/30 (0.0667) → 1, 3/30 (0.1) → 2.
  assert.deepEqual(resample([0.05, 0.02, 0.1]), [0, 0, 1, 2, 2]);
});

test("a card covers exactly the output frames that show its recorded frames", () => {
  // Recorded frames 0–1 are a card; the UI starts at recorded frame 2, wherever it lands.
  const shown = [0, 0, 1, 1, 1, 2, 2, 3];
  assert.equal(cardEnable(shown, [[0, 2]]), "between(n,0,4)");
  assert.equal(cardEnable(shown, [[0, 2], [3, 4]]), "between(n,0,4)+between(n,7,7)");
  assert.equal(cardEnable(shown, []), "");
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
