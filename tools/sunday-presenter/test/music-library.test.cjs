const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const os=require('node:os');
const path=require('node:path');
const {MusicLibrary,byteRange,musicResponse}=require('../music-library.cjs');
function fixture(t){const root=fs.mkdtempSync(path.join(os.tmpdir(),'sunday-music-test-'));t.after(()=>fs.rmSync(root,{recursive:true,force:true}));return root;}
test('three slots persist privately without exposing file paths in snapshots',t=>{
 const root=fixture(t),lib=new MusicLibrary(path.join(root,'state.json'));
 for(const slot of ['hymn','responseHymn','doxology']){const p=path.join(root,slot+'.mp3');fs.writeFileSync(p,'0123456789');lib.assign(slot,p);}
 assert.equal(lib.tracks.length,3);assert.equal(Object.keys(lib.slots).length,3);
 assert.ok(!JSON.stringify(lib.snapshot()).includes(root));
 const restored=new MusicLibrary(lib.file);assert.deepEqual(restored.snapshot(),lib.snapshot());
 assert.equal(fs.statSync(lib.file).mode&0o777,0o600);
 const replacement=path.join(root,'new.mp3');fs.writeFileSync(replacement,'abc');lib.assign('hymn',replacement);
 assert.equal(lib.tracks.length,3);assert.equal(lib.get(lib.slots.hymn).name,'new.mp3');
 assert.ok(fs.existsSync(path.join(root,'hymn.mp3')),'removing a library reference never deletes the original');
});
test('file selection rejects empty, missing and non-MP3 input without changing assignments',t=>{
 const root=fixture(t),lib=new MusicLibrary(path.join(root,'state.json'));
 const p=path.join(root,'empty.mp3');fs.writeFileSync(p,'');assert.throws(()=>lib.assign('hymn',p));
 assert.throws(()=>lib.assign('wrong-slot',p));assert.throws(()=>lib.assign('hymn',path.join(root,'missing.mp3')));
 assert.deepEqual(lib.slots,{});
});
test('byte ranges support seeks and reject malformed/multiple/invalid ranges',()=>{
 assert.deepEqual(byteRange('bytes=2-5',10),{start:2,end:5,partial:true});
 assert.deepEqual(byteRange('bytes=8-',10),{start:8,end:9,partial:true});
 assert.deepEqual(byteRange('bytes=-3',10),{start:7,end:9,partial:true});
 for(const range of ['bytes=12-','bytes=4-2','bytes=0-2,4-6','bytes=-0','bytes=NaN-','bytes=9007199254740993-'])assert.equal(byteRange(range,10),null);
});
test('only selected opaque file IDs can stream and ranges return correct bytes',async t=>{
 const root=fixture(t),lib=new MusicLibrary(path.join(root,'state.json'));const file=path.join(root,'sample.mp3');fs.writeFileSync(file,'0123456789');lib.assign('hymn',file);
 const url='sunday-music://track/'+lib.slots.hymn;
 let r=await musicResponse(new Request(url,{headers:{range:'bytes=3-6'}}),lib);assert.equal(r.status,206);assert.equal(r.headers.get('content-range'),'bytes 3-6/10');assert.equal(await r.text(),'3456');
 r=await musicResponse(new Request(url,{method:'HEAD'}),lib);assert.equal(r.headers.get('content-length'),'10');assert.equal(await r.text(),'');
 for(const bad of ['sunday-music://track/not-a-file','sunday-music://track/'+lib.slots.hymn+'?path=/etc/passwd','sunday-music://other/'+lib.slots.hymn])assert.equal((await musicResponse(new Request(bad),lib)).status,404);
 assert.equal((await musicResponse(new Request(url,{headers:{range:'bytes=11-'}}),lib)).status,416);
 fs.unlinkSync(file);assert.equal((await musicResponse(new Request(url),lib)).status,404);
});
