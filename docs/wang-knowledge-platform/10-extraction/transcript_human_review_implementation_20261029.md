# 圖文逐字稿首版實作驗證紀錄

> **讀者**：Developer、Solution architect、校對同工
> **類型**：記錄
> **狀態**：2026-10-29 本機驗證完成，未部署
> **與代碼對齊**：2026-10-29（#522 實作；基於 `f744d0bc` 設計）
> **權威範圍**：只記錄首版實作、自審與驗證；不代表真人校對、正式來源批准、production 部署或文庫抽取驗收。

## 1 本次接入

原 `/admin/surmons/[item]` 先查模式。未遷移稿繼續使用舊 editor；登記的候選與已採用圖文稿進入新審核畫面。單人認領、分段 Markdown 校對、投影片原圖／中間畫面／OCR／閱讀位置核對、自動儲存、可查看的歷史、明確發佈及取消認領已接通。

正文主本仍是 `script_review/<item>.json` 段落陣列。`script` 時間表與 `script_patched` 基線保留；時間、圖片、認領核對狀態及講道資料草稿放在 `transcript_review/<item>/context.json`。原始 pipeline 紀錄及分組另存 `source-record.json`、`source-layout.json`，原片在同一磁碟採 hard link、跨磁碟複製並核對 SHA，避免清理處理資料夾後無法繼續核對；不以外部試驗資料夾當唯一來源保存位置。

每次保存有不可覆寫的 revision、操作者、時間、前後 SHA、父版本與操作 ID。預寫檔完成後留下小型回執，恢復時只掃未完成操作，避免每次輸入都重讀全部歷史正文。跨講道的 metadata 恢復只更新自己的 row；舊 manager 存另一篇講道時保留新模式的當前認領。

發佈仍是 `{metadata, script}`；原圖、非口述標籤與受控 SVG 存入既有 text 內容。新 reader 從同一發佈 revision 顯示原圖及 OCR；不讀尚未發佈的草稿。取消認領保持已發佈快照及 `published` 狀態。

## 2 來源處理如何交接

先完成既有全片處理及閱讀排版，再明確登記，沒有新增模型呼叫：

```bash
backend/.venv/bin/python -m backend.pipeline.transcript_review_import \
  --root /path/to/application-data \
  --item S201011 \
  --source /path/to/saved-pipeline-output \
  --expected-script-sha256 <current-review-file-sha256>
```

`--source` 包含 `full_transcript.json`、`reading-layout/layout.json` 及原片／原圖。尚不存在 review 檔時最後一項使用 `missing`。登記只保存候選身份，不改正文；同工認領後按「採用圖文候選並開始審核」。已有人工差異、已發佈、來源 SHA 不符或原稿已變，均拒絕取代。

原視窗 ID 只留在附屬映射。新正文的 index/end_index 是穩定數字字串，時間表使用全片絕對時間；不觸發舊解析器的二十分鐘前綴偏移。

新 API 需要 backend 與 Next.js 相同的 server-only `TRANSCRIPT_REVIEW_ACTION_SECRET`；身份由 NextAuth session 取得並簽名，不信任 body 的 user ID。正式環境不啟用開發者身份替代。本機隔離測試另外明確設定 `TRANSCRIPT_REVIEW_DEV_ACTOR`。未遷移稿的模式探測不依賴這個新 secret。

正式啟用前需依 operations runbook 檢查所有接觸同一資料目錄的可寫服務，均使用本次保護；仍運行另一份舊程式的服務不能由這份代碼替它攔截寫入。本次沒有連接或改動 production。

## 3 自審時修正的問題

- 完整稿件的核對 SHA 與大段 metadata 不能塞入 HTTP 標頭。正文、附屬資料與核對 SHA 留在 ETag body；身份與簡短認領顯示資料才在獨立回應標頭。
- Next.js 的泛用 rewrite 原本會繞過登入代理。`/api/sc_api/` 已交回本地 route handler；新修改要求同源 Origin、簽名、精確版本及工作代號。
- 舊保存器及 publish 不能丟掉附屬資料後仍回成功。它們遇已遷移稿會拒絕；未遷移稿的真正保存／發佈器仍正常運作。
- 發佈前排空所有待存操作；操作 ID 先持久保存，未知結果查本人回執。每個分頁的本機草稿分開保存，衝突不盲目覆寫；可下載備份並載入最新稿。
- 圖片 caption、非口述標籤及 SVG 不得被抽成教授口述。來源投影只移除精確的 v1 視覺容器，SVG 仍形成獨立視覺單位。
- 人工確認閱讀位置及 OCR 不批准模型重建的來源忠實性。`visual_source_attestations` 保持空白；既有來源准入仍拒絕自動使用未批准視覺。

## 4 驗證結果

使用 production 同版本的 `backend/.venv` 執行相關 7 組測試，共 **189 passed**；前端 TypeScript 檢查通過。

涵蓋兩人認領競態、管理員接手、缺少／過期工作代號、版本衝突、操作重送、局部核對失效、原稿保護、跨檔恢復、第三種 SHA 拒絕覆蓋、其他講道 metadata 保留、簽名偽造／body 改動、精確 ETag、公開版固定、圖片校驗、舊讀取器及真正舊保存／發佈器、超過六十分鐘的時間，以及視覺／口述分離與未批准視覺拒絕准入。

瀏覽器實測使用獨立 SSD 資料目錄，API 9522、web 3522：

- S201011 完整候選：188 段、37 組圖片；認領、明確採用、Markdown 自動儲存、保存後撤銷、歷史前後差異、影片範圍讀取及原圖載入。
- 另外的兩段流程 fixture 明示「非正式講道稿」，測試未核對時拒絕發佈、逐段確認、圖片確認、發佈、取消認領、公開頁仍是發佈版本。
- 停止測試 API 後修改，頁面停止同步並保留本機操作；服務恢復、重新載入後可恢復待存修改，成功寫成單一新版本。測試插入的字句已還原。
- 完整 S201011 保持未發佈、零人工核對；沒有把測試點選當作整篇內容批准。

## 5 首版範圍與待驗證項

已移除 AI 問答、舊手動加圖／框選／生成入口及 F03 三種編輯器模式。舊頁面的其他選配保留；新模式提供基本格式、五秒跳轉、書籤、原稿差異及自動儲存的標題／簡介／重點／核心經文。

AI 生成 metadata／小標題未接入新保存服務，不能從舊 helper 背景改寫新模式正文。歷史回復、任意兩版本比較、事件篩選、音訊來源切換及網頁啟動／取消 AI 工作尚未開放。部分舊人工投影片內部程式仍留待退役，已無本 editor 的可見入口；共用 backend 截圖接口沒有在本次刪除。

中文組字暫停提交已接入；原生中文輸入法、窄螢幕與長時間連續同工操作仍需實機試用。瀏覽器演練不是此類完整可用性驗收。

正式視覺來源忠實複核、文庫准入，以及真實視覺證據穿過 Claim／論證／文章的端到端案例仍未完成。本次不建立來源批准、不寫知識庫、不產生文章、不合併或部署。
