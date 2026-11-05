import Foundation
import CryptoKit
import Security

var checks = 0
func check(_ condition: @autoclosure () -> Bool, _ message: String) {
    checks += 1
    if !condition() { fputs("FAIL: \(message)\n", stderr); exit(1) }
}
let a = String(repeating: "a", count: 64), b = String(repeating: "b", count: 64)
let tokenA=String(repeating:"A",count:43),tokenB=String(repeating:"B",count:43)
let legacyPair=try JSONSerialization.data(withJSONObject:["fingerprint":a,"token":tokenA])
check(PairStore.legacyToken(legacyPair,for:a)==tokenA,"Mac upgrade preserves its exact old host pairing")
check(PairStore.legacyToken(legacyPair,for:b)==nil,"old Mac token cannot authenticate a different host")
check(PairStore.legacyToken(Data("invalid".utf8),for:a)==nil,"invalid legacy payload is ignored")
let invalidPair=try JSONSerialization.data(withJSONObject:["fingerprint":a,"token":"invalid\nheader"])
check(PairStore.legacyToken(invalidPair,for:a)==nil,"legacy bearer token is validated")
let urlA = URL(string: "https://192.168.1.2:43123")!, urlB = URL(string: "https://192.168.1.3:43124")!
var scope = RemoteScope()
check(scope.observing && !scope.canFlip, "first run observes without controlling")
check(!RemoteScope.validCode("１２３４５６"), "reject Unicode lookalikes")
check(!RemoteScope.validCode("12345\n"), "reject non-digit pairing")
check(RemoteScope.validCode("012345"), "preserve leading zero")
check(!scope.select(endpoint: urlA, fingerprint: "malformed", savedToken: "secret-A"), "reject invalid identity")
check(!scope.select(endpoint: URL(string:"http://192.168.1.2:43")!, fingerprint:a, savedToken:nil), "HTTPS only")
check(scope.select(endpoint:urlA, fingerprint:a, savedToken:tokenA), "select pinned host")
let generationA = scope.generation
check(scope.acceptsPin(a,host:"192.168.1.2",port:43123), "accept exact identity and endpoint")
check(!scope.acceptsPin(b,host:"192.168.1.2",port:43123), "reject different cert")
check(!scope.acceptsPin(a,host:"192.168.1.3",port:43123), "reject other host")
check(!scope.acceptsPin(a,host:"192.168.1.2",port:443), "reject other port")
check(scope.apply(["live":true,"binding":"stage-A"],generation:generationA), "apply current state")
check(!scope.canFlip, "observer cannot flip a live paired host")
scope.observing=false;check(scope.canFlip, "explicit control enables flips")
scope.unavailable();check(!scope.canFlip, "lost state disables control")
check(scope.select(endpoint:urlB,fingerprint:b,savedToken:nil), "switch host")
check(scope.token==nil && scope.binding.isEmpty && !scope.live, "host switch clears credential and stage")
check(!scope.pair(token:"late-A",generation:generationA), "late pair cannot authenticate new host")
check(!scope.apply(["live":true,"binding":"old-stage"],generation:generationA), "late poll cannot activate new host")
let generationB=scope.generation
check(!scope.pair(token:"invalid\nheader",generation:generationB), "reject malformed bearer token")
check(scope.pair(token:tokenB,generation:generationB), "pair current host")
check(!RemoteScope.pairingRevoked(path:"pair",status:401),"wrong pairing code must retain its real error")
check(RemoteScope.pairingRevoked(path:"state",status:401),"revoked state token clears pairing")
check(scope.apply(["live":false,"binding":"stage-B"],generation:generationB), "accept inactive state")
check(!scope.canFlip, "nonlive host cannot flip even with binding")
scope.clear()
check(scope.token==nil && scope.endpoint==nil && !scope.canFlip, "background clears active identity and control")
check(!scope.apply(["live":true,"binding":"stage-B"],generation:generationB), "late background callback ignored")
// The real URLSession delegate must cancel redirects without forwarding a token.
let delegate = PinnedSessionDelegate(scope:scope)
var redirectCompleted=false
let response=HTTPURLResponse(url:urlA,statusCode:302,httpVersion:nil,headerFields:["Location":"https://example.com/"])!
delegate.urlSession(URLSession.shared,task:URLSession.shared.dataTask(with:urlA),willPerformHTTPRedirection:response,
                    newRequest:URLRequest(url:URL(string:"https://example.com/")!)) { request in
    check(request==nil,"refuse redirect before authorization can be forwarded");redirectCompleted=true
}
check(redirectCompleted,"redirect decision completes")
// Exercise the production TLS delegate with a real, test-only certificate.
final class TestProtectionSpace: URLProtectionSpace, @unchecked Sendable {
    var testTrust: SecTrust?
    override var serverTrust: SecTrust? { testTrust }
}
final class ChallengeSender: NSObject, URLAuthenticationChallengeSender {
    func use(_ credential: URLCredential, for challenge: URLAuthenticationChallenge) {}
    func continueWithoutCredential(for challenge: URLAuthenticationChallenge) {}
    func cancel(_ challenge: URLAuthenticationChallenge) {}
    func performDefaultHandling(for challenge: URLAuthenticationChallenge) {}
    func rejectProtectionSpaceAndContinue(with challenge: URLAuthenticationChallenge) {}
}
let certificateData=try Data(contentsOf:URL(fileURLWithPath:CommandLine.arguments[1]))
let certificate=SecCertificateCreateWithData(nil,certificateData as CFData)!
var trust: SecTrust?
check(SecTrustCreateWithCertificates(certificate,SecPolicyCreateBasicX509(),&trust)==errSecSuccess,"test trust created")
let digest=SHA256.hash(data:certificateData).map{String(format:"%02x",$0)}.joined()
var pinned=RemoteScope();check(pinned.select(endpoint:urlA,fingerprint:digest,savedToken:nil),"real certificate pin selected")
let tls=PinnedSessionDelegate(scope:pinned)
func challenge(_ host:String,_ port:Int,_ suppliedTrust:SecTrust?) -> URLAuthenticationChallenge {
    let space=TestProtectionSpace(host:host,port:port,protocol:"https",realm:nil,authenticationMethod:NSURLAuthenticationMethodServerTrust)
    space.testTrust=suppliedTrust
    return URLAuthenticationChallenge(protectionSpace:space,proposedCredential:nil,previousFailureCount:0,
                                      failureResponse:nil,error:nil,sender:ChallengeSender())
}
var decisions=0
tls.urlSession(URLSession.shared,didReceive:challenge("192.168.1.2",43123,trust)) { disposition,credential in
    check(disposition == .useCredential && credential != nil,"real exact certificate accepted");decisions+=1
}
for candidate in [challenge("192.168.1.3",43123,trust),challenge("192.168.1.2",443,trust),challenge("192.168.1.2",43123,nil)] {
    tls.urlSession(URLSession.shared,didReceive:candidate) { disposition,credential in
        check(disposition == .cancelAuthenticationChallenge && credential == nil,"wrong endpoint or missing certificate rejected");decisions+=1
    }
}
var differentIdentity=RemoteScope()
check(differentIdentity.select(endpoint:urlA,fingerprint:b,savedToken:nil),"different pin on same endpoint")
let wrongPin=PinnedSessionDelegate(scope:differentIdentity)
wrongPin.urlSession(URLSession.shared,didReceive:challenge("192.168.1.2",43123,trust)) { disposition,_ in
    check(disposition == .cancelAuthenticationChallenge,"different pinned certificate rejected at same endpoint");decisions+=1
}
check(decisions==5,"all TLS decisions completed")
print("PASS: \(checks) iPad production-scope/redirect checks; Mac SDK only, not iPad runtime acceptance")
