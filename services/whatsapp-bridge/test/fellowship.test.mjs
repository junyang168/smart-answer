import test from 'node:test';
import assert from 'node:assert/strict';
import {registeredMessage,allowedBySettings,allowedDestination} from '../outgoing.mjs';
test('fellowship collection posts only to the dedicated admin group independently of source questions',()=>{
 const cfg={enabled:true,allow_fellowship:true,groups:['church','sermon','songs'],admin_group:'admin',allow_questions:false,allow_reminders:false};
 const key='fellowship:request:2026-11-06';
 assert.equal(registeredMessage(key,'【團契助理】請補充'),true);
 assert.equal(registeredMessage(key,'arbitrary message'),false);
 assert.equal(allowedBySettings(cfg,key),true);
 assert.equal(allowedDestination(cfg,key,'admin'),true);
 for(const group of cfg.groups)assert.equal(allowedDestination(cfg,key,group),false);
 assert.equal(allowedDestination({...cfg,allow_fellowship:false},key,'admin'),false);
 assert.equal(allowedDestination({...cfg,admin_group:'church'},key,'church'),false);
 assert.equal(allowedDestination({...cfg,enabled:false},key,'admin'),false);
});
