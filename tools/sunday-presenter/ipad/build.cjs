const path = require('node:path');
const {execFileSync} = require('node:child_process');
const fs = require('node:fs');
const root=__dirname;
function run(command,args){return execFileSync(command,args,{encoding:'utf8',stdio:['ignore','pipe','pipe']});}
try {
  require('./generate-project.cjs');
  process.stdout.write(run('plutil',['-lint',path.join(root,'SundayRemote/Info.plist'),path.join(root,'SundayRemote.xcodeproj/project.pbxproj')]));
  const plist=JSON.parse(run('plutil',['-convert','json','-o','-',path.join(root,'SundayRemote/Info.plist')]));
  const protocol=require('../protocol.json');
  if(plist.NSBonjourServices[0]!==protocol.bonjourType)throw Error('Bonjour protocol drift');
  for(const file of ['remote.html','remote.js','remote.css'])if(!fs.existsSync(path.join(root,'../media',file)))throw Error('Missing bundled receiver');
  if(process.argv.includes('--check-project')){
    console.log('Project/plist/resources validated. This is NOT an iOS build or simulator/device test.');
  }else{
    const simulator=process.argv.includes('--simulator');
    if(!simulator&&!process.argv.includes('--device'))throw Error('Use --check-project, --simulator or --device (unsigned build).');
    run('xcrun',['--sdk',simulator?'iphonesimulator':'iphoneos','--show-sdk-path']);
    execFileSync('xcodebuild',['-project',path.join(root,'SundayRemote.xcodeproj'),'-scheme','SundayRemote',
      '-configuration','Debug','-destination',simulator?'generic/platform=iOS Simulator':'generic/platform=iOS',
      '-derivedDataPath',path.join(root,'../output/ipad'), 'CODE_SIGNING_ALLOWED=NO','build'],{stdio:'inherit'});
    console.log(simulator?'Simulator build complete; not yet launched/tested.':'Unsigned iPad build complete; use Xcode and your team/device for installation.');
  }
}catch(error){
  console.error(error.stderr?.toString()||error.message);
  console.error('Full Xcode with the iOS SDK is required for build/run. See ipad/README.md. No installable iPad build has been produced by this failed command.');
  process.exitCode=1;
}
