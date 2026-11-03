const fs = require('node:fs');
const path = require('node:path');
const { execFileSync } = require('node:child_process');
const { version } = require('./package.json');
const { bonjourType } = require('./protocol.json');
if (!/^_[a-z][a-z0-9-]{0,14}\._tcp$/.test(bonjourType)) throw Error('Bonjour protocol name must be at most 15 ASCII characters');

const output = path.join(__dirname, 'output', 'native');
const bundle = path.join(output, 'Sunday Remote.app');
const contents = path.join(bundle, 'Contents');
fs.mkdirSync(path.join(contents, 'MacOS'), { recursive: true });
fs.mkdirSync(path.join(contents, 'Resources'), { recursive: true });
const source = ['MeetWindowLocator.swift', 'NativeCore.swift', 'RemoteApp.swift', 'main.swift'].map(name =>
  fs.readFileSync(path.join(__dirname, 'native', name), 'utf8')).join('\n').replaceAll('__SUNDAY_SERVICE_TYPE__', bonjourType);
fs.writeFileSync(path.join(output, 'main.swift'), source);
for (const arch of ['arm64', 'x86_64']) {
  execFileSync('/usr/bin/swiftc', ['-swift-version', '5', '-O', '-target', `${arch}-apple-macosx11.0`,
    path.join(output, 'main.swift'), '-o', path.join(output, arch)], { stdio: 'inherit' });
}
execFileSync('/usr/bin/lipo', ['-create', path.join(output, 'arm64'), path.join(output, 'x86_64'),
  '-output', path.join(contents, 'MacOS', 'Sunday Remote')]);
fs.copyFileSync(path.join(__dirname, 'assets', 'SundayPresenter.icns'), path.join(contents, 'Resources', 'AppIcon.icns'));
fs.writeFileSync(path.join(contents, 'Info.plist'), `<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>CFBundleExecutable</key><string>Sunday Remote</string>
<key>CFBundleIdentifier</key><string>org.dallas-hlc.sunday-remote</string>
<key>CFBundleName</key><string>Sunday Remote</string>
<key>CFBundleDisplayName</key><string>Sunday Remote</string>
<key>CFBundlePackageType</key><string>APPL</string>
<key>CFBundleShortVersionString</key><string>${version}</string>
<key>CFBundleVersion</key><string>${version}</string>
<key>LSMinimumSystemVersion</key><string>11.0</string>
<key>CFBundleIconFile</key><string>AppIcon</string>
<key>NSHighResolutionCapable</key><true/>
<key>NSLocalNetworkUsageDescription</key><string>尋找同一個 Wi-Fi 的主電腦，控制主日簡報翻頁。</string>
<key>NSBonjourServices</key><array><string>${bonjourType}</string></array>
<key>NSAppTransportSecurity</key><dict><key>NSAllowsLocalNetworking</key><true/></dict>
</dict></plist>`);
execFileSync('/usr/bin/codesign', ['--force', '--sign', '-', bundle], { stdio: 'inherit' });
// Refresh Launch Services' Info.plist cache after an in-place development build.
// Otherwise Bonjour can reject a newly declared service as missing config.
fs.utimesSync(bundle, new Date(), new Date());
console.log(bundle);
