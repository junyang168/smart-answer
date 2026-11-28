import type { Root, Nodes, Parent, Text } from "mdast";

// Editorial ~~ spans preserve exact source characters. GFM's delimiter
// flanking rule otherwise leaves punctuation-final deletions literal in CJK.
export function remarkEditorialSoftDeletion() {
  return (root: Root) => {
    const walk = (node: Nodes) => {
      if (node.type === "delete" || node.type === "code" || node.type === "inlineCode") return;
      if (!("children" in node)) return;
      const parent = node as Parent;
      const result: Nodes[] = [];
      for (const child of parent.children) {
        if (child.type !== "text") { result.push(child); continue; }
        let cursor = 0;
        for (const match of child.value.matchAll(/~~([^\n]+?)~~/g)) {
          const index = match.index!;
          if (index > cursor) result.push({ type: "text", value: child.value.slice(cursor, index) } as Text);
          result.push({ type: "delete", children: [{ type: "text", value: match[1] }] });
          cursor = index + match[0].length;
        }
        if (cursor < child.value.length) result.push({ type: "text", value: child.value.slice(cursor) } as Text);
        else if (cursor === 0) result.push(child);
      }
      parent.children = result as Parent["children"];
      for (const child of parent.children) walk(child);
    };
    walk(root);
  };
}
