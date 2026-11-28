import test from 'node:test';
import assert from 'node:assert/strict';
import { unified } from 'unified';
import remarkParse from 'remark-parse';
import remarkGfm from 'remark-gfm';
import { remarkEditorialSoftDeletion } from '../src/app/components/admin/surmons/editorial-markdown.ts';
const processor=unified().use(remarkParse).use(remarkGfm).use(remarkEditorialSoftDeletion);
const parse=text=>processor.runSync(processor.parse(text));
const text=node=>node.value ?? (node.children || []).map(text).join('');
const deletions=node=>[...(node.type==='delete'?[text(node)]:[]),...(node.children||[]).flatMap(deletions)];
test('CJK punctuation-final editorial deletion renders as a deletion and preserves all words',()=>{
 const source='好~~設備調整。~~現在讀經。';
 const tree=parse(source);
 assert.deepEqual(deletions(tree),['設備調整。']);
 assert.equal(text(tree),'好設備調整。現在讀經。');
 assert.equal(source,'好~~設備調整。~~現在讀經。');
});
test('existing GFM deletions are not nested and unclosed marks and code remain literal',()=>{
 assert.deepEqual(deletions(parse('甲 ~~刪除~~ 乙')),['刪除']);
 assert.equal(text(parse('甲~~未閉合')), '甲~~未閉合');
 assert.deepEqual(deletions(parse('`~~程式碼。~~字`')),[]);
 assert.equal(text(parse('`~~程式碼。~~字`')), '~~程式碼。~~字');
});
