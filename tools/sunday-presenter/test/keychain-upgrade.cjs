// Explicit macOS integration check: two differently built, Apple-signed clients
// must read the same test-only Keychain item. Never touches real pairing items.
const fs=require('node:fs'),path=require('node:path'),os=require('node:os'),crypto=require('node:crypto');
const {execFileSync:run}=require('node:child_process');
const config=process.env.SUNDAY_PRESENTER_SIGNING_CONFIG||path.join(os.homedir(),'.config/sunday-presenter/signing.json');
const signer=process.env.SUNDAY_MAC_SIGN_IDENTITY||(fs.existsSync(config)?JSON.parse(fs.readFileSync(config)).macSigningIdentity:null);
if(!signer||signer==='-')throw Error('Configure an Apple signing identity; an ad-hoc check cannot prove upgrade identity.');
const root=fs.mkdtempSync(path.join(os.tmpdir(),'sunday-keychain-upgrade-'));
const service='org.dallas-hlc.remote-upgrade-check.'+crypto.randomUUID();
const source=path.resolve(__dirname,'../ipad/SundayRemote');
const swift=`import Foundation
let store=PairStore(service:CommandLine.arguments[1])
let pin=String(repeating:"a",count:64), token=String(repeating:"A",count:43)
let build="BUILD_MARKER"
switch CommandLine.arguments[2] {
case "save":try store.save(token,for:pin)
case "load":guard try store.load(pin)==token else {fatalError("pair lost after upgrade")}
case "migrate":
    let migrated=PairStore(service:CommandLine.arguments[1]+".v2",legacyService:CommandLine.arguments[1])
    guard try migrated.load(pin)==token else {fatalError("legacy pair migration failed")}
    store.remove(pin)
    guard try migrated.load(pin)==token else {fatalError("migration still depends on old item")}
    migrated.remove(pin)
case "remove":store.remove(pin)
default:fatalError("unknown operation")
}
print("PASS "+build+" "+CommandLine.arguments[2])
`;
const requirements=[];let saved=false;
try{
  for(const version of ['before','after']){
    fs.writeFileSync(path.join(root,'main.swift'),swift.replace('BUILD_MARKER',version));
    run('swiftc',['-swift-version','5',path.join(source,'RemoteScope.swift'),path.join(source,'PairStore.swift'),path.join(root,'main.swift'),'-o',path.join(root,version)],{stdio:'pipe'});
    run('codesign',['--force','--identifier','org.dallas-hlc.sunday-remote.upgrade-check','--sign',signer,path.join(root,version)],{stdio:'pipe'});
    const value=run('codesign',['-d','-r-',path.join(root,version)],{encoding:'utf8',stdio:['ignore','pipe','ignore']});
    const requirement=value.split('\n').find(x=>x.startsWith('designated =>'));
    if(!requirement||!requirement.includes('anchor apple'))throw Error('Expected stable Apple-backed signing requirement');
    requirements.push(requirement);
  }
  if(requirements[0]!==requirements[1])throw Error('Different builds have different signing requirements');
  run(path.join(root,'before'),[service,'save'],{stdio:'inherit'});saved=true;
  run(path.join(root,'after'),[service,'load'],{stdio:'inherit'});
  run(path.join(root,'after'),[service,'migrate'],{stdio:'inherit'});
  console.log('PASS: updated signed client restores pairing without a new pairing code');
}finally{
  if(saved)run(path.join(root,'before'),[service,'remove'],{stdio:'inherit'});
  fs.rmSync(root,{recursive:true,force:true});
}
