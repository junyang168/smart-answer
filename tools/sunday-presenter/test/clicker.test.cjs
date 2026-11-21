const { test } = require('node:test');
const assert = require('node:assert/strict');
const { Clicker } = require('../clicker.cjs');
function fixture(conflict) {
 const callbacks = new Map(), sent = [];
 const clicker = new Clicker({ shortcuts: {
 register(key, fn) { if(key===conflict)return false; callbacks.set(key,fn);return true; },
 unregister(key){callbacks.delete(key);}
 }, send(key,valid){sent.push({key,valid});return Promise.resolve();}, changed(){} });
 return {clicker,callbacks,sent};
}
test('clicker directions route exactly once independently of focus',()=>{
 const {clicker,callbacks,sent}=fixture();clicker.sync(false);assert.equal(callbacks.size,0);
 clicker.sync(true);clicker.sync(true);assert.equal(callbacks.size,4);
 for(const key of ['PageDown','PageUp','Right','Left'])callbacks.get(key)();
 assert.deepEqual(sent.map(x=>x.key),['Right','Left','Right','Left']);assert.ok(sent.every(x=>x.valid()));
});
test('pause and preparation release keys and cancel stale queued inputs',()=>{
 const {clicker,callbacks,sent}=fixture();clicker.sync(true);const old=callbacks.get('Right');old();
 clicker.sync(false);assert.equal(callbacks.size,0);assert.equal(sent[0].valid(),false);old();assert.equal(sent.length,1);
 clicker.sync(true);old();assert.equal(sent.length,1);
 clicker.enabled=false;clicker.sync(true);assert.equal(callbacks.size,0);
 clicker.enabled=true;clicker.sync(true);assert.equal(callbacks.size,4);clicker.release();assert.equal(callbacks.size,0);
});
test('conflicts roll back partial registration and identify the key',()=>{
 const {clicker,callbacks}=fixture('Right');clicker.sync(true);assert.equal(callbacks.size,0);assert.equal(clicker.active,false);assert.match(clicker.error,/Right/);
});
test('delivery failures are visible',async()=>{
 const {clicker,callbacks}=fixture();clicker.send=async()=>{throw Error('focus failed');};clicker.sync(true);callbacks.get('Right')();
 await new Promise(resolve=>setImmediate(resolve));assert.equal(clicker.error,'focus failed');
});
