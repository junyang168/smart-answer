final class RemoteApp: NSObject, NSApplicationDelegate, NetServiceBrowserDelegate, NetServiceDelegate, URLSessionDelegate {
    var window: NSWindow!
    let browser = NetServiceBrowser()
    var services: [NetService] = []
    var connectedService: NetService?
    let computers = NSPopUpButton()
    let code = NSTextField()
    let connect = NSButton(title: "第一次連線", target: nil, action: nil)
    let status = NSTextField(wrappingLabelWithString: "正在找主電腦…")
    let retry = NSButton(title: "完成登入／權限後重試", target: nil, action: nil)
    let previous = NSButton(title: "← 上一頁", target: nil, action: nil)
    let next = NSButton(title: "下一頁 →", target: nil, action: nil)
    var endpoint: URL?, fingerprint = "", token: String?, binding = ""
    var saved: [String: String] = [:]
    var busy = false, polling = false, audioBusy = false
    var preparedRun = "", pendingRun = "", phase = "idle", currentRun = "", microphoneKnown = false
    var failedRun = "", lastMeetingError = "", endedRun = ""
    var meetingURL: URL?, meeting: MeetControl?
    let speakerMute = OutputMute()
    let audioQueue = DispatchQueue(label: "sunday.remote.meet")
    var timer: Timer?
    var lastResolve = Date.distantPast
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
        loadPair()
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 380, height: 285),
            styleMask: [.titled, .closable, .miniaturizable], backing: .buffered, defer: false)
        window.title = "Sunday Remote · 講員翻頁"; window.center()
        window.level = .floating; window.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]
        window.isReleasedWhenClosed = false
        let stack = NSStackView(); stack.orientation = .vertical; stack.spacing = 12; stack.edgeInsets = NSEdgeInsets(top: 20, left: 20, bottom: 20, right: 20)
        stack.translatesAutoresizingMaskIntoConstraints = false
        let title = NSTextField(labelWithString: "講員翻頁"); title.font = .systemFont(ofSize: 22, weight: .semibold)
        code.placeholderString = "主電腦顯示的六位數字"; code.alignment = .center
        connect.target = self; connect.action = #selector(pair)
        previous.target = self; previous.action = #selector(back)
        next.target = self; next.action = #selector(forward)
        retry.target = self; retry.action = #selector(retryPreparation); retry.isHidden = true
        for button in [previous, next] { button.font = .systemFont(ofSize: 20, weight: .medium); button.bezelStyle = .rounded; button.isEnabled = false; button.heightAnchor.constraint(equalToConstant: 56).isActive = true }
        computers.target = self; computers.action = #selector(selectComputer)
        let keys = NSStackView(views: [previous, next]); keys.distribution = .fillEqually; keys.spacing = 10
        for view in [title, computers, code, connect, status, retry, keys] { stack.addArrangedSubview(view) }
        window.contentView!.addSubview(stack)
        NSLayoutConstraint.activate([stack.leadingAnchor.constraint(equalTo: window.contentView!.leadingAnchor), stack.trailingAnchor.constraint(equalTo: window.contentView!.trailingAnchor), stack.topAnchor.constraint(equalTo: window.contentView!.topAnchor)])
        let menu = NSMenu(); let appMenu = NSMenu(); let item = NSMenuItem(); item.submenu = appMenu; menu.addItem(item)
        appMenu.addItem(withTitle: "顯示遙控器", action: #selector(show), keyEquivalent: "p").target = self
        appMenu.addItem(withTitle: "離開 Sunday Remote", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        NSApp.mainMenu = menu; show()
        browser.delegate = self; browser.searchForServices(ofType: sundayServiceType, inDomain: "local.")
        timer = Timer.scheduledTimer(withTimeInterval: 0.5, repeats: true) { [weak self] _ in self?.poll() }
        NSEvent.addLocalMonitorForEvents(matching: .keyDown) { [weak self] event in
            guard let self = self, self.window.isKeyWindow, !(self.window.firstResponder is NSTextView) else { return event }
            if event.keyCode == 123 { self.back(); return nil }
            if event.keyCode == 124 { self.forward(); return nil }
            return event
        }
    }
    @objc func show() { window.makeKeyAndOrderFront(nil); NSApp.activate(ignoringOtherApps: true) }
    @objc func retryPreparation() { failedRun = ""; lastMeetingError = ""; retry.isHidden = true; poll() }
    func netServiceBrowser(_ browser: NetServiceBrowser, didFind service: NetService, moreComing: Bool) {
        services.append(service); computers.addItem(withTitle: service.name)
        service.delegate = self; service.resolve(withTimeout: 8)
    }
    func netServiceBrowser(_ browser: NetServiceBrowser, didRemove service: NetService, moreComing: Bool) {
        if connectedService == service { endpoint = nil; connectedService = nil }
        if let index = services.firstIndex(where: { $0 == service }) { services.remove(at: index); computers.removeItem(at: index) }
    }
    func netServiceBrowser(_ browser: NetServiceBrowser, didNotSearch errorDict: [String: NSNumber]) {
        NSLog("Sunday Remote Bonjour search failed: %@", errorDict)
        status.stringValue = errorDict["NSNetServicesErrorCode"]?.intValue == -72008 ?
            "請把 Sunday Remote 安裝到 Applications，再重新開啟。" :
            "找不到主電腦。請確認同一個 Wi-Fi，並允許本機網路。"
    }
    @objc func selectComputer() {
        token = nil; endpoint = nil; binding = ""; previous.isEnabled = false; next.isEnabled = false
        let index = computers.indexOfSelectedItem
        if services.indices.contains(index) { resolved(services[index]) }
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
        let text = service.txtRecordData().map(NetService.dictionary(fromTXTRecord:)) ?? [:]
        guard let raw = text["id"], let id = String(data: raw, encoding: .utf8), id.count == 64 else { return }
        if let remembered = saved["fingerprint"], remembered != id { return }
        guard saved.isEmpty || saved["fingerprint"] == id else { return }
        for data in service.addresses ?? [] {
            var host = [CChar](repeating: 0, count: Int(NI_MAXHOST))
            let found = data.withUnsafeBytes { bytes -> Bool in
                guard let address = bytes.bindMemory(to: sockaddr.self).baseAddress, address.pointee.sa_family == UInt8(AF_INET) else { return false }
                return getnameinfo(address, socklen_t(data.count), &host, socklen_t(host.count), nil, 0, NI_NUMERICHOST) == 0
            }
            if found, let url = URL(string: "https://\(String(cString: host)):\(service.port)") {
                endpoint = url; fingerprint = saved["fingerprint"] ?? id
                connectedService = service
                if let remembered = saved["token"] { token = remembered; poll() }
                else { status.stringValue = "找到主電腦，第一次請輸入六位數字。" }
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
        guard let endpoint = endpoint else { completion(.failure(ControlError("請先在主電腦按準備主日。"))); return }
        var request = URLRequest(url: endpoint.appendingPathComponent(path)); request.cachePolicy = .reloadIgnoringLocalCacheData
        if let token = token { request.setValue("Bearer " + token, forHTTPHeaderField: "Authorization") }
        if let body = body { request.httpMethod = "POST"; request.setValue("application/json", forHTTPHeaderField: "Content-Type"); request.httpBody = try? JSONSerialization.data(withJSONObject: body) }
        session.dataTask(with: request) { data, response, error in
            let result: Result<[String: Any], Error>
            if let error = error { result = .failure(error) }
            else if let data = data, let value = try? JSONSerialization.jsonObject(with: data) as? [String: Any] {
                if (response as? HTTPURLResponse)?.statusCode == 200 { result = .success(value) }
                else { result = .failure(ControlError(((response as? HTTPURLResponse)?.statusCode == 401 ? "PAIRING_REQUIRED: " : "") + (value["error"] as? String ?? "主電腦暫時無法翻頁。"))) }
            } else { result = .failure(ControlError("連線未完成。")) }
            DispatchQueue.main.async { completion(result) }
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
                self.previous.isEnabled = false; self.next.isEnabled = false
                self.status.stringValue = "等待主電腦；回到同一個 Wi-Fi 後自動重連。"
                self.refreshEndpoint()
                if error.localizedDescription.hasPrefix("PAIRING_REQUIRED:") {
                    self.token = nil; self.saved = [:]; SecItemDelete(self.keychainQuery() as CFDictionary)
                    self.code.isHidden = false; self.connect.isHidden = false; self.computers.isHidden = false
                    self.status.stringValue = "主電腦已重設配對，請重新輸入六位數字。"
                }
                if self.phase == "live", let url = self.meetingURL {
                    self.audioQueue.async { try? MeetControl(url: url, speaker: true).microphone(muted: true) }
                }
            }
        }
    }
    func apply(_ state: [String: Any]) {
        binding = state["binding"] as? String ?? ""
        let live = state["live"] as? Bool == true
        previous.isEnabled = live && !busy; next.isEnabled = live && !busy
        code.isHidden = token != nil; connect.isHidden = token != nil; computers.isHidden = token != nil
        phase = state["phase"] as? String ?? "idle"
        status.stringValue = lastMeetingError.isEmpty ? "已連線 · \(state["deck"] as? String ?? "尚未播放")" : lastMeetingError
        if let raw = state["meetingUrl"] as? String, let url = URL(string: raw) {
            meetingURL = url
            let run = state["runId"] as? String ?? ""
            currentRun = run
            updateMeeting(run: run)
        }
    }
    func updateMeeting(run: String) {
        guard !audioBusy, let url = meetingURL else { return }
        if failedRun == run && !["ending", "ended"].contains(phase) { return }
        audioBusy = true
        let currentPhase = phase
        audioQueue.async {
            var joined = false, muted = false, outputMuted = false, verified = false, detail = ""
            do {
                self.meeting = try MeetControl(url: url, speaker: true)
                if currentPhase == "ended" {
                    if self.endedRun != run { try self.meeting?.leave(); self.endedRun = run }
                    self.speakerMute.restore(); muted = true; verified = true
                } else if currentPhase != "idle" {
                    outputMuted = self.speakerMute.mute()
                    guard outputMuted else { throw ControlError("請把講員電腦的揚聲器靜音，避免回音。") }
                    if self.preparedRun != run && ["preparing", "ready", "live"].contains(currentPhase) {
                        try self.meeting?.prepare(); self.preparedRun = run
                    }
                    let shouldOpen = currentPhase == "live" && DispatchQueue.main.sync { self.phase == "live" && self.currentRun == run }
                    try self.meeting?.microphone(muted: !shouldOpen, canOpen: {
                        DispatchQueue.main.sync { self.phase == "live" && self.currentRun == run }
                    })
                    joined = (try self.meeting?.joined()) == true
                    muted = (try self.meeting?.microphoneMuted()) == true; verified = true
                }
            } catch { detail = error.localizedDescription }
            let report: [String: Any] = ["runId": run, "joined": joined, "microphoneMuted": muted,
                                          "microphoneVerified": verified, "speakerMuted": outputMuted, "error": detail]
            DispatchQueue.main.async {
                self.audioBusy = false; self.microphoneKnown = detail.isEmpty
                self.lastMeetingError = detail
                if !detail.isEmpty { self.status.stringValue = detail; self.failedRun = run; self.retry.isHidden = false }
                else { self.failedRun = ""; self.retry.isHidden = true }
                self.request("report", body: report) { _ in }
            }
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
            case .success(let value): self.apply(value)
            case .failure(let error): self.status.stringValue = error.localizedDescription; self.poll()
            }
        }
    }
    func applicationWillTerminate(_ notification: Notification) {
        timer?.invalidate(); browser.stop()
        if let url = meetingURL { try? MeetControl(url: url, speaker: true).microphone(muted: true) }
        speakerMute.restore()
    }
}
