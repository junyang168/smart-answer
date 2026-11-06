const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { execFileSync } = require('node:child_process');

test('native Remote switches hosts without reusing credentials or accepting background resolution', { skip: process.platform !== 'darwin' }, () => {
  const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'sunday-host-switch-'));
  try {
    const native = path.join(__dirname, '../native');
    const shared = ['RemoteScope.swift', 'PairStore.swift', 'RemoteClient.swift']
      .map(name => fs.readFileSync(path.join(native, '../ipad/SundayRemote', name), 'utf8'));
    const source = shared.concat(['NativeCore.swift', 'RemoteVideoView.swift', 'RemoteApp.swift']
      .map(name => fs.readFileSync(path.join(native, name), 'utf8'))).join('\n');
    const fixture = `
final class FixturePairs: PairStorage {
    var items: [String:String] = [:]
    func load(_ fingerprint: String) -> String? { items[fingerprint] }
    func save(_ token: String, for fingerprint: String) throws { items[fingerprint] = token }
    func remove(_ fingerprint: String) { items.removeValue(forKey:fingerprint) }
}
final class FixtureService: NetService {
    let identity: String
    init(_ name: String, _ identity: String) {
        self.identity = identity
        super.init(domain: "local.", type: "_sunday-ppt._tcp.", name: name, port: 12345)
    }
    override func txtRecordData() -> Data? { NetService.data(fromTXTRecord: ["id": Data(identity.utf8)]) }
    override var addresses: [Data]? {
        var address = sockaddr_in()
        address.sin_len = UInt8(MemoryLayout<sockaddr_in>.size)
        address.sin_family = sa_family_t(AF_INET)
        address.sin_addr.s_addr = inet_addr("127.0.0.1")
        return [withUnsafeBytes(of: &address) { Data($0) }]
    }
}
final class DelayedReply: URLProtocol {
    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
    override func startLoading() {
        DispatchQueue.global().asyncAfter(deadline: .now() + 0.1) {
            self.client?.urlProtocol(self, didReceive: HTTPURLResponse(url: self.request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!, cacheStoragePolicy: .notAllowed)
            self.client?.urlProtocol(self, didLoad: Data("{}".utf8))
            self.client?.urlProtocolDidFinishLoading(self)
        }
    }
    override func stopLoading() {}
}
_ = NSApplication.shared
NSApp.setActivationPolicy(.prohibited)
let remote = RemoteApp()
let a = FixtureService("old Mac", String(repeating: "a", count: 64))
let b = FixtureService("MacBook Pro", String(repeating: "b", count: 64))
remote.services = [a, b]
remote.computers.addItems(withTitles: [a.name, b.name])
let pairs = FixturePairs(); remote.pairStore = pairs
let tokenA = String(repeating:"A",count:43), tokenB = String(repeating:"B",count:43)
try! pairs.save(tokenA,for:a.identity)
// Suppress network polling; these checks exercise real native resolution and selection.
remote.polling = true
remote.computers.selectItem(at: 1)
remote.resolved(b)
precondition(remote.endpoint != nil, "new selected host must resolve despite remembered old host")
precondition(remote.fingerprint == b.identity && remote.token == nil, "old token must never cross hosts")
precondition(!remote.code.isHidden && !remote.connect.isHidden)
precondition(pairs.load(a.identity) == tokenA, "keep saved pairing until replacement succeeds")
remote.resolved(a)
precondition(remote.fingerprint == b.identity, "late background discovery must not change selected host")
remote.computers.selectItem(at: 0)
remote.polling = true
remote.resolved(a)
precondition(remote.token == tokenA && remote.fingerprint == a.identity, "same host reconnects")
remote.busy = true
remote.computers.selectItem(at: 1)
let generation = remote.connectionGeneration
remote.selectComputer()
precondition(remote.connectionGeneration > generation && !remote.busy && !remote.polling)
precondition(remote.token == nil && remote.fingerprint == b.identity && remote.endpoint != nil)
precondition(!remote.previous.isEnabled && !remote.next.isEnabled && remote.binding.isEmpty)
precondition(!remote.computers.isHidden, "host selector stays available")
let configuration = URLSessionConfiguration.ephemeral
configuration.protocolClasses = [DelayedReply.self]
remote.session = URLSession(configuration: configuration)
remote.sessionIdentity = remote.fingerprint + "@" + remote.endpoint!.absoluteString
var staleCompleted = false
remote.request("state") { _ in staleCompleted = true }
remote.computers.selectItem(at: 0)
remote.selectComputer()
RunLoop.main.run(until: Date().addingTimeInterval(0.4))
precondition(!staleCompleted, "late response from previous host must not mutate new connection")
remote.session?.invalidateAndCancel()
try! pairs.save(tokenB,for:b.identity)
remote.computers.selectItem(at:1); remote.resolved(b)
precondition(remote.token == tokenB && pairs.load(a.identity) == tokenA, "each host keeps its own pair")
remote.setViewingActive(true)
remote.forgetPair()
precondition(pairs.load(b.identity) == nil && pairs.load(a.identity) == tokenA, "forget removes only selected host")
precondition(remote.viewingActivity == nil, "forget releases the viewer activity")
remote.setViewingActive(true)
precondition(remote.viewingActivity != nil, "visible live presentation holds an activity")
remote.setViewingActive(false)
precondition(remote.viewingActivity == nil, "stopped presentation releases its activity")
print("native host-switch regression passed")
`;
    const file = path.join(temp, 'main.swift');
    fs.writeFileSync(file, source + '\n' + fixture);
    const binary = path.join(temp, 'host-switch');
    execFileSync('/usr/bin/swiftc', ['-swift-version', '5', file, '-o', binary], { stdio: 'pipe' });
    assert.match(execFileSync(binary, { encoding: 'utf8', timeout: 15000 }), /native host-switch regression passed/);
  } finally { fs.rmSync(temp, { recursive: true, force: true }); }
});
