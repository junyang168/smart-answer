if CommandLine.arguments.contains("--probe") {
    _ = NSApplication.shared
    NSApp.setActivationPolicy(.prohibited)
    guard let line = readLine(), let data = line.data(using: .utf8),
          let value = try? JSONSerialization.jsonObject(with: data) as? [String: String],
          let raw = value["url"], let url = URL(string: raw) else { exit(2) }
    let client = RemoteApp(); client.endpoint = url; client.fingerprint = value["fingerprint"] ?? ""; client.token = value["token"]
    client.request("state") { result in
        switch result {
        case .success(let data): emitJSON(["ok": true, "state": data]); exit(0)
        case .failure(let error): emitJSON(["ok": false, "error": error.localizedDescription]); exit(1)
        }
    }
    RunLoop.main.run()
} else {
    let app = NSApplication.shared
    let delegate = RemoteApp()
    app.delegate = delegate
    app.setActivationPolicy(.regular)
    app.run()
}
