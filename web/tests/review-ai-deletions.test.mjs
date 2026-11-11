import test from 'node:test';
import assert from 'node:assert/strict';
import { aiDeletions, restoreAiDeletion } from '../src/app/components/admin/surmons/review-ai-deletions.ts';
const e={paragraph_id:'w0-p1',old:'調整設備。',reason:'純操作',kind:'technical_setup'};
const binding={'7':{source_paragraph_id:'w0-p1'}};
test('restore only AI source-bound exact marker; preserve surrounding text and manual deletion',()=>{
 const text='甲~~手工~~乙~~調整設備。~~丙';
 const [item]=aiDeletions([{index:'7',text}],binding,[e]);
 assert.equal(item.id,'7');
 assert.equal(restoreAiDeletion(text,item),'甲~~手工~~乙調整設備。丙');
 assert.throws(()=>restoreAiDeletion(text+'新修改',item),/變更/);
 assert.equal(aiDeletions([{index:'7',text}],binding,[e,e]).length,1);
});
test('do not guess changed, restored, repeated, unbound or speaker-split deletions',()=>{
 for(const text of ['調整設備。','~~調整裝置。~~','~~調整設備。~~~~調整設備。~~','~~調整**教授：**設備。~~']){
  assert.deepEqual(aiDeletions([{index:'7',text}],binding,[e]),[]);
 }
 assert.deepEqual(aiDeletions([{index:'8',text:'~~調整設備。~~'}],binding,[e]),[]);
});
