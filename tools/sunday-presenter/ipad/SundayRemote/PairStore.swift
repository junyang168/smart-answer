import Foundation
import Security

protocol PairStorage {
    func load(_ fingerprint: String) -> String?
    func save(_ token: String, for fingerprint: String) throws
    func remove(_ fingerprint: String)
}

struct PairStore: PairStorage {
    #if os(macOS)
    private let service = "org.dallas-hlc.sunday-remote"
    #else
    private let service = "org.dallas-hlc.sunday-remote.ipad"
    #endif
    private func query(_ fingerprint: String) -> [String: Any] {
        [kSecClass as String: kSecClassGenericPassword,
         kSecAttrService as String: service,
         kSecAttrAccount as String: fingerprint]
    }
    static func legacyToken(_ data: Data, for fingerprint: String) -> String? {
        guard RemoteScope.validFingerprint(fingerprint),
              let value = try? JSONSerialization.jsonObject(with: data) as? [String: String],
              value["fingerprint"] == fingerprint, let token = value["token"],
              RemoteScope.validToken(token) else { return nil }
        return token
    }
    func load(_ fingerprint: String) -> String? {
        guard RemoteScope.validFingerprint(fingerprint) else { return nil }
        var q = query(fingerprint); q[kSecReturnData as String] = true
        var item: CFTypeRef?
        if SecItemCopyMatching(q as CFDictionary, &item) == errSecSuccess,
           let data = item as? Data, let token = String(data: data, encoding: .utf8),
           RemoteScope.validToken(token) { return token }
        #if os(macOS)
        // Preserve the installed Mac's old single-host pairing during upgrade.
        // Read only its exact host; new pairings use fingerprint-specific items.
        q = query("paired-presenter"); q[kSecReturnData as String] = true
        if SecItemCopyMatching(q as CFDictionary, &item) == errSecSuccess,
           let data = item as? Data { return Self.legacyToken(data, for: fingerprint) }
        #endif
        return nil
    }
    func save(_ token: String, for fingerprint: String) throws {
        guard RemoteScope.validFingerprint(fingerprint), RemoteScope.validToken(token) else { throw RemoteError("配對資料不完整。") }
        let data = Data(token.utf8)
        let status = SecItemUpdate(query(fingerprint) as CFDictionary, [kSecValueData as String: data] as CFDictionary)
        if status == errSecItemNotFound {
            var q = query(fingerprint); q[kSecValueData as String] = data
            q[kSecAttrAccessible as String] = kSecAttrAccessibleWhenUnlockedThisDeviceOnly
            let added = SecItemAdd(q as CFDictionary, nil)
            guard added == errSecSuccess else {
                NSLog("SundayRemote Keychain add failed: %d", added)
                throw RemoteError("無法儲存配對，請重新連線。")
            }
        } else if status != errSecSuccess {
            NSLog("SundayRemote Keychain update failed: %d", status)
            throw RemoteError("無法更新配對，請重新連線。")
        }
    }
    func remove(_ fingerprint: String) {
        guard RemoteScope.validFingerprint(fingerprint) else { return }
        SecItemDelete(query(fingerprint) as CFDictionary)
        #if os(macOS)
        var q = query("paired-presenter"); q[kSecReturnData as String] = true
        var item: CFTypeRef?
        if SecItemCopyMatching(q as CFDictionary, &item) == errSecSuccess,
           let data = item as? Data, Self.legacyToken(data, for: fingerprint) != nil {
            SecItemDelete(query("paired-presenter") as CFDictionary)
        }
        #endif
    }
}

struct RemoteError: LocalizedError {
    let message: String
    var errorDescription: String? { message }
    init(_ message: String) { self.message = message }
}
