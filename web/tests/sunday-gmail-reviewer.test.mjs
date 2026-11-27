import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp,writeFile,rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import vm from 'node:vm';
import ts from 'typescript';
import { hasEditorAccess } from '../src/app/utils/roles.ts';
const require=createRequire(import.meta.url);
const code=ts.transpileModule(readFileSync(new URL('../src/app/utils/sunday-gmail-access.ts',import.meta.url),'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,esModuleInterop:true}}).outputText;

test('mailbox reviewer config is fail-closed, live-read and does not grant ordinary accounts editor access', async()=>{
  const root=await mkdtemp(tmpdir()+'/sunday-mail-review-');
  const config=root+'/mail.json';const exports={};
  vm.runInNewContext(code,{exports,process:{env:{SUNDAY_GMAIL_CONFIG:config}},require(name){return name==='./roles'?{hasEditorAccess}:require(name);}});
  const check=exports.canReviewSundayGmail;
  try{
    assert.equal(await check('owner@example.com','editor'),false);
    await writeFile(config,JSON.stringify({reviewers:['owner@example.com']}));
    assert.equal(await check('OWNER@example.com','beta_tester'),true);
    assert.equal(await check('coworker@example.com','editor'),false);
    assert.equal(await check('owner@example.com','reader'),false);
    assert.equal(await check(null,'admin'),false);
    await writeFile(config,JSON.stringify({reviewers:[]}));
    assert.equal(await check('owner@example.com','editor'),false);
    await writeFile(config,'invalid');
    assert.equal(await check('owner@example.com','editor'),false);
  }finally{await rm(root,{recursive:true,force:true});}
});
