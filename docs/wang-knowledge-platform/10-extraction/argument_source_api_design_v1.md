# 論證層來源解耦 API 設計與 Eval v1

> **讀者**：Solution architect、Developer
> **類型**：規範
> **狀態**：當前
> **與代碼對齊**：2026-11-27（核對到 `feat/472-argument-source-api`）
> **權威範圍**：`argument-source-package.v1` 合約、講道存儲（`transcript_review`／`script_review`）與論證層之間的解耦邊界、投影片准入閘門與 Eval 驗收標準。

## 1. 目標與解耦邊界

論證層（`claim_creation_flash.py` 等下游消費者）不得直接耦合 `transcript_review/<item>/context.json`、`script_review/<item>.json` 或 `assets/` 目錄結構。所有講道資料統一經由 `argument_source` 介面（Python `build_argument_source_package` 與 FastAPI `GET /sc_api/argument_source/{item}`）輸出單一穩定合約 `argument-source-package.v1`：

1. **口述軌（`paragraphs`）**：只含口述段落（`paragraph_id`、`start_ms`、`end_ms`、`text`、`timing_status`），過濾小標題，不混入投影片文字。
2. **視覺軌（`visual_blocks`）**：只含非隱藏且通過准入閘門的投影片白底 SVG（`locator`、`before_paragraph_id`、`time_ms`、`svg_xml`、`svg_sha256`、`visible_text`、`visual_facts`、`source_modality: "visual"`）。
3. **准入閘門（Fail-Closed）**：
   - `hidden: true`（純經文頁、封面、冗餘 `build`）一律排除。
   - 僅 `reviews["g:" + group.id].decision == "confirmed"` 且 `svg_sha256`、`image_sha256` 吻合磁碟原檔者，才進入 `visual_blocks` 與 `visual_source_attestations`。未確認者留在 `pending_visual_blocks`，不進入論證層。
   - 沒有投影片的講道回傳 `visual_blocks: []`、`visual_source_attestations: {}`，與有投影片的講道共用同一合約。

## 2. Eval：Criteria

1. **格式解耦**：輸入端無須直接讀取 `context.json` 或 `script_review/` 即可透過 `argument-source-package.v1` 與 `project_argument_source` 取得段落與視覺區塊。
2. **無投影片相容性**：只有口述段落、無 `visual_assets` 的講道能正常輸出 `paragraphs` 與空的 `visual_blocks`。
3. **純經文與未確認攔截**：`hidden: true` 不得出現於 `visual_blocks` 或 `pending_visual_blocks`；未經 `confirmed` 審核的投影片不得進入 `visual_blocks` 或 `visual_source_attestations`。
4. **雜湊防竄改**：若磁碟上的 `reconstruction.svg` 或 `original.png` 與 `context.json` 記錄的 SHA256 不符，必須 fail-closed 拋錯拒絕。
5. **端點權限與回應**：`GET /sc_api/argument_source/{item}` 經正常身份驗證後回傳 `200` 與完整合約。

## 3. Eval：Key（`S 200607`）

以目前磁碟上的 `S 200607`（已匯入 26 張白底 SVG、45 個事件、28 組可見段落綁定、0 筆人工 `confirmed`）為基準：

- `paragraphs`：應含 15 個帶時間錨點的口述段落（對應 `paragraph_bindings`）。
- 預設嚴格模式下（`require_confirmed_visuals=True`）：`visual_blocks` 應為 **0**，`pending_visual_blocks` 應為 **28**，`visual_source_attestations` 為 `{}`。
- 確認後（或驗證預覽模式 `require_confirmed_visuals=False`）：`visual_blocks` 應為 **28**（排除 16 張 `hidden` 投影片），每筆皆含非空 `svg_xml`、`svg_sha256` 與 `visual_facts`。

## 4. Eval：Grading

每輪執行自動化測試與 `S 200607` 實測回報：
- 單元與 FastAPI 整合測試通過率（目標 100%）。
- `S 200607` 實測計數：`paragraphs` 數、`visual_blocks` 數、`pending_visual_blocks` 數、SHA 竄改測試攔截數。
