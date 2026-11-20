import {test} from 'node:test';
import assert from 'node:assert/strict';
import {mediaTime, sourceTime} from '../src/app/resources/wang-repository/audio/listeningClock.ts';

test('one media frame at an edit maps to the end of the outgoing interval and the start of the next', () => {
 const previous = {start:120,end:378.05,media_start:82};
 const next = {start:620.9,end:900,media_start:340.05};
 assert.ok(Math.abs(mediaTime(previous,previous.end)-mediaTime(next,next.start)) < 1e-8);
 assert.ok(Math.abs(sourceTime(next,next.media_start+2)-622.9) < 1e-8);
 assert.equal(mediaTime({start:55,end:70},56),56);
});
