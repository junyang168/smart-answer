import Foundation
import Security

struct PairStore {
    private func query(_ fingerprint: String) -> [String: Any] {
        [kSecClass as String: kSecClassGenericPassword,
         kSecAttrService as String: "org.dallas-hlc.sunday-remote.ipad",
         kSecAttrAccount as String: fingerprint]
    }
    func load(_ fingerprint: String) -> String? {
        guard RemoteScope.validFingerprint(fingerprint) else { return nil }
        var q = query(fingerprint); q[kSecReturnData as String] = true
        var item: CFTypeRef?
        guard SecItemCopyMatching(q as CFDictionary, &item) == errSecSuccess, let data = item as? Data else { return nil }
        return String(data: data, encoding: .utf8)
    }
    func save(_ token: String, for fingerprint: String) throws {
        guard RemoteScope.validFingerprint(fingerprint), RemoteScope.validToken(token) else { throw RemoteError("配對資料不完整。") }
        let data = Data(token.utf8)
        let status = SecItemUpdate(query(fingerprint) as CFDictionary, [kSecValueData as String: data] as CFDictionary)
        if status == errSecItemNotFound {
            var q = query(fingerprint); q[kSecValueData as String] = data
            q[kSecAttrAccessible as String] = kSecAttrAccessibleWhenUnlockedThisDeviceOnly
            guard SecItemAdd(q as CFDictionary, nil) == errSecSuccess else { throw RemoteError("無法儲存配對，請重新連線。") }
        } else if status != errSecSuccess { throw RemoteError("無法更新配對，請重新連線。") }
    }
    func remove(_ fingerprint: String) { SecItemDelete(query(fingerprint) as CFDictionary) }
}

struct RemoteError: LocalizedError {
    let message: String
    var errorDescription: String? { message }
    init(_ message: String) { self.message = message }
}
