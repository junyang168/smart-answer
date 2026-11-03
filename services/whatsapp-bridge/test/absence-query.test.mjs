import test from 'node:test';
import assert from 'node:assert/strict';
import {registeredMessage,allowedBySettings,allowedDestination} from '../outgoing.mjs';
test('absence answers are registered and restricted to the exact church group with fresh enabled settings',()=>{
 const config={enabled:true,groups:['church','sermon','songs'],availability_group:'church',admin_group:'admin',allow_questions:false,allow_reminders:false};
 const key='absence-query:original-message';
 assert.equal(registeredMessage(key,'【主日請假查詢】主日：2026-11-08'),true);
 assert.equal(registeredMessage(key,'freeform'),false);
 assert.equal(allowedBySettings(config,key),true);
 for(const group of ['sermon','songs','admin','unknown'])assert.equal(allowedDestination(config,key,group),false);
 assert.equal(allowedDestination(config,key,'church'),true);
 assert.equal(allowedDestination({...config,enabled:false},key,'church'),false);
 assert.equal(allowedDestination({...config,availability_group:'songs'},key,'church'),false);
 assert.equal(allowedDestination({...config,admin_group:'church'},key,'church'),false);
 assert.equal(allowedDestination({...config,availability_group:undefined},key,'church'),false);
});
