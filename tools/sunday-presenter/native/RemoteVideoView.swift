import WebKit

final class RemoteVideoView: NSObject, WKScriptMessageHandler, WKNavigationDelegate, WKUIDelegate {
    let webView: WKWebView
    let source: URL
    var loaded = false
    var onMessage: (([String: Any]) -> Void)?
    var lastValue: [String: Any]?
    var pendingStop: String?
    init(resources: URL) {
        source = resources.appendingPathComponent("media/remote.html")
        let configuration = WKWebViewConfiguration()
        configuration.websiteDataStore = .nonPersistent()
        configuration.mediaTypesRequiringUserActionForPlayback = []
        #if os(iOS)
        configuration.allowsInlineMediaPlayback = true
        configuration.allowsPictureInPictureMediaPlayback = false
        #endif
        webView = WKWebView(frame: .zero, configuration: configuration)
        super.init()
        webView.configuration.userContentController.add(self, name: "video")
        webView.navigationDelegate = self; webView.uiDelegate = self
        webView.loadFileURL(source, allowingReadAccessTo: resources.appendingPathComponent("media"))
    }
    func call(_ method: String, _ value: Any) {
        guard loaded, let data = try? JSONSerialization.data(withJSONObject: value, options: [.fragmentsAllowed]),
              let json = String(data: data, encoding: .utf8) else { return }
        webView.evaluateJavaScript("window.remoteVideo.\(method)(\(json))", completionHandler: nil)
    }
    func apply(_ value: [String: Any]) { pendingStop = nil; lastValue = value; call("apply", value) }
    func stop(_ text: String) { lastValue = nil; pendingStop = text; call("stop", text) }
    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        loaded = true
        if let text = pendingStop { call("stop", text) }
        else if let value = lastValue { call("apply", value) }
    }
    func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        let allowed = navigationAction.targetFrame?.isMainFrame != false && navigationAction.request.url?.standardizedFileURL == source.standardizedFileURL
        decisionHandler(allowed ? .allow : .cancel)
    }
    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
        onMessage?(["op": "failed", "error": "Remote 畫面程式未載入：" + error.localizedDescription])
    }
    func userContentController(_ userContentController: WKUserContentController, didReceive message: WKScriptMessage) {
        guard message.webView === webView, message.frameInfo.isMainFrame, message.frameInfo.request.url?.standardizedFileURL == source.standardizedFileURL,
              let value = message.body as? [String: Any], let op = value["op"] as? String,
              ["answer", "failed", "stats"].contains(op) else { return }
        onMessage?(value)
    }
    @available(macOS 12.0, iOS 15.0, *)
    func webView(_ webView: WKWebView, requestMediaCapturePermissionFor origin: WKSecurityOrigin,
                 initiatedByFrame frame: WKFrameInfo, type: WKMediaCaptureType,
                 decisionHandler: @escaping (WKPermissionDecision) -> Void) { decisionHandler(.deny) }
    func webViewWebContentProcessDidTerminate(_ webView: WKWebView) {
        loaded = false; lastValue = nil; pendingStop = "畫面程序已停止，正在重連…"
        onMessage?(["op": "failed"])
        webView.reload()
    }
}
