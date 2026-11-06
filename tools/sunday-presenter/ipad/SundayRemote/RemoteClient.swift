import Foundation
import Security
import CryptoKit
import Darwin

// Each URLSession pins an immutable host identity. No arbitrary trust or redirects.
final class PinnedSessionDelegate: NSObject, URLSessionDelegate, URLSessionTaskDelegate {
    let scope: RemoteScope
    init(scope: RemoteScope) { self.scope = scope }
    func urlSession(_ session: URLSession, didReceive challenge: URLAuthenticationChallenge,
                    completionHandler: @escaping (URLSession.AuthChallengeDisposition, URLCredential?) -> Void) {
        guard challenge.protectionSpace.authenticationMethod == NSURLAuthenticationMethodServerTrust,
              let trust = challenge.protectionSpace.serverTrust else {
            completionHandler(.cancelAuthenticationChallenge, nil); return
        }
        let certificate: SecCertificate?
        if #available(macOS 12.0, iOS 15.0, *) {
            certificate = (SecTrustCopyCertificateChain(trust) as? [SecCertificate])?.first
        } else { certificate = SecTrustGetCertificateAtIndex(trust, 0) }
        guard let certificate = certificate else { completionHandler(.cancelAuthenticationChallenge, nil); return }
        let digest = SHA256.hash(data: SecCertificateCopyData(certificate) as Data).map { String(format: "%02x", $0) }.joined()
        guard scope.acceptsPin(digest, host: challenge.protectionSpace.host, port: challenge.protectionSpace.port) else {
            completionHandler(.cancelAuthenticationChallenge, nil); return
        }
        completionHandler(.useCredential, URLCredential(trust: trust))
    }
    func urlSession(_ session: URLSession, task: URLSessionTask, willPerformHTTPRedirection response: HTTPURLResponse,
                    newRequest request: URLRequest, completionHandler: @escaping (URLRequest?) -> Void) { completionHandler(nil) }
}

// Main-thread client; UIKit is deliberately outside this file so the actual
// protocol/lifecycle implementation can also be type-checked with the Mac SDK.
final class RemoteClient: NSObject, NetServiceBrowserDelegate, NetServiceDelegate {
    private(set) var scope = RemoteScope()
    private(set) var status = "正在找主電腦…"
    private(set) var selectedID: String?
    private(set) var commandBusy = false
    private(set) var pairingBusy = false
    private var services: [NetService] = []
    private var browser: NetServiceBrowser?
    private var timer: Timer?
    private var selected: NetService?
    private var session: URLSession?
    private var polling = false
    private var active = false
    private var lastResolve = Date.distantPast
    private let store = PairStore()
    private var videoSession = "", videoClient = UUID().uuidString
    private var videoBusy = false, videoGeneration = 0
    private var videoRetry = Date.distantPast
    private var commandErrorUntil = Date.distantPast
    var onChange: (() -> Void)?
    var applyVideo: (([String: Any]) -> Void)?
    var stopVideo: ((String) -> Void)?
    var videoReceiving = false
    var computers: [(id: String, name: String)] { services.map { (identity($0), $0.name) } }
    var canFlip: Bool { active && scope.canFlip && !commandBusy && !pairingBusy }
    var paired: Bool { scope.token != nil }
    var canPair: Bool { active && scope.endpoint != nil && !pairingBusy }
    private func identity(_ service: NetService) -> String { service.name + "." + service.type + service.domain }
    private func changed() { onChange?() }

    func start() {
        guard !active else { return }; active = true
        let browser = NetServiceBrowser(); self.browser = browser; browser.delegate = self
        browser.searchForServices(ofType: "_sunday-ppt._tcp.", inDomain: "local.")
        let timer = Timer(timeInterval: 0.5, repeats: true) { [weak self] _ in self?.poll() }
        RunLoop.main.add(timer, forMode: .common); self.timer = timer
        status = "正在找主電腦；請允許本機網路。"; changed()
    }
    func suspend() {
        active = false; timer?.invalidate(); timer = nil; browser?.stop(); browser = nil
        services.forEach { $0.stop() }; services.removeAll(); selected = nil; selectedID = nil
        disconnect("已暫停；回到 App 後重新連線。")
    }
    private func disconnect(_ text: String) {
        resetVideo(text); session?.invalidateAndCancel(); session = nil
        scope.clear(); polling = false; commandBusy = false; pairingBusy = false
        status = text; commandErrorUntil = .distantPast; changed()
    }
    func observe(_ enabled: Bool) { scope.observing = enabled; changed() }
    func select(_ id: String) {
        guard active, let service = services.first(where: { identity($0) == id }) else { return }
        disconnect("正在連接所選主電腦…"); selected = service; selectedID = id
        service.delegate = self; service.resolve(withTimeout: 8); changed()
    }
    func forget() {
        if !scope.fingerprint.isEmpty { store.remove(scope.fingerprint) }
        let id = selectedID; disconnect("請重新輸入主電腦的配對碼。")
        if let id = id { select(id) }
    }
    func netServiceBrowser(_ browser: NetServiceBrowser, didFind service: NetService, moreComing: Bool) {
        guard active, browser === self.browser else { return }
        if !services.contains(where: { identity($0) == identity(service) }) { services.append(service) }
        if selectedID == nil { select(identity(service)) }; changed()
    }
    func netServiceBrowser(_ browser: NetServiceBrowser, didRemove service: NetService, moreComing: Bool) {
        guard browser === self.browser else { return }
        let id = identity(service); services.removeAll { identity($0) == id }
        if id == selectedID { selected = nil; selectedID = nil; disconnect("主電腦已離線，正在尋找…") }
        changed()
    }
    func netServiceBrowser(_ browser: NetServiceBrowser, didNotSearch errorDict: [String: NSNumber]) {
        guard browser === self.browser else { return }
        status = "找不到主電腦。請確認同一個 Wi-Fi／熱點，並在設定允許本機網路。"; changed()
    }
    func netService(_ sender: NetService, didNotResolve errorDict: [String: NSNumber]) {
        guard sender === selected else { return }
        status = "主電腦位址未就緒，正在重試…"; changed()
    }
    func netServiceDidResolveAddress(_ sender: NetService) {
        guard active, sender === selected,
              let raw = sender.txtRecordData().map(NetService.dictionary(fromTXTRecord:))?["id"],
              let fingerprint = String(data: raw, encoding: .utf8), RemoteScope.validFingerprint(fingerprint),
              (1...65535).contains(sender.port) else { return }
        // Same IPv4 Bonjour address path as the existing Mac Remote.
        for data in sender.addresses ?? [] {
            var host = [CChar](repeating: 0, count: Int(NI_MAXHOST))
            let found = data.withUnsafeBytes { bytes -> Bool in
                guard let address = bytes.bindMemory(to: sockaddr.self).baseAddress,
                      address.pointee.sa_family == UInt8(AF_INET) else { return false }
                return getnameinfo(address, socklen_t(data.count), &host, socklen_t(host.count), nil, 0, NI_NUMERICHOST) == 0
            }
            guard found, let endpoint = URL(string: "https://\(String(cString: host)):\(sender.port)") else { continue }
            if scope.endpoint == endpoint && scope.fingerprint == fingerprint { return }
            disconnect("正在驗證主電腦…")
            let savedToken: String?
            do { savedToken = try store.load(fingerprint) }
            catch { status = error.localizedDescription; resetVideo(status); changed(); return }
            guard scope.select(endpoint: endpoint, fingerprint: fingerprint, savedToken: savedToken) else { return }
            let config = URLSessionConfiguration.ephemeral
            config.timeoutIntervalForRequest = 5; config.timeoutIntervalForResource = 10
            config.httpShouldSetCookies = false; config.urlCache = nil
            session = URLSession(configuration: config, delegate: PinnedSessionDelegate(scope: scope), delegateQueue: .main)
            status = paired ? "正在重連已配對主電腦…" : "請輸入主電腦的六位數配對碼。"
            // Discovery/pin selection is complete. Do not leave the central
            // viewer saying it is verifying while the footer requests pairing.
            resetVideo(status)
            changed(); poll(); return
        }
    }
    private func refreshEndpoint() {
        guard active, let selected = selected, Date().timeIntervalSince(lastResolve) >= 5 else { return }
        lastResolve = Date(); selected.stop(); selected.resolve(withTimeout: 4)
    }
    private func request(_ path: String, body: [String: Any]? = nil,
                         completion: @escaping (Result<[String: Any], Error>, Int) -> Void) {
        guard active, let endpoint = scope.endpoint, let session = session else { return }
        let generation = scope.generation
        var request = URLRequest(url: endpoint.appendingPathComponent(path)); request.cachePolicy = .reloadIgnoringLocalCacheData
        if let token = scope.token { request.setValue("Bearer " + token, forHTTPHeaderField: "Authorization") }
        if let body = body {
            request.httpMethod = "POST"; request.setValue("application/json", forHTTPHeaderField: "Content-Type")
            request.httpBody = try? JSONSerialization.data(withJSONObject: body)
        }
        session.dataTask(with: request) { [weak self] data, response, error in
            DispatchQueue.main.async {
                guard let self = self, self.active, self.scope.generation == generation else { return }
                let code = (response as? HTTPURLResponse)?.statusCode
                if RemoteScope.pairingRevoked(path: path, status: code) {
                    self.forget(); self.status = "配對已被主電腦撤銷，請重新輸入六位數字。"; self.changed(); return
                }
                if let error = error { completion(.failure(error), generation); return }
                guard let data = data, data.count <= 1048576,
                      let value = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] else {
                    completion(.failure(RemoteError("主電腦回應不完整。")), generation); return
                }
                guard code == 200 else {
                    completion(.failure(RemoteError(value["error"] as? String ?? "主電腦暫時無法連線。")), generation); return
                }
                completion(.success(value), generation)
            }
        }.resume()
    }
    func pair(_ code: String) {
        guard canPair, RemoteScope.validCode(code) else { status = "請輸入六位數字。"; changed(); return }
        pairingBusy = true; changed()
        request("pair", body: ["code": code]) { result, generation in
            self.pairingBusy = false
            do {
                let value = try result.get()
                guard let token = value["token"] as? String, self.scope.pair(token: token, generation: generation) else { throw RemoteError("配對回應不完整。") }
                do { try self.store.save(token, for: self.scope.fingerprint) }
                catch { self.forget(); throw error }
                self.apply(value, generation: generation)
            } catch { self.status = error.localizedDescription; self.refreshEndpoint() }
            self.changed()
        }
    }
    private func poll() {
        guard active else { return }
        guard scope.endpoint != nil else { refreshEndpoint(); return }
        guard paired, !polling, !pairingBusy else { return }; polling = true
        request("state") { result, generation in
            self.polling = false
            switch result {
            case .success(let value): self.apply(value, generation: generation)
            case .failure:
                self.scope.unavailable(); self.resetVideo("等待主電腦；畫面已停止。")
                self.status = "連線中斷；回到同一個 Wi-Fi 後會重試。"; self.refreshEndpoint(); self.changed()
            }
        }
    }
    private func apply(_ value: [String: Any], generation: Int) {
        guard scope.apply(value, generation: generation) else { return }
        if Date() >= commandErrorUntil { status = "已連線 · \(value["deck"] as? String ?? "尚未播放")" }
        if scope.live, (value["capabilities"] as? [String])?.contains("ppt-video-v1") == true { pollVideo() }
        else { resetVideo(scope.live ? "主電腦版本不支援畫面，請更新 Presenter。" : "等待主電腦播放 PPT") }
        changed()
    }
    private func resetVideo(_ text: String) {
        videoGeneration += 1; videoSession = ""; videoBusy = false; videoClient = UUID().uuidString
        videoReceiving = false; stopVideo?(text)
    }
    private func pollVideo() {
        guard !videoBusy, Date() >= videoRetry else { return }; videoBusy = true
        let generation = videoGeneration
        let body: [String: Any] = videoSession.isEmpty ? ["op": "start", "clientId": videoClient] : ["op": "poll", "sessionId": videoSession]
        request("video", body: body) { result, _ in
            guard self.videoGeneration == generation else { return }; self.videoBusy = false
            switch result {
            case .success(let value): self.videoSession = value["sessionId"] as? String ?? ""; self.applyVideo?(value)
            case .failure(let error): self.resetVideo(error.localizedDescription); self.videoRetry = Date().addingTimeInterval(3)
            }
        }
    }
    func videoMessage(_ value: [String: Any]) {
        guard active else { return }
        if let id = value["sessionId"] as? String, id != videoSession { return }
        switch value["op"] as? String {
        case "failed":
            resetVideo(value["error"] as? String ?? "畫面已停止，正在重連…")
            videoRetry = Date().addingTimeInterval(5); changed()
        case "stats":
            videoReceiving = (value["frames"] as? Int ?? 0) > 0; changed()
        case "answer":
            guard let id = value["sessionId"] as? String, id == videoSession,
                  let sdp = value["sdp"] as? String, sdp.utf8.count <= 262144 else { return }
            let generation = videoGeneration
            request("video", body: ["op": "answer", "sessionId": id, "sdp": sdp]) { result, _ in
                guard generation == self.videoGeneration else { return }
                if case .failure = result { self.resetVideo("畫面連線失敗，正在重試…"); self.videoRetry = Date().addingTimeInterval(3) }
            }
        default: break
        }
    }
    func flip(_ key: String) {
        guard canFlip, ["Left", "Right"].contains(key) else { return }
        commandBusy = true; changed()
        request("key", body: ["key": key, "binding": scope.binding, "id": UUID().uuidString]) { result, generation in
            self.commandBusy = false
            switch result {
            case .success(let value): self.commandErrorUntil = .distantPast; self.apply(value, generation: generation)
            case .failure(let error):
                self.status = error.localizedDescription; self.commandErrorUntil = Date().addingTimeInterval(8)
                // Never replay a failed/uncertain slide command.
                self.scope.unavailable(); self.poll(); self.changed()
            }
        }
    }
}
