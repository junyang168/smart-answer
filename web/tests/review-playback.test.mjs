import test from "node:test";
import assert from "node:assert/strict";
import {
  toggleParagraphPlayback,
  prepareParagraphPlayback,
  seekParagraphPlayback,
  stopAtParagraphEnd,
} from "../src/app/components/admin/surmons/review-playback.ts";
function media(time, paused = true) {
  return {
    currentTime: time,
    paused,
    play() {
      this.paused = false;
      return Promise.resolve();
    },
    pause() {
      this.paused = true;
    },
  };
}
test("a newly selected paragraph starts at its source start, whether previous media was paused or playing", () => {
  for (const paused of [true, false]) {
    const m = media(300, paused);
    assert.equal(
      toggleParagraphPlayback(m, { start_ms: 28000, end_ms: 50000 }, true),
      50,
    );
    assert.equal(m.currentTime, 28);
    assert.equal(m.paused, false);
  }
});
test("same paragraph pauses/resumes at the current position and preserves deliberate seeking", () => {
  const m = media(36, false);
  const binding = { start_ms: 28000, end_ms: 50000 };
  toggleParagraphPlayback(m, binding, false);
  assert.equal(m.paused, true);
  assert.equal(m.currentTime, 36);
  toggleParagraphPlayback(m, binding, false);
  assert.equal(m.paused, false);
  assert.equal(m.currentTime, 36);
  m.pause();
  m.currentTime = 31;
  toggleParagraphPlayback(m, binding, false);
  assert.equal(m.currentTime, 31);
});
test("moving back or replaying after the paragraph end starts at the selected start", () => {
  const m = media(100);
  toggleParagraphPlayback(m, { start_ms: 14000, end_ms: 25000 }, true);
  assert.equal(m.currentTime, 14);
  m.pause();
  m.currentTime = 30;
  assert.equal(
    toggleParagraphPlayback(m, { start_ms: 14000, end_ms: 25000 }, false),
    25,
  );
  assert.equal(m.currentTime, 14);
});

test("paragraph end pauses and clamps overshoot; replay and native play remain bounded", () => {
  const binding = { start_ms: 14000, end_ms: 25000 };
  const m = media(24.8, false);
  assert.equal(stopAtParagraphEnd(m, binding), false);
  m.currentTime = 25.2;
  assert.equal(stopAtParagraphEnd(m, binding), true);
  assert.equal(m.paused, true);
  assert.equal(m.currentTime, 25);
  assert.equal(prepareParagraphPlayback(m, binding, false), true);
  assert.equal(m.currentTime, 14);
  m.play();
  m.currentTime = 25;
  assert.equal(stopAtParagraphEnd(m, binding), true);
  assert.equal(m.paused, true);
});

test("fast forward pauses at the paragraph end and rewind stays within the selected paragraph", () => {
  const binding = { start_ms: 14000, end_ms: 25000 };
  const m = media(24, false);
  seekParagraphPlayback(m, binding, 15, false);
  assert.equal(m.currentTime, 25);
  assert.equal(m.paused, true);
  seekParagraphPlayback(m, binding, -5, false);
  assert.equal(m.currentTime, 20);
  seekParagraphPlayback(m, binding, -15, false);
  assert.equal(m.currentTime, 14);
  m.currentTime = 300;
  seekParagraphPlayback(m, binding, 5, true);
  assert.equal(m.currentTime, 19);
});

test("an unbound paragraph cannot start unbounded playback", () => {
  const m = media(10);
  toggleParagraphPlayback(m, undefined, true);
  assert.equal(m.paused, true);
});

test("native seeking already inside a paragraph does not issue another seek", () => {
  let position = 20;
  let seeks = 0;
  const m = {
    ...media(20),
    get currentTime() {
      return position;
    },
    set currentTime(value) {
      seeks++;
      position = value;
    },
  };
  seekParagraphPlayback(m, { start_ms: 14000, end_ms: 25000 }, 0, false);
  assert.equal(seeks, 0);
  position = 40;
  seekParagraphPlayback(m, { start_ms: 14000, end_ms: 25000 }, 0, false);
  assert.equal(position, 25);
  assert.equal(seeks, 1);
});
