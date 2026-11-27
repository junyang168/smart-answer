import test from 'node:test';
import assert from 'node:assert/strict';
import { registeredMessage, allowedDestination, allowedBySettings } from '../outgoing.mjs';

test('speaker follow-up is independently authorized only to the administrator group', () => {
  const config={enabled:true,groups:['1@g.us'],admin_group:'2@g.us',allow_speaker_followup:true,allow_questions:false,allow_reminders:false};
  const key='speaker-ppt-followup:2026-11-08:version';
  assert.equal(registeredMessage(key,'【講員PPT跟進提醒】請跟進'),true);
  assert.equal(registeredMessage(key,'freeform message'),false);
  assert.equal(allowedBySettings(config,key),true);
  assert.equal(allowedDestination(config,key,'2@g.us'),true);
  assert.equal(allowedDestination(config,key,'1@g.us'),false);
  assert.equal(allowedDestination({...config,allow_speaker_followup:false},key,'2@g.us'),false);
  assert.equal(allowedDestination({...config,enabled:false},key,'2@g.us'),false);
  assert.equal(allowedDestination({...config,admin_group:'1@g.us'},key,'1@g.us'),false);
});
