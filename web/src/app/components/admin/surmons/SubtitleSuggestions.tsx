"use client";
import { useEffect, useRef, useState } from "react";
export type SubtitleSuggestion = {
  after_index: string;
  text: string;
  level: number;
};
export function SubtitleSuggestions({
  suggestions,
  busy,
  onApply,
  onClose,
}: {
  suggestions: SubtitleSuggestion[];
  busy: boolean;
  onApply: (selected: SubtitleSuggestion[]) => void;
  onClose: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [rows, setRows] = useState(() =>
    suggestions.map((s) => ({
      ...s,
      text: s.text.replace(/^#{1,6}\s+/, ""),
      selected: true,
    })),
  );
  useEffect(() => {
    const node = dialog.current!;
    node.showModal();
    return () => node.close();
  }, []);
  return (
    <dialog
      ref={dialog}
      aria-labelledby="subtitle-proposals-title"
      onCancel={(e) => {
        e.preventDefault();
        if (!busy) onClose();
      }}
      className="fixed inset-0 max-h-[85vh] w-[min(95vw,45rem)] overflow-y-auto rounded-xl border p-5 backdrop:bg-black/40"
    >
      <h2 id="subtitle-proposals-title" className="text-lg font-semibold">
        小標題建議
      </h2>
      <p className="my-2 text-sm text-slate-600">
        小標題屬編輯整理，並非教授口述。可修改、取消勾選；既有小標題會保留。
      </p>
      {rows.map((row, i) => (
        <div key={i} className="my-3 rounded border p-3">
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={row.selected}
              disabled={busy}
              onChange={(e) =>
                setRows((old) =>
                  old.map((v, j) =>
                    j === i ? { ...v, selected: e.target.checked } : v,
                  ),
                )
              }
            />
            {row.after_index === "START"
              ? "全文開始之前"
              : `第 ${row.after_index} 段之後`}{" "}
            · {row.level === 1 ? "小標題" : "次標題"}
          </label>
          <input
            aria-label={`小標題建議 ${i + 1}`}
            className="mt-2 w-full rounded border p-2"
            value={row.text}
            disabled={busy}
            onChange={(e) =>
              setRows((old) =>
                old.map((v, j) =>
                  j === i ? { ...v, text: e.target.value } : v,
                ),
              )
            }
          />
        </div>
      ))}
      <div className="mt-4 flex justify-end gap-2">
        <button
          type="button"
          disabled={busy}
          className="rounded border px-3 py-2"
          onClick={onClose}
        >
          取消
        </button>
        <button
          type="button"
          disabled={
            busy ||
            !rows.some((r) => r.selected) ||
            rows.some((r) => r.selected && !r.text.trim())
          }
          className="rounded bg-blue-700 px-3 py-2 text-white disabled:opacity-40"
          onClick={() =>
            onApply(
              rows
                .filter((r) => r.selected)
                .map(({ after_index, text, level }) => ({
                  after_index,
                  text,
                  level,
                })),
            )
          }
        >
          {busy ? "正在儲存…" : "加入並儲存"}
        </button>
      </div>
    </dialog>
  );
}
