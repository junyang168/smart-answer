import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readBookmark, resumeClip} from '../src/app/resources/wang-repository/audio/listeningProgress.ts';
const clips = [
 {section:'1.2-front',source:'source1',start:273,end:1216},
 {section:'1.3',source:'source1',start:1216,end:1616},
 {section:'1.2-continued',source:'source1',start:1619,end:2101},
 {section:'3.2',source:'source2',start:39.4,end:1203},
];
const bookmark={version:'v1',section:'1.2-continued',source:'source1',time:1849.7};
test('reload resumes exact position in continuation, not its first occurrence',()=>{
 const saved=readBookmark({getItem:()=>JSON.stringify(bookmark)},'key');
 assert.deepEqual(resumeClip(clips,saved,'v1',{}),{index:2,time:1849.7});
 assert.deepEqual(resumeClip(clips,saved,'v1',{section:'1.2-continued'}),{index:2,time:1849.7});
});
test('explicit section and timestamp links take precedence over local bookmark',()=>{
 assert.equal(resumeClip(clips,bookmark,'v1',{section:'3.2'}),null);
 assert.deepEqual(resumeClip(clips,bookmark,'v1',{section:'3.2',source:'source2',time:70}),{index:3,time:70});
});
test('changed sources, removed ranges and wrong recordings do not reuse progress',()=>{
 assert.equal(resumeClip(clips,bookmark,'v2',{}),null);
 assert.equal(resumeClip(clips,{...bookmark,time:2200},'v1',{}),null);
 assert.equal(resumeClip(clips,{...bookmark,source:'source2'},'v1',{}),null);
});
test('unavailable or corrupt browser storage cannot break playback',()=>{
 for(const getItem of [()=>'{',()=>'{"version":"v1","time":"x"}',()=>{throw Error('private mode');}])
  assert.equal(readBookmark({getItem},'key'),null);
});
test('normal parent links and historical passage assignments resolve by recording time',()=>{
 const catalog=clips.map(c=>({...c,parent:c.section.startsWith('1.')?'mat-5-1-12':'mat-5-17-20'}));
 assert.deepEqual(resumeClip(catalog,null,'v1',{section:'mat-5-1-12',source:'source1',time:1849.7}),{index:2,time:1849.7});
 assert.deepEqual(resumeClip(catalog,null,'v1',{section:'old-passage',source:'source2',time:70}),{index:3,time:70});
 assert.equal(resumeClip(catalog,null,'v1',{section:'old-passage',source:'unknown',time:70}),null);
});

test('explicit times in removed audio use the next retained interval; terminal deletions stop at the end', () => {
 const edited = [
  {section:'edited',source:'edited',start:60,end:378.05,excluded:[[378.05,620.9],[1000,1100]],audioRevision:'media'},
  {section:'edited',source:'edited',start:620.9,end:1000,excluded:[[378.05,620.9],[1000,1100]],audioRevision:'media'},
 ];
 assert.deepEqual(resumeClip(edited,null,'v2',{source:'edited',time:412}),{index:1,time:620.9});
 assert.deepEqual(resumeClip(edited,null,'v2',{source:'edited',time:1050}),{index:1,time:1000});
 assert.deepEqual(resumeClip(edited,{version:'v1',section:'edited',source:'edited',time:450,audioRevision:'media'},'v2',{}),{index:1,time:620.9});
 assert.equal(resumeClip(edited,{version:'v1',section:'edited',source:'edited',time:450,audioRevision:'other'},'v2',{}),null);
});
