import Foundation

final class FakeNode {
    let role: String
    let url: URL?
    let children: [FakeNode]
    init(_ role: String, _ url: String? = nil, _ children: [FakeNode] = []) {
        self.role = role; self.url = url.flatMap(URL.init(string:)); self.children = children
    }
}

let meeting = URL(string: "https://meet.google.com/abc-mnop-xyz")!
for value in ["https://meet.google.com/abc-mnop-xyz", "https://meet.google.com/meet/abc-mnop-xyz?authuser=1"] {
    precondition(isRequestedMeetPage(URL(string: value), meeting: meeting))
}
for value in ["http://meet.google.com/abc-mnop-xyz", "https://example.com/abc-mnop-xyz",
              "https://meet.google.com/another/abc-mnop-xyz", "https://meet.google.com/meet/def-mnop-xyz"] {
    precondition(!isRequestedMeetPage(URL(string: value), meeting: meeting))
}

let unrelated = FakeNode("AXWebArea", "https://mail.google.com/", (0..<5000).map { _ in FakeNode("AXGroup") })
let wrongRoom = FakeNode("AXWebArea", "https://meet.google.com/def-mnop-xyz")
let requested = FakeNode("AXWebArea", "https://meet.google.com/meet/abc-mnop-xyz")
var expanded: [FakeNode] = []
func find(_ root: FakeNode) -> Bool {
    hasRequestedMeetPage(in: root, meeting: meeting, role: { $0.role }, pageURL: { $0.url },
        children: { node in expanded.append(node); return node.children })
}
// The old eager walk examined up to 2,500 unrelated nodes before checking the
// next window, consuming the shared deadline. Never expand that content here.
let firstWindow = FakeNode("AXWindow", nil, [unrelated])
let secondWindow = FakeNode("AXWindow", nil, [wrongRoom, FakeNode("AXGroup", nil, [requested])])
precondition(!find(firstWindow))
precondition(find(secondWindow))
precondition(!expanded.contains(where: { $0 === unrelated || $0 === wrongRoom || $0 === requested }))
precondition(expanded.count < 10)
// A link/embedded node within an unrelated page cannot impersonate the room.
precondition(!find(FakeNode("AXWebArea", "https://example.com/", [requested])))
precondition(!hasRequestedMeetPage(in: secondWindow, meeting: meeting,
    role: { $0.role }, pageURL: { $0.url }, children: { $0.children }, canContinue: { false }))
print("Meet locator counterexamples passed")
