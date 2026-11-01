import test from "node:test";
import assert from "node:assert/strict";
import { toggleParagraphPlayback } from "../src/app/components/admin/surmons/review-playback.ts";
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
test("moving back to an earlier paragraph restarts it; playback beyond the selected end has no segment stop", () => {
  const m = media(100);
  toggleParagraphPlayback(m, { start_ms: 14000, end_ms: 25000 }, true);
  assert.equal(m.currentTime, 14);
  m.pause();
  m.currentTime = 30;
  assert.equal(
    toggleParagraphPlayback(m, { start_ms: 14000, end_ms: 25000 }, false),
    undefined,
  );
});
