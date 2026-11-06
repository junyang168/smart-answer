if CommandLine.arguments.contains("--scope-probe") {
    _ = NSApplication.shared
    NSApp.setActivationPolicy(.prohibited)
    let remote = RemoteApp()
    remote.endpoint = URL(string:"https://127.0.0.1:43123")
    remote.fingerprint = String(repeating:"a",count:64)
    remote.token = String(repeating:"A",count:43)
    remote.live = true; remote.binding = "synthetic-stage"
    remote.observing.state = .on
    let observerBlocked = !remote.canFlip
    remote.forward() // Must not emit a request, even via the keyboard action.
    let observerDidNotSend = !remote.busy
    remote.observing.state = .off
    let controlEnabled = remote.canFlip
    remote.flip("Escape")
    let unsupportedKeyBlocked = !remote.busy
    remote.live = false
    let inactiveBlocked = !remote.canFlip
    remote.live = true; remote.token = nil
    emitJSON(["observerBlocked":observerBlocked,"observerDidNotSend":observerDidNotSend,
              "controlEnabled":controlEnabled,"inactiveBlocked":inactiveBlocked,
              "unpairedBlocked":!remote.canFlip,"unsupportedKeyBlocked":unsupportedKeyBlocked])
} else if CommandLine.arguments.contains("--video-probe") {
    let app = NSApplication.shared
    guard let line = readLine(), let data = line.data(using: .utf8),
          let value = try? JSONSerialization.jsonObject(with: data) as? [String: String],
          let raw = value["url"], let url = URL(string: raw) else { exit(2) }
    let delegate = RemoteApp(); delegate.videoProbe = true
    delegate.endpoint = url; delegate.fingerprint = value["fingerprint"] ?? ""; delegate.token = value["token"]
    app.delegate = delegate; app.setActivationPolicy(.regular)
    DispatchQueue.global().async {
        while let command = readLine() {
            DispatchQueue.main.async {
                switch command {
                case "right": delegate.forward()
                case "right-button": delegate.next.performClick(nil)
                case "right-viewer":
                    delegate.show(); delegate.window.makeFirstResponder(delegate.videoView?.webView)
                    if let event = NSEvent.keyEvent(with: .keyDown, location: .zero, modifierFlags: [], timestamp: ProcessInfo.processInfo.systemUptime, windowNumber: delegate.window.windowNumber, context: nil, characters: "\u{F703}", charactersIgnoringModifiers: "\u{F703}", isARepeat: false, keyCode: 124) {
                        NSApp.postEvent(event, atStart: false)
                    }
                case "left": delegate.back()
                case "fullscreen": delegate.fullScreen.performClick(nil)
                case "layout":
                    delegate.window.contentView?.layoutSubtreeIfNeeded()
                    let content = delegate.window.contentView!
                    let viewer = delegate.videoView!.webView.frame
                    emitJSON(["op": "layout", "fullscreen": delegate.window.styleMask.contains(.fullScreen),
                              "width": content.bounds.width, "height": content.bounds.height,
                              "viewerHeight": viewer.height, "viewerBottom": viewer.minY,
                              "controlsTop": delegate.next.convert(delegate.next.bounds, to: content).maxY,
                              "awake": delegate.viewingActivity != nil,
                              "fullscreenTitle": delegate.fullScreen.title])
                case "reconnect": delegate.resetVideo("測試重新連線")
                case "diagnose": delegate.videoView?.webView.evaluateJavaScript("JSON.stringify({url:location.href,ready:document.readyState,body:document.body?.innerHTML,pc:typeof RTCPeerConnection,viewer:typeof window.remoteVideo,bridge:typeof window.webkit.messageHandlers.video,pcstate:typeof current !== 'undefined' ? current?.pc?.connectionState : null,answer:typeof current !== 'undefined' ? !!current?.pc?.localDescription : false})") { value, error in emitJSON(["op": "diagnose", "value": value ?? NSNull(), "error": error?.localizedDescription ?? ""]) }
                case "sample": delegate.videoView?.webView.evaluateJavaScript("window.remoteVideo.sample()") { value, error in emitJSON(["op": "sample", "value": value ?? NSNull(), "error": error?.localizedDescription ?? ""]) }
                case "snapshot":
                    delegate.videoView?.webView.takeSnapshot(with: nil) { image, error in
                        if let image = image, let raw = image.tiffRepresentation, let bitmap = NSBitmapImageRep(data: raw), let png = bitmap.representation(using: .png, properties: [:]), let path = value["snapshot"] {
                            try? png.write(to: URL(fileURLWithPath: path)); emitJSON(["op": "snapshot", "ok": true])
                        } else { emitJSON(["op": "snapshot", "ok": false]) }
                    }
                case "quit": app.terminate(nil)
                default: break
                }
            }
        }
        DispatchQueue.main.async { app.terminate(nil) }
    }
    app.run()
} else if CommandLine.arguments.contains("--probe") {
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
