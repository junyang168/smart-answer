# 已校對講道的投影片匯入

> **讀者**：Developer、Solution architect
> **類型**：規範
> **狀態**：當前
> **與代碼對齊**：2026-11-27，#881 工作分支
> **權威範圍**：從一講影片到正式 Editor 草稿的自動處理和回歸驗證。

## Eval

沿用[投影片抽取 Eval](slide_extraction_eval_v1.md)的重要性、原圖忠實度和講解段落標準。工程回歸以[第一講位置表](../../../backend/tests/fixtures/slide_editor/matthew_2021_first.json)為草案。它凍結上次實際附上的每張及位置、五個重複畫面和十二個保留項，不能證明之前漏掉的內容。正式內容評分須由負責人確認答案。

以事件和段落配對計算命中、遺漏、多收、錯段、錯併。逐項檢查附件像素來源、白底、文字邊界、切換時間與取圖時間。任何無法驗證的候選保留原因。重跑操作、文字或時間變動、附件衝突須測試。比較寫入前後逐字稿、已發佈稿、歷史與講員段落。回放保存的模型判斷時直接讀階段產物，計分答案僅供事後比較。

## 設計與審查

輸入為影片、檔案識別、Editor 資料根目錄、該講來源影片的預期 SHA 與工作目錄。先快照逐字稿和段落時間，既有程式依次偵測、分類、辨識、判重要性和段落，獨立核對原圖。批次限制由程序控制。每張保留來源與兩種時間。審查時發現三個風險：

- 幀相似或時間短仍可能代表新重點。先判讀內容，重複附件只在同段且證據足夠時合併。
- 獨立覆核可漏掉實際排版碰撞。用瀏覽器量字，超界或重疊的結果列入報告。
- Editor 可能在長時間抽取時被校訂。匯入使用鎖內版本比對與固定操作收據，已存在的不同附件拒絕覆蓋。

預設產生本地逐張處理報告。明確指定寫入時，合格附件經既有版本化匯入器一次寫入。發佈與論證層由各自流程負責。

## 操作

在開發環境準備 ffmpeg、ffprobe、Gemini 憑證及 Chrome 或 Chromium。可用 CHROME_BIN 指定瀏覽器。用資料庫內既有的講道 ID、對應影片及其已知 SHA 執行：

```bash
backend/.venv/bin/python -m backend.pipeline.editor_slide_runner \
  --video "$video" --sha256 "$source_sha" --item "$item" \
  --root "$editor_data_root" --out "$work_dir" --apply
```

省略 `--apply` 即預演。在同一工作目錄重試可重用模型回覆與 Editor 收據。每項結果記於 `report.json`，包含原影片時間、取圖時間、段號、標題與入選或保留理由。程式依原圖黃色像素檢查漏掉的強調，並用 Chromium 實際排版檢查文字重疊、越界。獨立核對涵蓋文字、其他用色與圖形。無法通過的圖留在工作目錄供檢視。

離線重播可加 `--replay "$capture_dir"`，輸出另用獨立目錄。舊回放沿用當時的候選清單，新影片對每個已分類事件執行重要性判斷。評分單獨執行：

```bash
backend/.venv/bin/python -m backend.pipeline.editor_slide_eval \
  backend/tests/fixtures/slide_editor/matthew_2021_first.json "$work_dir"
```

整講副本測試設定 `SLIDE_881_CAPTURE` 與 `SLIDE_881_VIDEO`，執行 `backend/tests/test_editor_slide_runner.py`。其餘測試亦可在無講道資料的環境執行，影片入口測試需要瀏覽器。每次部署之後以該版本的工作目錄執行。
