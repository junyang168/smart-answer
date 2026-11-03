import AppKit
import Foundation
import Security
import CryptoKit
import Darwin
let sundayServiceType = "__SUNDAY_SERVICE_TYPE__"

struct ControlError: LocalizedError {
    let message: String
    var errorDescription: String? { message }
    init(_ message: String) { self.message = message }
}

func emitJSON(_ value: [String: Any]) {
    if let data = try? JSONSerialization.data(withJSONObject: value), let line = String(data: data, encoding: .utf8) {
        print(line); fflush(stdout)
    }
}
