import AppKit
import ApplicationServices
import Foundation
import CoreAudio
import Security
import CryptoKit
import Darwin

struct ControlError: LocalizedError {
    let message: String
    var errorDescription: String? { message }
    init(_ message: String) { self.message = message }
}

// Changes only the remote laptop's output. The host keeps its headphones.
final class OutputMute {
    private var originals: [AudioObjectID: UInt32] = [:]
    private var volumes: [AudioObjectID: Float32] = [:]
    func mute() -> Bool {
        var device: AudioObjectID = 0
        var size = UInt32(MemoryLayout<AudioObjectID>.size)
        var address = AudioObjectPropertyAddress(mSelector: kAudioHardwarePropertyDefaultOutputDevice,
            mScope: kAudioObjectPropertyScopeGlobal, mElement: 0)
        guard AudioObjectGetPropertyData(AudioObjectID(kAudioObjectSystemObject), &address, 0, nil, &size, &device) == noErr,
              device != 0 else { return false }
        address = AudioObjectPropertyAddress(mSelector: kAudioDevicePropertyMute, mScope: kAudioDevicePropertyScopeOutput, mElement: 0)
        var settable: DarwinBoolean = false
        if !AudioObjectHasProperty(device, &address) ||
              AudioObjectIsPropertySettable(device, &address, &settable) != noErr || !settable.boolValue {
            address.mSelector = kAudioDevicePropertyVolumeScalar
            guard AudioObjectHasProperty(device, &address),
                  AudioObjectIsPropertySettable(device, &address, &settable) == noErr, settable.boolValue else { return false }
            var old: Float32 = 0; size = UInt32(MemoryLayout<Float32>.size)
            guard AudioObjectGetPropertyData(device, &address, 0, nil, &size, &old) == noErr else { return false }
            if volumes[device] == nil { volumes[device] = old }
            var zero: Float32 = 0
            guard AudioObjectSetPropertyData(device, &address, 0, nil, size, &zero) == noErr else { return false }
            return AudioObjectGetPropertyData(device, &address, 0, nil, &size, &old) == noErr && old == 0
        }
        var old: UInt32 = 0; size = UInt32(MemoryLayout<UInt32>.size)
        guard AudioObjectGetPropertyData(device, &address, 0, nil, &size, &old) == noErr else { return false }
        if originals[device] == nil { originals[device] = old }
        var value: UInt32 = 1
        guard AudioObjectSetPropertyData(device, &address, 0, nil, size, &value) == noErr else { return false }
        return AudioObjectGetPropertyData(device, &address, 0, nil, &size, &old) == noErr && old == 1
    }
    func restore() {
        for (device, original) in originals {
            var address = AudioObjectPropertyAddress(mSelector: kAudioDevicePropertyMute, mScope: kAudioDevicePropertyScopeOutput, mElement: 0)
            var value: UInt32 = 0; var size = UInt32(MemoryLayout<UInt32>.size)
            // Preserve a user's later manual change instead of overwriting it.
            if AudioObjectGetPropertyData(device, &address, 0, nil, &size, &value) == noErr && value == 1 {
                var old = original
                AudioObjectSetPropertyData(device, &address, 0, nil, size, &old)
            }
        }
        originals.removeAll()
        for (device, original) in volumes {
            var address = AudioObjectPropertyAddress(mSelector: kAudioDevicePropertyVolumeScalar, mScope: kAudioDevicePropertyScopeOutput, mElement: 0)
            var value: Float32 = 0; var size = UInt32(MemoryLayout<Float32>.size)
            if AudioObjectGetPropertyData(device, &address, 0, nil, &size, &value) == noErr && value == 0 {
                var old = original; AudioObjectSetPropertyData(device, &address, 0, nil, size, &old)
            }
        }
        volumes.removeAll()
    }
}

struct MeetingNode {
    let element: AXUIElement
    let role: String
    let text: String
}

var verifiedShares: Set<String> = []

final class MeetControl {
    let url: URL
    let speaker: Bool
    let deadline = Date().addingTimeInterval(45)
    init(url: URL, speaker: Bool) throws {
        guard url.scheme == "https", url.host == "meet.google.com",
              url.path.range(of: "^/[a-z]{3}-[a-z]{4}-[a-z]{3}$", options: .regularExpression) != nil else {
            throw ControlError("請先設定主日的 Meet 連結。")
        }
        self.url = url; self.speaker = speaker
    }
    func trusted(prompt: Bool = false) -> Bool {
        AXIsProcessTrustedWithOptions([kAXTrustedCheckOptionPrompt.takeUnretainedValue() as String: prompt] as CFDictionary)
    }
    func attribute(_ element: AXUIElement, _ name: String) -> Any? {
        var result: CFTypeRef?
        guard AXUIElementCopyAttributeValue(element, name as CFString, &result) == .success else { return nil }
        return result
    }
    func nodes(_ root: AXUIElement, limit: Int = 2500) -> [MeetingNode] {
        var result: [MeetingNode] = [], pending = [(root, 0)]
        while let (element, depth) = pending.popLast(), result.count < limit && Date() < deadline {
            let role = attribute(element, kAXRoleAttribute) as? String ?? ""
            let title = attribute(element, kAXTitleAttribute) as? String ?? ""
            let desc = attribute(element, kAXDescriptionAttribute) as? String ?? ""
            let value = attribute(element, kAXValueAttribute) as? String ?? ""
            let text = (!title.isEmpty ? title : !desc.isEmpty ? desc : value)
                .split(whereSeparator: \.isWhitespace).joined(separator: " ")
            result.append(MeetingNode(element: element, role: role, text: text))
            if depth < 35 {
                let children = attribute(element, kAXChildrenAttribute) as? [AXUIElement] ?? []
                pending.append(contentsOf: children.reversed().map { ($0, depth + 1) })
            }
        }
        return result
    }
    func meetingWindow() throws -> AXUIElement {
        guard trusted() else { throw ControlError("首次使用：請在系統設定的輔助使用中允許 Sunday Remote 控制 Meet。") }
        guard let chrome = NSRunningApplication.runningApplications(withBundleIdentifier: "com.google.Chrome").first else {
            throw ControlError("等待 Chrome 開啟 Meet。")
        }
        let app = AXUIElementCreateApplication(chrome.processIdentifier)
        AXUIElementSetMessagingTimeout(app, 1)
        // Chrome exposes its web controls only after an assistive client requests
        // accessibility mode. A DOM/CDP snapshot does not enable this native tree.
        AXUIElementSetAttributeValue(app, "AXEnhancedUserInterface" as CFString, kCFBooleanTrue)
        for window in attribute(app, kAXWindowsAttribute) as? [AXUIElement] ?? [] {
            for node in nodes(window) where node.role == "AXWebArea" {
                let raw = attribute(node.element, kAXURLAttribute)
                let page = (raw as? URL) ?? (raw as? String).flatMap(URL.init(string:))
                if page?.host == "meet.google.com", page?.path.hasSuffix(url.path) == true { return window }
            }
        }
        throw ControlError("請在 Chrome 登入 Google 並開啟主日 Meet。")
    }
    func matches(_ node: MeetingNode, _ labels: [String]) -> Bool {
        ["AXButton", "AXMenuButton", "AXMenuItem", "AXRadioButton", "AXPopUpButton", "AXTab"].contains(node.role) &&
            labels.contains(where: { node.text == $0 || node.text.hasPrefix($0 + " (") || node.text.hasPrefix($0 + "（") })
    }
    @discardableResult func press(_ labels: [String], in window: AXUIElement? = nil, allowed: () -> Bool = { true }) throws -> Bool {
        guard Date() < deadline else { throw ControlError("Meet 操作逾時，請重試。") }
        let root = try window ?? meetingWindow()
        guard let node = nodes(root).first(where: { matches($0, labels) }) else { return false }
        guard Date() < deadline else { throw ControlError("Meet 操作逾時，請重試。") }
        guard allowed() else { throw ControlError("操作已由主電腦取消。") }
        guard AXUIElementPerformAction(node.element, kAXPressAction as CFString) == .success else {
            throw ControlError("Meet 未接受操作，請查看會議視窗。")
        }
        return true
    }
    let onLabels = ["Turn off microphone", "关闭麦克风", "關閉麥克風", "Mute microphone"]
    let offLabels = ["Turn on microphone", "开启麦克风", "開啟麥克風", "Unmute microphone"]
    func microphoneMuted() throws -> Bool {
        let all = nodes(try meetingWindow())
        if all.contains(where: { matches($0, offLabels) }) { return true }
        if all.contains(where: { matches($0, onLabels) }) { return false }
        throw ControlError("無法確認 Meet 麥克風，請查看會議視窗。")
    }
    func microphone(muted: Bool, canOpen: () -> Bool = { true }) throws {
        if !speaker && !muted { throw ControlError("主電腦麥克風保持關閉。") }
        if try microphoneMuted() != muted {
            if !muted && !canOpen() { throw ControlError("主電腦已結束開麥指令。") }
            guard try press(muted ? onLabels : offLabels, allowed: muted ? { true } : canOpen) else { throw ControlError("找不到 Meet 麥克風按鈕。") }
        }
        guard try microphoneMuted() == muted else { throw ControlError("Meet 麥克風狀態未確認。") }
    }
    func joined() throws -> Bool {
        nodes(try meetingWindow()).contains(where: { matches($0, ["Leave call", "Leave meeting", "退出通话", "離開通話", "離開會議"]) })
    }
    func prepare() throws {
        guard trusted(prompt: true) else { throw ControlError("請先允許輔助使用權限，再按準備主日。") }
        if (try? meetingWindow()) == nil {
            DispatchQueue.main.sync {
                let options = NSWorkspace.OpenConfiguration()
                NSWorkspace.shared.open([url], withApplicationAt: URL(fileURLWithPath: "/Applications/Google Chrome.app"), configuration: options)
            }
        }
        let deadline = Date().addingTimeInterval(35)
        var last = "等待 Meet 載入。"
        while Date() < deadline {
            do {
                _ = try press(["Rejoin", "重新加入", "再次加入"])
                try microphone(muted: true)
                _ = try press(["Turn off camera", "关闭摄像头", "關閉攝影機"])
                if try joined() {
                    return
                }
                _ = try press(["Join here too", "也在此处加入", "也在此處加入", "Join now", "立即加入", "立即加入，不使用摄像头", "現在加入", "加入"])
            } catch { last = error.localizedDescription }
            Thread.sleep(forTimeInterval: 0.5)
        }
        throw ControlError(last)
    }
    func isRecording() throws -> Bool {
        let all = nodes(try meetingWindow())
        if all.contains(where: { matches($0, ["Stop recording", "停止录制", "停止錄製"]) ||
            ["Recording in progress", "正在录制", "錄製中"].contains($0.text) }) { return true }
        if all.contains(where: { matches($0, ["Start recording", "开始录制", "開始錄製"]) }) { return false }
        throw ControlError("錄影狀態尚未確認。")
    }
    func recording(start: Bool) throws {
        guard !speaker else { throw ControlError("錄影由主電腦控制。") }
        if (try? isRecording()) == start { return }
        if start { try disableRecordingExtras() }
        let labels = start ? ["Start recording", "开始录制", "開始錄製"] : ["Stop recording", "停止录制", "停止錄製"]
        if try !press(labels) {
            guard try press(["Meeting tools", "会议工具", "會議工具", "Activities", "活动", "活動"]) else {
                throw ControlError("找不到 Meet 錄影控制；請確認帳號及主持人權限。")
            }
            Thread.sleep(forTimeInterval: 0.3)
            _ = try press(["Recording", "录制", "录制 录制会议", "錄製", "錄影"])
            Thread.sleep(forTimeInterval: 0.3)
            if (try? isRecording()) == start { return }
            if start { try disableRecordingExtras() }
            guard try press(labels) else { throw ControlError("這個會議尚未提供錄影權限。") }
        }
        Thread.sleep(forTimeInterval: 0.3)
        let window = try meetingWindow()
        // Only confirm within a recording dialog, never a generic Start button.
        for node in nodes(window) where node.role == "AXDialog" || node.role == "AXSheet" ||
            attribute(node.element, kAXSubroleAttribute) as? String == "AXApplicationDialog" {
            let text = nodes(node.element).map(\.text).joined(separator: " ").lowercased()
            if text.contains("record") || text.contains("录制") || text.contains("錄") {
                _ = try press(start ? ["Start", "开始", "開始"] : labels, in: node.element)
            }
        }
        // Some Chrome builds expose web dialogs as plain groups. In that case
        // restrict confirmation to a known recording heading's nearby parent,
        // rather than searching the whole meeting for a generic Start button.
        let headings = start ? ["确保所有人均已准备好", "Make sure everyone is ready"] :
            ["要停止录制此会议吗？", "Stop recording this meeting?"]
        for heading in nodes(window) where headings.contains(heading.text) {
            var parent = heading.element
            for _ in 0..<3 {
                guard let next = attribute(parent, kAXParentAttribute) else { break }
                parent = next as! AXUIElement
                if try press(start ? ["Start", "开始", "開始"] : labels, in: parent) { break }
            }
        }
        let deadline = Date().addingTimeInterval(12)
        while Date() < deadline {
            if (try? isRecording()) == start { return }
            Thread.sleep(forTimeInterval: 0.5)
        }
        throw ControlError("尚未確認錄影狀態，請查看 Meet；不會顯示已開始。")
    }
    func disableRecordingExtras() throws {
        let labels = ["同时启用转录功能", "同时开始使用 Gemini 记录会议内容",
            "Also start a transcript", "Also start taking notes with Gemini"]
        for node in nodes(try meetingWindow()) where node.role == "AXCheckBox" && labels.contains(node.text) {
            if (attribute(node.element, kAXValueAttribute) as? NSNumber)?.boolValue == true {
                guard AXUIElementPerformAction(node.element, kAXPressAction as CFString) == .success else {
                    throw ControlError("請先關閉錄影面板的附加轉寫／Gemini 選項。")
                }
            }
        }
    }
    func sharePicker() throws -> AXUIElement {
        guard let chrome = NSRunningApplication.runningApplications(withBundleIdentifier: "com.google.Chrome").first else {
            throw ControlError("等待 Chrome 分享視窗。")
        }
        let app = AXUIElementCreateApplication(chrome.processIdentifier)
        AXUIElementSetMessagingTimeout(app, 1)
        for window in attribute(app, kAXWindowsAttribute) as? [AXUIElement] ?? [] {
            let title = attribute(window, kAXTitleAttribute) as? String ?? ""
            // The browser picker belongs to Chrome, not the Meet web area.
            // Scope it to this origin before touching the window choices.
            if title.contains("meet.google.com") &&
                ["Choose what to share", "选择要共享", "選擇要分享"].contains(where: title.contains) { return window }
        }
        throw ControlError("請在 Chrome 開啟 Meet 的視窗分享選擇器。")
    }
    func sharing() throws -> Bool {
        nodes(try meetingWindow()).contains(where: { matches($0, ["Stop presenting", "You're presenting", "You are presenting",
            "你正在展示", "您正在展示", "停止展示", "停止演示", "停止分享", "你正在分享"]) })
    }
    func shareOutput() throws {
        if try sharing() {
            guard verifiedShares.contains(url.path) else { throw ControlError("Meet 正在分享；請在控制台確認分享的是 OUTPUT 視窗。") }
            return
        }
        NSRunningApplication.runningApplications(withBundleIdentifier: "com.google.Chrome").first?.activate(options: [])
        guard try press(["Present now", "Share screen", "立即演示", "分享螢幕", "共享屏幕", "分享畫面"]) else {
            throw ControlError("請在 Meet 選擇分享 Sunday Presenter — OUTPUT 視窗。")
        }
        Thread.sleep(forTimeInterval: 0.5)
        let picker = try sharePicker()
        _ = try press(["Window", "窗口", "視窗"], in: picker)
        Thread.sleep(forTimeInterval: 0.5)
        // An exact audience-window match is required. Never choose a desktop,
        // private preparation window or controller as a fallback.
        guard try press(["Sunday Presenter — OUTPUT", "Sunday Presenter – OUTPUT"], in: picker) else {
            throw ControlError("請在分享選擇器選 Sunday Presenter — OUTPUT，再按分享。")
        }
        guard try press(["Share", "分享", "共享"], in: picker) else { throw ControlError("請確認分享 OUTPUT 的系統提示。") }
        let until = Date().addingTimeInterval(12)
        var confirmed = false
        repeat {
            if (try? sharing()) == true { confirmed = true; break }
            Thread.sleep(forTimeInterval: 0.25)
        } while Date() < until
        guard confirmed else { throw ControlError("Meet 尚未確認投影片分享；請查看 OUTPUT 分享狀態。") }
        verifiedShares.insert(url.path)
    }
    func confirmSharedOutput() throws {
        guard try sharing() else { throw ControlError("Meet 尚未開始分享。") }
        verifiedShares.insert(url.path)
    }
    func verifiedSharing() throws -> Bool {
        try sharing() && verifiedShares.contains(url.path)
    }
    func leave() throws {
        try microphone(muted: true)
        let window = try meetingWindow()
        guard try press(["Leave call", "Leave meeting", "退出通话", "離開通話", "離開會議"], in: window) else { throw ControlError("找不到離開會議按鈕。") }
        let until = Date().addingTimeInterval(8)
        while Date() < until {
            // Hosts see a second choice; leave only this computer. Never end
            // the meeting for every attendee as an automation fallback.
            _ = try press(["Just leave the call", "Just leave", "仅退出通话", "僅離開通話"], in: window)
            let all = nodes(window)
            if all.contains(where: { ["You left the meeting", "You've left the meeting", "您已退出会议", "你已退出会议", "你已离开会议", "已離開會議", "您已離開會議"].contains($0.text) }) {
                verifiedShares.remove(url.path); return
            }
            Thread.sleep(forTimeInterval: 0.3)
        }
        throw ControlError("請確認 Meet 已離開，麥克風已保持關閉。")
    }
}

func emitJSON(_ value: [String: Any]) {
    if let data = try? JSONSerialization.data(withJSONObject: value), let line = String(data: data, encoding: .utf8) {
        print(line); fflush(stdout)
    }
}
