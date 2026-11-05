import Foundation
import Security

protocol PairStorage {
    func load(_ fingerprint: String) throws -> String?
    func save(_ token: String, for fingerprint: String) throws
    func remove(_ fingerprint: String)
}

struct PairStore: PairStorage {
    private let service: String
    private let legacyService: String?
    init(service: String? = nil, legacyService: String? = nil) {
    #if os(macOS)
        self.service = service ?? "org.dallas-hlc.sunday-remote.v2"
        self.legacyService = legacyService ?? (service == nil ? "org.dallas-hlc.sunday-remote" : nil)
    #else
        self.service = service ?? "org.dallas-hlc.sunday-remote.ipad"
        self.legacyService = nil
    #endif
    }
    private func query(_ fingerprint: String, service: String? = nil) -> [String: Any] {
        [kSecClass as String: kSecClassGenericPassword,
         kSecAttrService as String: service ?? self.service,
         kSecAttrAccount as String: fingerprint]
    }
    static func legacyToken(_ data: Data, for fingerprint: String) -> String? {
        guard RemoteScope.validFingerprint(fingerprint),
              let value = try? JSONSerialization.jsonObject(with: data) as? [String: String],
              value["fingerprint"] == fingerprint, let token = value["token"],
              RemoteScope.validToken(token) else { return nil }
        return token
    }
    static func decodedToken(status: OSStatus, data: Data?) throws -> String? {
        if status == errSecItemNotFound { return nil }
        guard status == errSecSuccess else {
            throw RemoteError("配對資料仍保留，但 Keychain 暫時無法讀取（\(status)）。請解鎖裝置或允許 Sunday Remote 存取，再重新選擇主電腦。")
        }
        guard let data = data, let token = String(data: data, encoding: .utf8), RemoteScope.validToken(token) else {
            throw RemoteError("已存配對資料無法解讀，請核對後才重新配對。")
        }
        return token
    }
    func load(_ fingerprint: String) throws -> String? {
        guard RemoteScope.validFingerprint(fingerprint) else { return nil }
        var q = query(fingerprint); q[kSecReturnData as String] = true
        var item: CFTypeRef?
        let status = SecItemCopyMatching(q as CFDictionary, &item)
        if let token = try Self.decodedToken(status: status, data: item as? Data) { return token }
        #if os(macOS)
        // Authorized migration creates an item owned by the current stable
        // Apple-signed app, rather than retaining an old ad-hoc-code ACL.
        // Keep the old item as recovery material; never widen its access ACL.
        if let legacyService = legacyService {
            for account in [fingerprint, "paired-presenter"] {
                q = query(account, service: legacyService); q[kSecReturnData as String] = true
                let legacyStatus = SecItemCopyMatching(q as CFDictionary, &item)
                if legacyStatus == errSecItemNotFound { continue }
                let token = account == fingerprint
                    ? try Self.decodedToken(status: legacyStatus, data: item as? Data)
                    : legacyStatus == errSecSuccess ? (item as? Data).flatMap { Self.legacyToken($0, for: fingerprint) } : nil
                if legacyStatus != errSecSuccess {
                    throw RemoteError("舊配對仍保留，但 Keychain 無法讀取（\(legacyStatus)）。請允許 Sunday Remote 存取後重新選擇主電腦。")
                }
                if let token = token { try save(token, for: fingerprint); return token }
            }
        }
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
        if let legacyService = legacyService {
            SecItemDelete(query(fingerprint, service: legacyService) as CFDictionary)
            var q = query("paired-presenter", service: legacyService); q[kSecReturnData as String] = true
            var item: CFTypeRef?
            if SecItemCopyMatching(q as CFDictionary, &item) == errSecSuccess,
               let data = item as? Data, Self.legacyToken(data, for: fingerprint) != nil {
                SecItemDelete(query("paired-presenter", service: legacyService) as CFDictionary)
            }
        }
        #endif
    }
}

struct RemoteError: LocalizedError {
    let message: String
    var errorDescription: String? { message }
    init(_ message: String) { self.message = message }
}
