import { useMemo } from "react";
import { textChanges } from "./review-text-diff";

export function ReviewTextDiff({
  before = "",
  after = "",
}: {
  before?: string;
  after?: string;
}) {
  const changes = useMemo(() => textChanges(before, after), [before, after]);
  return (
    <div className="whitespace-pre-wrap break-words">
      {changes.map((change, index) =>
        change.kind === "delete" ? (
          <del
            key={index}
            className="bg-red-100 text-red-800"
            aria-label="刪除"
          >
            {change.text}
          </del>
        ) : change.kind === "insert" ? (
          <ins
            key={index}
            className="bg-green-100 text-green-800 no-underline"
            aria-label="新增"
          >
            {change.text}
          </ins>
        ) : (
          <span key={index}>{change.text}</span>
        ),
      )}
    </div>
  );
}
