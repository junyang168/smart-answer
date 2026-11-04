const fs=require('node:fs'),path=require('node:path'),{execFileSync}=require('node:child_process');
const root=__dirname,out=path.join(root,'../output/ipad-checks');fs.mkdirSync(out,{recursive:true});
const certificate=path.join(out,'test-cert.der');
execFileSync('openssl',['req','-x509','-newkey','rsa:2048','-nodes','-days','1','-subj','/CN=Sunday-Remote-Test',
  '-keyout',path.join(out,'test-only-private.key'),'-outform','DER','-out',certificate],{stdio:['ignore','ignore','pipe']});
const sources=['RemoteScope.swift','PairStore.swift','RemoteClient.swift'].map(f=>path.join(root,'SundayRemote',f));
execFileSync('swiftc',['-swift-version','5','-target',`${process.arch==='arm64'?'arm64':'x86_64'}-apple-macosx13.0`,...sources,path.join(root,'Tests/main.swift'),'-o',path.join(out,'scope-checks')],{stdio:'inherit'});
execFileSync(path.join(out,'scope-checks'),[certificate],{stdio:'inherit'});
execFileSync(process.execPath,[path.join(root,'build.cjs'),'--check-project'],{stdio:'inherit'});
// Parse UIKit sources with the available compiler; parsing is not SDK type checking.
execFileSync('swiftc',['-frontend','-parse',path.join(root,'SundayRemote/AppDelegate.swift'),path.join(root,'SundayRemote/RemoteViewController.swift')],{stdio:'inherit'});
console.log('UIKit source parsed; full iOS SDK compilation/device checks still required.');
