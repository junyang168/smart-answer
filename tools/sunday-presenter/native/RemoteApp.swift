final class RemoteApp: NSObject, NSApplicationDelegate, NetServiceBrowserDelegate, NetServiceDelegate, URLSessionDelegate, NSWindowDelegate {
    var window: NSWindow!
    let browser = NetServiceBrowser()
    var services: [NetService] = []
    var connectedService: NetService?
    let computers = NSPopUpButton()
    let code = NSTextField()
    let connect = NSButton(title: "第一次連線", target: nil, action: nil)
    let status = NSTextField(labelWithString: "正在找主電腦…")
    let fullScreen = NSButton(title: "全螢幕", target: nil, action: nil)
    let previous = NSButton(title: "← 上一頁", target: nil, action: nil)
    let next = NSButton(title: "下一頁 →", target: nil, action: nil)
    var endpoint: URL?, fingerprint = "", token: String?, binding = ""
    var saved: [String: String] = [:]
    var connectionGeneration = 0
    var busy = false, polling = false
    var commandError = "", commandErrorUntil = Date.distantPast
    var timer: Timer?
    var viewingActivity: NSObjectProtocol?
    var lastResolve = Date.distantPast
    var videoView: RemoteVideoView?
    var videoSession = "", videoClient = UUID().uuidString, videoBusy = false
    var videoGeneration = 0, videoRetry = Date.distantPast
    var videoStats: [String: Any] = [:]
    var videoProbe = false

    lazy var session: URLSession = {
        let config = URLSessionConfiguration.ephemeral
        config.timeoutIntervalForRequest = 5
        config.httpShouldSetCookies = false
        return URLSession(configuration: config, delegate: self, delegateQueue: nil)
    }()
    func keychainQuery() -> [String: Any] {
        [kSecClass as String: kSecClassGenericPassword,
         kSecAttrService as String: "org.dallas-hlc.sunday-remote",
         kSecAttrAccount as String: "paired-presenter"]
    }
    func loadPair() {
        var query = keychainQuery(); query[kSecReturnData as String] = true
        var data: CFTypeRef?
        if SecItemCopyMatching(query as CFDictionary, &data) == errSecSuccess,
           let raw = data as? Data, let item = try? JSONSerialization.jsonObject(with: raw) as? [String: String] { saved = item }
    }
    func savePair() throws {
        let value = ["fingerprint": fingerprint, "token": token ?? ""]
        let data = try JSONSerialization.data(withJSONObject: value)
        let result = SecItemUpdate(keychainQuery() as CFDictionary, [kSecValueData as String: data] as CFDictionary)
        if result == errSecItemNotFound {
            var query = keychainQuery(); query[kSecValueData as String] = data
            guard SecItemAdd(query as CFDictionary, nil) == errSecSuccess else { throw ControlError("無法記住配對，請檢查鑰匙圈權限。") }
        } else if result != errSecSuccess { throw ControlError("無法記住配對，請檢查鑰匙圈權限。") }
        saved = value
    }
    func applicationDidFinishLaunching(_ notification: Notification) {
        if !videoProbe { loadPair() }
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 920, height: 720),
            styleMask: [.titled, .closable, .miniaturizable, .resizable], backing: .buffered, defer: false)
        window.title = "Sunday Remote · PPT 畫面與翻頁"; window.center()
        window.level = .normal; window.collectionBehavior = [.fullScreenPrimary]
        window.delegate = self
        window.isReleasedWhenClosed = false
        window.minSize = NSSize(width: 520, height: 440)
        computers.target = self; computers.action = #selector(selectComputer)
        code.placeholderString = "主電腦的六位數字"; code.alignment = .center
        connect.target = self; connect.action = #selector(pair)
        previous.target = self; previous.action = #selector(back)
        next.target = self; next.action = #selector(forward)
        fullScreen.target = self; fullScreen.action = #selector(toggleFullScreen)
        for button in [previous, next, fullScreen] {
            button.font = .systemFont(ofSize: 16, weight: .medium); button.bezelStyle = .rounded
            button.heightAnchor.constraint(equalToConstant: 40).isActive = true
        }
        previous.isEnabled = false; next.isEnabled = false
        status.font = .systemFont(ofSize: 12); status.textColor = .secondaryLabelColor
        status.lineBreakMode = .byTruncatingTail
        status.setContentCompressionResistancePriority(.defaultLow, for: .horizontal)
        computers.setContentCompressionResistancePriority(.defaultLow, for: .horizontal)
        let connection = NSStackView(views: [computers, status]); connection.spacing = 8
        let pairing = NSStackView(views: [code, connect]); pairing.spacing = 8
        let keys = NSStackView(views: [previous, next, fullScreen]); keys.spacing = 8
        keys.distribution = .fillEqually
        let footer = NSStackView(views: [connection, pairing, keys])
        footer.orientation = .vertical; footer.alignment = .leading; footer.spacing = 4
        footer.edgeInsets = NSEdgeInsets(top: 4, left: 8, bottom: 6, right: 8)
        footer.translatesAutoresizingMaskIntoConstraints = false
        let viewer = RemoteVideoView(resources: Bundle.main.resourceURL!)
        videoView = viewer
        viewer.onMessage = { [weak self] value in self?.videoMessage(value) }
        viewer.webView.translatesAutoresizingMaskIntoConstraints = false
        window.contentView!.addSubview(viewer.webView); window.contentView!.addSubview(footer)
        let content = window.contentView!
        NSLayoutConstraint.activate([
            viewer.webView.topAnchor.constraint(equalTo: content.topAnchor),
            viewer.webView.leadingAnchor.constraint(equalTo: content.leadingAnchor),
            viewer.webView.trailingAnchor.constraint(equalTo: content.trailingAnchor),
            viewer.webView.bottomAnchor.constraint(equalTo: footer.topAnchor),
            footer.leadingAnchor.constraint(equalTo: content.leadingAnchor),
            footer.trailingAnchor.constraint(equalTo: content.trailingAnchor),
            footer.bottomAnchor.constraint(equalTo: content.bottomAnchor),
            connection.widthAnchor.constraint(equalTo: footer.widthAnchor, constant: -16),
            keys.widthAnchor.constraint(equalTo: footer.widthAnchor, constant: -16),
            code.widthAnchor.constraint(equalToConstant: 180),
            computers.widthAnchor.constraint(lessThanOrEqualToConstant: 330)
        ])
        let menu = NSMenu(); let appMenu = NSMenu(); let item = NSMenuItem(); item.submenu = appMenu; menu.addItem(item)
        appMenu.addItem(withTitle: "顯示遙控器", action: #selector(show), keyEquivalent: "p").target = self
        appMenu.addItem(withTitle: "離開 Sunday Remote", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        let viewItem = NSMenuItem(); let viewMenu = NSMenu(title: "顯示"); viewItem.submenu = viewMenu; menu.addItem(viewItem)
        let fullscreenItem = viewMenu.addItem(withTitle: "切換全螢幕", action: #selector(toggleFullScreen), keyEquivalent: "f")
        fullscreenItem.keyEquivalentModifierMask = [.command, .control]; fullscreenItem.target = self
        NSApp.mainMenu = menu; show()
        if !videoProbe { browser.delegate = self; browser.searchForServices(ofType: sundayServiceType, inDomain: "local.") }
        let heartbeat = Timer(timeInterval: 0.5, repeats: true) { [weak self] _ in self?.poll() }
        RunLoop.main.add(heartbeat, forMode: .common); timer = heartbeat
        NSEvent.addLocalMonitorForEvents(matching: .keyDown) { [weak self] event in
            guard let self = self, self.window.isKeyWindow else { return event }
            // WebKit can use a text-view responder too. Only the visible pairing
            // field is text entry; do not discard arrows in the PPT viewer.
            if !self.code.isHidden, self.window.firstResponder === self.code.currentEditor() { return event }
            if !event.modifierFlags.intersection([.command, .control, .option, .shift]).isEmpty { return event }
            if event.keyCode == 123 { self.back(); return nil }
            if event.keyCode == 124 { self.forward(); return nil }
            return event
        }
    }
    @objc func toggleFullScreen() { window.toggleFullScreen(nil) }
    func windowDidEnterFullScreen(_ notification: Notification) { fullScreen.title = "退出全螢幕" }
    func windowDidExitFullScreen(_ notification: Notification) { fullScreen.title = "全螢幕" }
    func windowWillClose(_ notification: Notification) { setViewingActive(false) }
    func windowDidMiniaturize(_ notification: Notification) { setViewingActive(false) }
    func setViewingActive(_ active: Bool) {
        if active && viewingActivity == nil {
            viewingActivity = ProcessInfo.processInfo.beginActivity(options: [.userInitiated, .idleDisplaySleepDisabled], reason: "觀看主日 PPT 並保持講員連線")
        } else if !active, let activity = viewingActivity {
            ProcessInfo.processInfo.endActivity(activity); viewingActivity = nil
        }
    }
    @objc func show() { window.makeKeyAndOrderFront(nil); NSApp.activate(ignoringOtherApps: true) }
    func netServiceBrowser(_ browser: NetServiceBrowser, didFind service: NetService, moreComing: Bool) {
        services.append(service); computers.addItem(withTitle: service.name)
        service.delegate = self; service.resolve(withTimeout: 8)
    }
    func netServiceBrowser(_ browser: NetServiceBrowser, didRemove service: NetService, moreComing: Bool) {
        if connectedService == service {
            setViewingActive(false); resetVideo("等待主電腦重新連線…"); connectionGeneration += 1
            endpoint = nil; connectedService = nil; token = nil; busy = false; polling = false
            previous.isEnabled = false; next.isEnabled = false
        }
        if let index = services.firstIndex(where: { $0 == service }) { services.remove(at: index); computers.removeItem(at: index) }
    }
    func netServiceBrowser(_ browser: NetServiceBrowser, didNotSearch errorDict: [String: NSNumber]) {
        NSLog("Sunday Remote Bonjour search failed: %@", errorDict)
        status.stringValue = errorDict["NSNetServicesErrorCode"]?.intValue == -72008 ?
            "請把 Sunday Remote 安裝到 Applications，再重新開啟。" :
            "找不到主電腦。請確認同一個 Wi-Fi，並允許本機網路。"
    }
    @objc func selectComputer() {
        setViewingActive(false); resetVideo("正在切換主電腦…")
        connectionGeneration += 1; busy = false; polling = false
        token = nil; endpoint = nil; connectedService = nil; fingerprint = ""; binding = ""
        commandError = ""; commandErrorUntil = .distantPast
        previous.isEnabled = false; next.isEnabled = false
        code.isHidden = false; connect.isHidden = false
        status.stringValue = "正在尋找所選主電腦…"
        let index = computers.indexOfSelectedItem
        if services.indices.contains(index) {
            let service = services[index]
            resolved(service)
            if endpoint == nil { service.stop(); service.resolve(withTimeout: 8) }
        }
    }
    func netServiceDidResolveAddress(_ sender: NetService) { resolved(sender) }
    func refreshEndpoint() {
        guard let service = connectedService, Date().timeIntervalSince(lastResolve) >= 5 else { return }
        lastResolve = Date()
        // A restarted source can keep its Bonjour name/certificate but change
        // its port without a remove/add notification. Re-resolve after failure.
        service.stop(); service.resolve(withTimeout: 4)
    }
    func resolved(_ service: NetService) {
        let index = computers.indexOfSelectedItem
        guard services.indices.contains(index), services[index] == service else { return }
        let text = service.txtRecordData().map(NetService.dictionary(fromTXTRecord:)) ?? [:]
        guard let raw = text["id"], let id = String(data: raw, encoding: .utf8), id.count == 64 else { return }
        for data in service.addresses ?? [] {
            var host = [CChar](repeating: 0, count: Int(NI_MAXHOST))
            let found = data.withUnsafeBytes { bytes -> Bool in
                guard let address = bytes.bindMemory(to: sockaddr.self).baseAddress, address.pointee.sa_family == UInt8(AF_INET) else { return false }
                return getnameinfo(address, socklen_t(data.count), &host, socklen_t(host.count), nil, 0, NI_NUMERICHOST) == 0
            }
            if found, let url = URL(string: "https://\(String(cString: host)):\(service.port)") {
                if endpoint != url { resetVideo("正在連接主電腦…") }
                endpoint = url; fingerprint = id
                connectedService = service
                // A remembered token belongs only to its exact pinned host.
                token = saved["fingerprint"] == id ? saved["token"] : nil
                code.isHidden = token != nil; connect.isHidden = token != nil
                if token != nil { poll() }
                else { status.stringValue = "找到主電腦，請輸入這台主電腦的六位數字。" }
                return
            }
        }
    }
    func urlSession(_ session: URLSession, didReceive challenge: URLAuthenticationChallenge,
                    completionHandler: @escaping (URLSession.AuthChallengeDisposition, URLCredential?) -> Void) {
        guard challenge.protectionSpace.authenticationMethod == NSURLAuthenticationMethodServerTrust,
              let trust = challenge.protectionSpace.serverTrust,
              let certificate = SecTrustGetCertificateAtIndex(trust, 0) else {
            completionHandler(.cancelAuthenticationChallenge, nil); return
        }
        let digest = SHA256.hash(data: SecCertificateCopyData(certificate) as Data).map { String(format: "%02x", $0) }.joined()
        // Exact device certificate pin. Never accept an arbitrary TLS certificate.
        guard digest == fingerprint else { completionHandler(.cancelAuthenticationChallenge, nil); return }
        completionHandler(.useCredential, URLCredential(trust: trust))
    }
    func request(_ path: String, body: [String: Any]? = nil, completion: @escaping (Result<[String: Any], Error>) -> Void) {
        guard let endpoint = endpoint else { completion(.failure(ControlError("請先在主電腦開啟講員翻頁。"))); return }
        var request = URLRequest(url: endpoint.appendingPathComponent(path)); request.cachePolicy = .reloadIgnoringLocalCacheData
        if let token = token { request.setValue("Bearer " + token, forHTTPHeaderField: "Authorization") }
        if let body = body { request.httpMethod = "POST"; request.setValue("application/json", forHTTPHeaderField: "Content-Type"); request.httpBody = try? JSONSerialization.data(withJSONObject: body) }
        let generation = connectionGeneration
        session.dataTask(with: request) { data, response, error in
            let result: Result<[String: Any], Error>
            if let error = error { result = .failure(error) }
            else if let data = data, let value = try? JSONSerialization.jsonObject(with: data) as? [String: Any] {
                if (response as? HTTPURLResponse)?.statusCode == 200 { result = .success(value) }
                else { result = .failure(ControlError(((response as? HTTPURLResponse)?.statusCode == 401 ? "PAIRING_REQUIRED: " : "") + (value["error"] as? String ?? "主電腦暫時無法翻頁。"))) }
            } else { result = .failure(ControlError("連線未完成。")) }
            DispatchQueue.main.async {
                guard generation == self.connectionGeneration else { return }
                completion(result)
            }
        }.resume()
    }
    @objc func pair() {
        guard !busy, code.stringValue.count == 6 else { status.stringValue = "請輸入主電腦的六位數字。"; return }
        busy = true
        request("pair", body: ["code": code.stringValue]) { result in
            self.busy = false
            do {
                let state = try result.get(); self.token = state["token"] as? String
                try self.savePair(); self.code.stringValue = ""; self.apply(state)
            } catch { self.status.stringValue = error.localizedDescription; self.refreshEndpoint() }
        }
    }
    func poll() {
        guard token != nil, !polling else { return }
        polling = true
        request("state") { result in
            self.polling = false
            switch result {
            case .success(let value): self.apply(value)
            case .failure(let error):
                self.setViewingActive(false); self.resetVideo("等待主電腦；畫面已停止。")
                self.previous.isEnabled = false; self.next.isEnabled = false
                self.status.stringValue = "等待主電腦；回到同一個 Wi-Fi 後自動重連。"
                self.refreshEndpoint()
                if error.localizedDescription.hasPrefix("PAIRING_REQUIRED:") {
                    self.token = nil; self.saved = [:]; SecItemDelete(self.keychainQuery() as CFDictionary)
                    self.code.isHidden = false; self.connect.isHidden = false; self.computers.isHidden = false
                    self.status.stringValue = "主電腦已重設配對，請重新輸入六位數字。"
                }
            }
        }
    }
    func apply(_ state: [String: Any]) {
        binding = state["binding"] as? String ?? ""
        let live = state["live"] as? Bool == true
        setViewingActive(live && window?.isVisible == true && window?.isMiniaturized == false)
        previous.isEnabled = live && !busy; next.isEnabled = live && !busy
        code.isHidden = token != nil; connect.isHidden = token != nil; computers.isHidden = false
        status.stringValue = Date() < commandErrorUntil ? commandError : "已連線 · \(state["deck"] as? String ?? "尚未播放")"
        if live, (state["capabilities"] as? [String])?.contains("ppt-video-v1") == true { pollVideo() }
        else { resetVideo(live ? "主電腦版本只支援翻頁，請更新 Presenter。" : "等待主電腦播放已確認的 PPT") }
    }
    func resetVideo(_ text: String) {
        if !videoSession.isEmpty || videoBusy { videoGeneration += 1 }
        videoSession = ""; videoBusy = false; videoClient = UUID().uuidString
        videoView?.stop(text)
    }
    func pollVideo() {
        guard videoView != nil, !videoBusy, Date() >= videoRetry else { return }
        videoBusy = true
        let generation = videoGeneration
        let body: [String: Any] = videoSession.isEmpty ? ["op": "start", "clientId": videoClient] : ["op": "poll", "sessionId": videoSession]
        request("video", body: body) { result in
            guard generation == self.videoGeneration else { return }
            self.videoBusy = false
            switch result {
            case .success(let value):
                self.videoSession = value["sessionId"] as? String ?? ""
                self.videoView?.apply(value)
            case .failure:
                self.resetVideo("畫面連線未完成，正在重試…")
                self.videoRetry = Date().addingTimeInterval(3)
            }
        }
    }
    func videoMessage(_ value: [String: Any]) {
        if videoProbe, value["op"] as? String != "stats" { emitJSON(["op": "diagnostic", "event": value["op"] ?? "", "error": value["error"] ?? ""]) }
        switch value["op"] as? String {
        case "failed":
            if let failed = value["sessionId"] as? String, failed != videoSession { return }
            resetVideo(value["error"] as? String ?? "畫面已斷線，正在重試…"); videoRetry = Date().addingTimeInterval(5)
        case "stats":
            guard value["sessionId"] as? String == videoSession else { return }
            videoStats = value
            if videoProbe { emitJSON(value) }
        case "answer":
            guard value["sessionId"] as? String == videoSession, let sdp = value["sdp"] as? String,
                  sdp.utf8.count <= 262144 else { return }
            let generation = videoGeneration
            request("video", body: ["op": "answer", "sessionId": videoSession, "sdp": sdp]) { result in
                guard generation == self.videoGeneration else { return }
                if case .failure(let error) = result {
                    if self.videoProbe { emitJSON(["op": "diagnostic", "answerError": error.localizedDescription]) }
                    self.resetVideo("畫面連線失敗，正在重試…"); self.videoRetry = Date().addingTimeInterval(3)
                }
            }
        default: break
        }
    }
    @objc func back() { flip("Left") }
    @objc func forward() { flip("Right") }
    func flip(_ key: String) {
        guard !busy, !binding.isEmpty else { return }
        busy = true; previous.isEnabled = false; next.isEnabled = false
        request("key", body: ["key": key, "binding": binding, "id": UUID().uuidString]) { result in
            self.busy = false
            switch result {
            case .success(let value): self.commandError = ""; self.commandErrorUntil = .distantPast; self.apply(value)
            case .failure(let error):
                self.commandError = error.localizedDescription; self.commandErrorUntil = Date().addingTimeInterval(8)
                self.status.stringValue = self.commandError; self.poll()
            }
        }
    }
    func applicationWillTerminate(_ notification: Notification) {
        setViewingActive(false); timer?.invalidate(); browser.stop(); resetVideo("已結束")
    }
}
