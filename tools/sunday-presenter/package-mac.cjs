const fs = require('node:fs');
const path = require('node:path');
const { execFileSync } = require('node:child_process');
const { packager } = require('@electron/packager');
const { version } = require('./package.json');

function verifyBundleLinks(root, folder = root) {
  for (const entry of fs.readdirSync(folder, { withFileTypes: true })) {
    const file = path.join(folder, entry.name);
    if (entry.isSymbolicLink()) {
      const target = fs.readlinkSync(file);
      if (path.isAbsolute(target) || !fs.realpathSync(file).startsWith(root + path.sep)) {
        throw Error(`App contains a nonportable link: ${file}`);
      }
    } else if (entry.isDirectory()) verifyBundleLinks(root, file);
  }
}

(async () => {
  execFileSync(process.execPath, [path.join(__dirname, 'build-native.cjs')], { stdio: 'inherit' });
  const remote = path.join(__dirname, 'output', 'native', 'Sunday Remote.app');
  const [build] = await packager({ dir: __dirname, name: 'Sunday Presenter', platform: 'darwin', arch: 'arm64',
    out: path.join(__dirname, 'dist'), overwrite: true, asar: true,
    appBundleId: 'org.dallas-hlc.sunday-presenter', appCategoryType: 'public.app-category.productivity',
    icon: path.join(__dirname, 'assets', 'SundayPresenter.icns'),
    extraResource: [remote],
    extendInfo: { NSLocalNetworkUsageDescription: '與已配對的講員電腦連線，接收主日簡報翻頁指令。',
      NSBonjourServices: ['_sunday-presenter._tcp'] },
    ignore: [/^\/test($|\/)/, /^\/output($|\/)/, /^\/native($|\/)/,
      /^\/build-(icons|native)\.cjs$/, /^\/package-mac\.cjs$/] });
  const app = path.join(build, 'Sunday Presenter.app');
  // Bind the renamed Electron executable to this bundle identity. Leaving its
  // upstream linker signature makes macOS attribute child AX requests to an
  // executable path while System Settings grants the bundle ID instead.
  execFileSync('/usr/bin/codesign', ['--force', '--deep', '--sign', '-',
    '--identifier', 'org.dallas-hlc.sunday-presenter', app], { stdio: 'inherit' });
  execFileSync('/usr/bin/codesign', ['--verify', '--deep', '--strict', app], { stdio: 'inherit' });
  const executable = execFileSync('/usr/libexec/PlistBuddy', ['-c', 'Print :CFBundleExecutable',
    path.join(app, 'Contents', 'Info.plist')], { encoding: 'utf8' }).trim();
  fs.accessSync(path.join(app, 'Contents', 'MacOS', executable), fs.constants.X_OK);
  const base = `Sunday-Presenter-${version}-mac-arm64`;
  const stage = path.join(__dirname, 'output', 'installer');
  fs.rmSync(stage, { recursive: true, force: true }); fs.mkdirSync(stage, { recursive: true });
  fs.cpSync(app, path.join(stage, 'Sunday Presenter.app'), { recursive: true, verbatimSymlinks: true });
  fs.cpSync(remote, path.join(stage, 'Sunday Remote.app'), { recursive: true, verbatimSymlinks: true });
  verifyBundleLinks(path.join(stage, 'Sunday Presenter.app'));
  verifyBundleLinks(path.join(stage, 'Sunday Remote.app'));
  fs.symlinkSync('/Applications', path.join(stage, 'Applications'));
  fs.copyFileSync(path.join(__dirname, 'INSTALL-zh.txt'), path.join(stage, '安裝說明.txt'));
  const dmg = path.join(__dirname, 'dist', base + '.dmg');
  fs.rmSync(dmg, { force: true });
  execFileSync('hdiutil', ['create', '-volname', 'Sunday Presenter', '-srcfolder', stage,
    '-format', 'UDZO', '-ov', dmg], { stdio: 'inherit' });
  const zip = path.join(__dirname, 'dist', base + '.zip');
  fs.rmSync(zip, { force: true });
  execFileSync('ditto', ['-c', '-k', '--sequesterRsrc', stage, zip]);
  const remoteZip = path.join(__dirname, 'dist', `Sunday-Remote-${version}-mac-universal.zip`);
  fs.rmSync(remoteZip, { force: true });
  execFileSync('ditto', ['-c', '-k', '--sequesterRsrc', '--keepParent', remote, remoteZip]);
  console.log(dmg); console.log(zip);
  console.log(remoteZip);
})().catch(error => { console.error(error); process.exitCode = 1; });
