const test=require('node:test');
const path=require('node:path');
const {execFileSync}=require('node:child_process');
test('iPad production protocol scope, real certificate delegate and project checks (Mac SDK only)',{skip:process.platform!=='darwin'},()=>{
  execFileSync(process.execPath,[path.join(__dirname,'../ipad/test.cjs')],{stdio:'pipe'});
});
