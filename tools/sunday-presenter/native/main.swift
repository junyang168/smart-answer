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
} else if CommandLine.arguments.contains("--host") {
    _ = NSApplication.shared
    NSApp.setActivationPolicy(.prohibited)
    let worker = DispatchQueue(label: "sunday.host.meet")
    DispatchQueue.global().async {
        while let line = readLine() {
            worker.async {
                var response: [String: Any] = [:]
                do {
                    guard let data = line.data(using: .utf8),
                          let command = try JSONSerialization.jsonObject(with: data) as? [String: Any],
                          let id = command["id"] as? String, let action = command["action"] as? String,
                          let raw = command["meetingUrl"] as? String, let url = URL(string: raw) else {
                        throw ControlError("Invalid host request")
                    }
                    response["id"] = id
                    let meeting = try MeetControl(url: url, speaker: false)
                    switch action {
                    case "prepare": try meeting.prepare(); try meeting.shareOutput()
                    case "confirm-share": try meeting.microphone(muted: true); try meeting.confirmSharedOutput()
                    case "begin":
                        try meeting.microphone(muted: true)
                        guard try meeting.verifiedSharing() else { throw ControlError("請先確認 Meet 分享 OUTPUT 視窗。") }
                        try meeting.recording(start: true)
                    case "test-recording":
                        response["recordingNotStarted"] = true
                        try meeting.microphone(muted: true)
                        // Verify a complete start/stop during first-time setup.
                        // Do not stop a recording somebody else already started.
                        _ = try meeting.press(["Meeting tools", "会议工具", "會議工具"])
                        Thread.sleep(forTimeInterval: 0.5)
                        _ = try meeting.press(["Recording", "录制", "录制 录制会议", "錄製", "錄影"])
                        Thread.sleep(forTimeInterval: 0.5)
                        guard try meeting.isRecording() == false else { throw ControlError("會議已在錄影；測試不會中斷既有錄影。") }
                        response["recordingNotStarted"] = false
                        try meeting.recording(start: true)
                        Thread.sleep(forTimeInterval: 3)
                        try meeting.microphone(muted: true)
                        try meeting.recording(start: false)
                        response["recordingTestPassed"] = true
                    case "end":
                        try meeting.microphone(muted: true); try meeting.recording(start: false)
                        response["recording"] = false
                        try meeting.leave()
                        response["joined"] = false; response["microphoneMuted"] = true; response["sharing"] = false
                    case "mute": try meeting.microphone(muted: true)
                    case "leave":
                        try meeting.microphone(muted: true); try meeting.leave()
                        response["joined"] = false; response["microphoneMuted"] = true; response["sharing"] = false
                    case "status": try meeting.microphone(muted: true)
                    default: throw ControlError("Unknown host action")
                    }
                    response["ok"] = true
                    if response["joined"] == nil { response["joined"] = try meeting.joined() }
                    if response["microphoneMuted"] == nil { response["microphoneMuted"] = try meeting.microphoneMuted() }
                    if response["sharing"] == nil { response["sharing"] = try meeting.verifiedSharing() }
                    if response["recording"] == nil, let recording = try? meeting.isRecording() { response["recording"] = recording }
                } catch { response["ok"] = false; response["error"] = error.localizedDescription }
                emitJSON(response)
            }
        }
        DispatchQueue.main.async { exit(0) }
    }
    RunLoop.main.run()
} else {
    let app = NSApplication.shared
    let delegate = RemoteApp()
    app.delegate = delegate
    app.setActivationPolicy(.regular)
    app.run()
}
