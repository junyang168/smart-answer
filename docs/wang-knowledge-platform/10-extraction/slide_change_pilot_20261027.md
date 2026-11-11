# 投影片換頁辨識試跑：S 210307 前 15 分鐘

> **讀者**：Developer 與負責核對來源的同工。
> **類型**：記錄。
> **狀態**：當前。
> **與代碼對齊**：2026-10-27（核對到 `821755ea`）。
> **權威範圍**：本片段的實測、費用、標注訂正與限制；不是整批影片的準確率或上線准入。

## 1. 已完成的實測

使用 `S 210307.mp4` 的 `00:00–15:00`，原始畫面 1920×1080。
來源 SHA-256：`43443ed0c1c0c25cc25378379fca26cfc53e1aafaedee1f4b8f0fab20419fe72`。
這段有 PowerPoint 編輯／播放模式、繁體中文、英文、希臘文、逐行出現的文字和游標。
沒有用本片段驗證希伯來文、白底／圖片投影片、遮擋或攝影機拍攝的投影幕。

| 項目 | 實測 |
| --- | --- |
| 取樣 | 1,731 張；設定 500ms，這份影片實際間隔 520ms |
| 固定間隔巡覽 | 15 張聯絡表，實際巡覽間隔 2,080ms；在偵測前檢視並凍結標注 |
| 畫面差異候選 | 41 |
| 保留的可見內容狀態 | 24，含首次取得；後續 23 次內容變化 |
| 分類 | 7 次新內容（含首次取得）、17 次內容增減 |
| 排除 | 14 次游標／控制項等相同內容，3 次空白過渡 |
| 模型一致性 | 兩個模型全部 41 個候選分類一致 |
| 原圖 | 24 份原尺寸 PNG 裁圖及完整影格，保留截圖時間、影格編號、ROI 和各檔 SHA |

**游標移動、形狀變化、輸入游標與選取框不算換頁。** 第一層可以召回這些畫面差異，但不得進入最後的投影片事件清單。
圖片中的游標沒有被移除，也沒有重繪投影片；原錄影已截掉的畫面底部內容無法恢復。

## 2. 實作與來源保存

入口是 [slide_change_pilot.py](../../../backend/pipeline/slide_change_pilot.py)，模型呼叫在
[slide_pilot_models.py](../../../backend/pipeline/slide_pilot_models.py)。沒有改動原來的轉錄器、來源逐字稿、知識庫或網站資料。

1. 順序解碼影片，讀取解碼器的 presentation milliseconds；不以影格編號乘平均 FPS 代造時間。
2. 保留 960px 寬的完整預覽，以藍底連通區提出 ROI；其他底色退回完整畫面，不能直接丟棄。
3. ROI 縮至 480×360，平滑後計算 RGB 差異至少 35 的像素比例；門檻 0.0025，至少持續 500ms 且相鄰畫面變化低於門檻才建立候選。
4. 每批 6 組前後比對，同時附上 ROI 和未裁切畫面的縮圖，讓模型排除游標、操作介面和空白。模型只回分類及候選 ID，不生成時間戳。
5. 對保留的狀態，在下一個候選前、穩定後最初約 3 秒內的最多 6 張取樣中，以 Laplacian 清晰度選圖。重新解碼原片，核對來源影格編號與 PTS，保存原尺寸 PNG。
6. 產生本地 HTML 時間線、全部候選的可展開比對、JSON 指標與費用記錄。圖片標籤是 AI 導覽說明，不當作教授的原話或逐字稿。

本次 ROI 仍是藍底啟發式，尚未實作通用 AI 投影片定位。時間欄位分開記錄「第一個候選差異」與「實際選圖時間」；前者可能受游標噪音影響，還不是經逐影格核對的內容起始點。
原生 rational PTS／time base 尚未輸出；短於取樣間隔的狀態、連續動畫、快速返回，以及同頁跨狀態的語義身份仍需後續驗證。

## 3. 獨立標注及其訂正

初始標注由 **Codex 視覺檢視**建立，**不是同工人工確認**。
[凍結標注](../../../backend/tests/fixtures/slide_pilot/s210307_first15m_reference.json) 保存來源、manifest 及巡覽圖 SHA。
先建立標注，再執行偵測器；沒有拿候選清單倒造初始標注。

原始標注有 24 次後續內容變化。零額外容差、以標注區間一對一配對，命中 22 次；輸出 23 次。偵測後回看原影格，發現兩處標注錯誤：

- `r-003`：把記述式文字出現的時間標早一格；245440ms 原影格仍只有希臘文標題，246480ms 才有文字。
- `r-022`：把畫面底部之外的文字誤讀成新一行；807040ms 與 809120ms 都只顯示到「人民日報」，沒有可見的新內容。

[訂正紀錄](../../../backend/tests/fixtures/slide_pilot/s210307_first15m_reference_errata.json) 另存理由和原影格 SHA；原凍結標注沒有改寫。
訂正後 23 次內容變化全部配對，但這是**事後診斷**，不能宣稱獨立測試 100% 準確。
這份資料是開發片段；下一次評估需要另外的片段、較密集的標注與同工確認。

## 4. 模型、費用與耗時

| 模型 | 成功辨識請求 | 輸入 tokens | 輸出含推理 tokens | 模型請求耗時 | 保守費用估算 |
| --- | --- | --- | --- | --- | --- |
| `gemini-3.8-flash` | 7 | 46,400 | 7,297 | 74.3 秒 | US$0.06216 |
| `gpt-6.1-sol` | 7 | 58,038 | 2,291 | 45.0 秒 | US$0.16801 |
| 合計 | 14 | — | — | — | US$0.23017 |

取樣另外約 10.0 秒；表中耗時不含 token 計數、圖片準備、原圖輸出及 Codex／同工檢視時間，不能當成整個工作的耗時。
Gemini 首次請求因預付額度不足回傳 402；補值後完成，失敗紀錄另存。

費用依 2026-10-27 的官方頁面和回應 usage 估算，未與帳單核對：
[Gemini pricing](https://ai.google.dev/gemini-api/docs/pricing)、
[GPT-6.1 Sol](https://developers.openai.com/api/docs/models/gpt-6.1-sol)。
Gemini 採輸入 US$0.75／百萬、輸出 US$3.75／百萬，輸出含 thinking；OpenAI 為保守估算，所有輸入按包含 cache-write 溢價的 US$2.50／百萬、輸出 US$10／百萬，不申報快取折扣。不能把 reasoning 再加到已包含它的 output token 數。

每個輸出目錄預設最多 16 次生成嘗試、預留上限 US$3；單次 timeout 90 秒，SDK 自動重試關閉。送出前落盤 request 及狀態，完整回應保存後才驗證 ID。未知結果會阻止後續生成，不能盲目重送。
已知拒絕也不自動重送；恢復須先核對該次回應及保存狀態。不同 provider 的請求與回應分開保存，沒有自動切換模型。

## 5. 使用與檢查

在本卡 worktree，使用 `backend/.venv/bin/python`。以下先準備巡覽，**此階段不跑偵測器**：

```bash
backend/.venv/bin/python -m backend.pipeline.slide_change_pilot prepare \
  --video '/Volumes/Jun SSD/data/video/S 210307.mp4' \
  --out output/slide-pilot/s210307-first15m --end-ms 900000
```

檢視巡覽後另建 reference JSON，需有來源 SHA、manifest SHA、巡覽圖 SHA、事件時間區間及 `human_confirmed`。格式參考上面的凍結標注；那是本次記錄，不能直接充當另一份 manifest 的標注。
新目錄以 `freeze --reference <file> --out <dir>` 凍結，再依序執行 `detect`、`review --provider gemini`、`report`，各階段都傳相同的 `--out`。
也可明確指定 `--provider openai` 作比較。相同輸入續跑讀取保存回應；來源、取樣圖、比較圖或回應內容改變會停止，不能偷偷沿用舊結果。

本次產物位於 `output/slide-pilot/s210307-first15m/`，影片、巡覽、原圖及 raw model response 不進 Git。
[保存的結果 fixture](../../../backend/tests/fixtures/slide_pilot/s210307_first15m_results.json) 包含兩模型分類、候選、指標及費用；不含 API key。
`index.html` 可由本地靜態伺服器查看；這不是 staging／production 的網站入口。

```bash
backend/.venv/bin/python -m pytest backend/tests/test_slide_change_pilot.py -q
cd web && ./node_modules/.bin/tsc --noEmit
```

本次 12 個測試通過，涵蓋游標噪音、持續標示、非藍底回退、一對一配對、原圖 PTS／解析度、檔案竄改、保存回應續跑、未知請求阻擋和推理 token 計費。前端 type-check 通過。
尚未串接音訊轉錄、中文對齊、標點分段或逐字稿嵌圖，沒有部署。
