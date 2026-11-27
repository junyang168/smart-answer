import {test} from 'node:test';
import assert from 'node:assert/strict';
import {groupUnits,parentPassage} from '../src/app/resources/wang-repository/audio/listeningUnits.ts';
test('overview totals every playback run exactly once and preserves interrupted source order',()=>{
 const sections=[['1','1.1',3],['1','1.2',16],['1','1.3',7],['1','1.2',8],['3','3.1',18]].map(([unit,sub,minutes],i)=>({unit_id:unit,sub_unit_id:sub,unit_label:`Unit ${unit}`,passage:`mat-5-1-12-sub-${i}`,sermons:[{seconds:minutes*60}]}));
 const groups=groupUnits(sections);
 assert.equal(groups[0].seconds,34*60);
 assert.deepEqual(groups.flatMap(g=>g.parts),sections);
 assert.deepEqual(groups[0].parts.map(s=>s.sub_unit_id),['1.1','1.2','1.3','1.2']);
 assert.equal(parentPassage(sections[0]),'mat-5-1-12');
});
