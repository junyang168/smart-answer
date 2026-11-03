import Foundation

func isRequestedMeetPage(_ page: URL?, meeting: URL) -> Bool {
    guard let page = page, page.scheme == "https", page.host == "meet.google.com" else { return false }
    return page.path == meeting.path || page.path == "/meet" + meeting.path
}

// Search browser chrome, never the contents of an unrelated tab. Eagerly walking
// Gmail/Docs before inspecting the next window can exhaust the operation budget.
func hasRequestedMeetPage<Node>(in root: Node, meeting: URL,
    role: (Node) -> String, pageURL: (Node) -> URL?, children: (Node) -> [Node],
    canContinue: () -> Bool = { true }, limit: Int = 512) -> Bool {
    var pending = [(root, 0)], cursor = 0
    while cursor < pending.count && cursor < limit && canContinue() {
        let (node, depth) = pending[cursor]; cursor += 1
        if role(node) == "AXWebArea" {
            if isRequestedMeetPage(pageURL(node), meeting: meeting) { return true }
            continue
        }
        if depth < 24 {
            pending.append(contentsOf: children(node).prefix(max(0, limit - pending.count)).map { ($0, depth + 1) })
        }
    }
    return false
}
