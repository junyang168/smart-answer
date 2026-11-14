import Foundation

// Used by the actual network client, not a parallel test implementation.
struct RemoteScope {
    private(set) var generation = 0
    private(set) var endpoint: URL?
    private(set) var fingerprint = ""
    private(set) var token: String?
    private(set) var binding = ""
    private(set) var live = false
    var observing = true

    static func validFingerprint(_ value: String) -> Bool {
        value.count == 64 && value.unicodeScalars.allSatisfy { (48...57).contains($0.value) || (97...102).contains($0.value) }
    }
    static func validCode(_ value: String) -> Bool {
        value.utf8.count == 6 && value.utf8.allSatisfy { (48...57).contains($0) }
    }
    static func validToken(_ value: String) -> Bool {
        value.utf8.count == 43 && value.utf8.allSatisfy {
            (48...57).contains($0) || (65...90).contains($0) || (97...122).contains($0) || $0 == 45 || $0 == 95
        }
    }
    static func pairingRevoked(path: String, status: Int?) -> Bool { status == 401 && path != "pair" }
    mutating func clear() {
        generation += 1; endpoint = nil; fingerprint = ""; token = nil; binding = ""; live = false
    }
    mutating func select(endpoint: URL, fingerprint: String, savedToken: String?) -> Bool {
        guard Self.validFingerprint(fingerprint), endpoint.scheme == "https", endpoint.host != nil,
              endpoint.user == nil, endpoint.password == nil, endpoint.port != nil else { return false }
        clear(); self.endpoint = endpoint; self.fingerprint = fingerprint
        if let savedToken = savedToken, Self.validToken(savedToken) { token = savedToken }
        return true
    }
    mutating func pair(token: String, generation: Int) -> Bool {
        guard generation == self.generation, endpoint != nil, Self.validToken(token) else { return false }
        self.token = token; return true
    }
    mutating func apply(_ state: [String: Any], generation: Int) -> Bool {
        guard generation == self.generation, endpoint != nil, token != nil else { return false }
        binding = state["binding"] as? String ?? ""; live = state["live"] as? Bool == true
        return true
    }
    mutating func unavailable() { binding = ""; live = false }
    var canFlip: Bool { !observing && live && !binding.isEmpty && token != nil && endpoint != nil }
    func acceptsPin(_ digest: String, host: String, port: Int) -> Bool {
        Self.validFingerprint(digest) && digest == fingerprint && host == endpoint?.host && port == endpoint?.port
    }
}
