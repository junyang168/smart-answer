// Dependency-free, deterministic Xcode project. Run after shared sources change.
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const root = __dirname;
const {version} = require('../package.json');
if (!/^\d+\.\d+\.\d+$/.test(version)) throw Error('App version must contain three numeric components');
const id = name => crypto.createHash('sha256').update(name).digest('hex').slice(0,24).toUpperCase();
const q = value => JSON.stringify(value);
const files = ['AppDelegate.swift','RemoteViewController.swift','RemoteClient.swift','RemoteScope.swift','PairStore.swift']
  .map(name => `SundayRemote/${name}`).concat('../native/RemoteVideoView.swift');
const media = ['remote.html','remote.js','remote.css'].map(name => `../media/${name}`);
const objects = [];
const add = (name, value) => objects.push(`${id(name)} = { ${value} };`);
for (const file of [...files,...media]) {
  if (!fs.existsSync(path.resolve(root,file))) throw Error('Missing source '+file);
  add(file,`isa = PBXFileReference; path = ${q(file)}; sourceTree = SOURCE_ROOT; lastKnownFileType = ${q(file.endsWith('.swift')?'sourcecode.swift':file.endsWith('.html')?'text.html':file.endsWith('.js')?'sourcecode.javascript':'text.css')};`);
  add('build:'+file,`isa = PBXBuildFile; fileRef = ${id(file)};`);
}
add('product','isa = PBXFileReference; explicitFileType = wrapper.application; path = SundayRemote.app; sourceTree = BUILT_PRODUCTS_DIR;');
add('products',`isa = PBXGroup; name = Products; sourceTree = "<group>"; children = (${id('product')});`);
add('group',`isa = PBXGroup; sourceTree = "<group>"; children = (${[...files,...media].map(id).concat(id('products')).join(',')});`);
add('sources',`isa = PBXSourcesBuildPhase; buildActionMask = 2147483647; files = (${files.map(f=>id('build:'+f)).join(',')}); runOnlyForDeploymentPostprocessing = 0;`);
add('frameworks','isa = PBXFrameworksBuildPhase; buildActionMask = 2147483647; files = (); runOnlyForDeploymentPostprocessing = 0;');
add('resources',`isa = PBXCopyFilesBuildPhase; buildActionMask = 2147483647; dstPath = media; dstSubfolderSpec = 7; files = (${media.map(f=>id('build:'+f)).join(',')}); name = "Bundled receiver only"; runOnlyForDeploymentPostprocessing = 0;`);
for (const mode of ['Debug','Release']) {
  add('project:'+mode,`isa = XCBuildConfiguration; name = ${mode}; buildSettings = { CLANG_ENABLE_MODULES = YES; SDKROOT = iphoneos; IPHONEOS_DEPLOYMENT_TARGET = 16.0; SWIFT_VERSION = 5.0; SWIFT_OPTIMIZATION_LEVEL = ${q(mode==='Debug'?'-Onone':'-O')}; };`);
  add('target:'+mode,`isa = XCBuildConfiguration; name = ${mode}; buildSettings = { PRODUCT_BUNDLE_IDENTIFIER = "org.dallas-hlc.sunday-remote.ipad"; PRODUCT_NAME = "$(TARGET_NAME)"; INFOPLIST_FILE = SundayRemote/Info.plist; GENERATE_INFOPLIST_FILE = NO; TARGETED_DEVICE_FAMILY = 2; SUPPORTED_PLATFORMS = "iphoneos iphonesimulator"; SUPPORTS_MACCATALYST = NO; CODE_SIGN_STYLE = Automatic; CURRENT_PROJECT_VERSION = ${version}; MARKETING_VERSION = ${version}; SWIFT_EMIT_LOC_STRINGS = YES; };`);
}
for (const kind of ['project','target']) add(kind+':configs',`isa = XCConfigurationList; buildConfigurations = (${id(kind+':Debug')},${id(kind+':Release')}); defaultConfigurationIsVisible = 0; defaultConfigurationName = Release;`);
add('target',`isa = PBXNativeTarget; buildConfigurationList = ${id('target:configs')}; buildPhases = (${id('sources')},${id('frameworks')},${id('resources')}); buildRules = (); dependencies = (); name = SundayRemote; productName = SundayRemote; productReference = ${id('product')}; productType = "com.apple.product-type.application";`);
add('project',`isa = PBXProject; attributes = { LastUpgradeCheck = 1600; }; buildConfigurationList = ${id('project:configs')}; compatibilityVersion = "Xcode 14.0"; developmentRegion = en; hasScannedForEncodings = 0; knownRegions = (en,Base); mainGroup = ${id('group')}; productRefGroup = ${id('products')}; projectDirPath = ""; projectRoot = ""; targets = (${id('target')});`);
const project = path.join(root,'SundayRemote.xcodeproj');
fs.mkdirSync(path.join(project,'xcshareddata/xcschemes'),{recursive:true});
fs.writeFileSync(path.join(project,'project.pbxproj'),`// !$*UTF8*$!\n{ archiveVersion = 1; classes = {}; objectVersion = 56; objects = {\n${objects.join('\n')}\n}; rootObject = ${id('project')}; }\n`);
fs.writeFileSync(path.join(project,'xcshareddata/xcschemes/SundayRemote.xcscheme'),`<?xml version="1.0" encoding="UTF-8"?>
<Scheme LastUpgradeVersion="1600" version="1.3"><BuildAction parallelizeBuildables="YES" buildImplicitDependencies="YES"><BuildActionEntries><BuildActionEntry buildForTesting="YES" buildForRunning="YES" buildForProfiling="YES" buildForArchiving="YES" buildForAnalyzing="YES"><BuildableReference BuildableIdentifier="primary" BlueprintIdentifier="${id('target')}" BuildableName="SundayRemote.app" BlueprintName="SundayRemote" ReferencedContainer="container:SundayRemote.xcodeproj"/></BuildActionEntry></BuildActionEntries></BuildAction><LaunchAction buildConfiguration="Debug" selectedDebuggerIdentifier="Xcode.DebuggerFoundation.Debugger.LLDB" selectedLauncherIdentifier="Xcode.IDEFoundation.Launcher.LLDB" launchStyle="0" useCustomWorkingDirectory="NO" ignoresPersistentStateOnLaunch="NO" debugDocumentVersioning="YES" debugServiceExtension="internal" allowLocationSimulation="YES"><BuildableProductRunnable runnableDebuggingMode="0"><BuildableReference BuildableIdentifier="primary" BlueprintIdentifier="${id('target')}" BuildableName="SundayRemote.app" BlueprintName="SundayRemote" ReferencedContainer="container:SundayRemote.xcodeproj"/></BuildableProductRunnable></LaunchAction><ArchiveAction buildConfiguration="Release" revealArchiveInOrganizer="YES"/></Scheme>\n`);
console.log(project);
