# 來源段落變更後更新 Claim 與 CVP

> **讀者**：Developer。
> **類型**：流程。
> **狀態**：當前。
> **與代碼對齊**：2026-10-27，`backend.pipeline.source_claim_updates`、`source_cvp_updates` 與現有 Claim／CVP／論證執行器。
> **權威範圍**：已登記來源版本的段落變更如何依序更新 Claim，再接續既有 CVP 與論證維護；不取代來源登記或各管理域的審閱、預算與提交規則。

來源流程比較已登記的新舊來源、列出受影響 Claim，依原文順序呼叫普通 Claim ID Update，
再將完成的當前 Claim 交給既有 CVP Update 與逐 CVP 論證 worker。
不增加來源影響模型審閱，不以「主張沒有變」省略 Update，也不更改 Claim 管理的既有契約。

```bash
PYTHONPATH=. backend/.venv/bin/python -m backend.pipeline.source_claim_updates \
  --source-id SRC-ID --before /absolute/before.json \
  --change-set-id KCS-ID --root /absolute/task \
  --instruction '來源校訂後完整 refresh 相關 Claim'
```

`--prepare-only` 只產生 Claim 清單，不呼叫模型、不建立 Claim action 或分配新 Claim ID。
`--detach` 沿用既有 batch 的背景啟動。預設執行完整接續；`--claim-only` 明確停在 Claim 階段。
Python 的 `run` 保留原 Claim 階段契約，完整入口為 `run_workflow`。
歷史 Claim-only 任務可用 `--downstream-instruction` 記錄後來授予的 CVP 接續方向，
不改寫原 Claim 指示、action 或預算。

來源版本登記必須已完成。入口核對 applied ChangeSet 的來源 before/after、current SQL 後像及實際檔案 SHA。
舊檔必須是原檔，或完整正文、純文字、標題結構、標題位置四項投影全部吻合原登記的恢復檔。
兩者 SHA 不冒充相同。新增、刪除、搬移段落或改變座標須另有映射，這個入口不猜測。

程式逐條檢查本篇 Claim 的完整自身證據及問題等相關 objects。
若任一使用變更段落，該 Claim 就列入完整 Update；共享 Question 的所有回答 Claim 都會列入。
單純 Claim 直接鄰居不因相連而入選。清單不判斷主張應否改寫；內容及相關 objects
由既有 GPT、Opus、多輪修改、交易及回讀流程負責。教授投影片與原話具有同等證據地位。

同一 Claim 使用多個變更段落時，歸入最早一段。先呼叫第一段的普通 Claim ID batch，
確認全部完成後才建立下一段的 batch，讓下一段讀到前段已提交的共享資料。
每段結束時核對本次截至該段的全部 action 及當前資格；後段使前段失效不能顯示完成。
原 action 都已完成、但同批已提交修改使舊讀回證明過期時，自動使用現有 Update 執行器作只讀核對，
只選當前失效的目標及本批新建 Claim。來源、Claim 本身或人工決定另有變更、本批以外寫入、
缺少原完整審閱、hold 或未決阻擋異議，不走此自動路徑。

只讀核對沒有修改、新建或退休許可，不分配 Claim ID；同一交接點只凍結一份清單，單 worker 執行。
每條最多初輪 Opus、一次 GPT 判定及一次 Opus 複核，額外核對的固定預算單列，不修改或重開原 Update 的預算。
無過期資格時沒有模型呼叫。失敗、答覆不確定或仍有實質問題就保留未完成，不換清單、root 或預算重試。
核對全部通過後再重新讀取整批當前資格；該只讀階段不能修改共用資料，因而不會反過來使前項失效。

每段通過後保存不可改寫的完成檢查點。中斷恢復可越過已通過的早段，接回實際造成新變更的後段；
檢查點只證明當時完成，不取代最後的當前資格核對。早段已過、後段寫入後中斷時，不重啟早段內容 Update。
重用 root 必須維持原來源、ChangeSet、方向與清單，已完成 action 沿用原記錄。

找不到任何既有 Claim 承載的變更段落，列為 `unowned_paragraphs` 並保持未完成，不能宣稱已補足新教導。
整篇來源的語義覆蓋、初次抽取及來源版本登記不由本清單入口推斷。

Claim 階段仍將 `claims_completed` 寫入原 `result.json`，保留已被歷史證明引用的 SHA。
完整入口另寫 `workflow-result.json`，不能把 Claim 階段完成寫成整個來源操作完成。

Claim 全數完成後，程式核對當前資格，透過現有反向依賴查詢找出受影響的有效 CVP。
將現有端點與完整當前成員交給 #474 的既有 maintain 入口；本批尚未連接的當前 Claim
作為明確候選一併審閱，不自動繼承舊連接或論證作用。沒有既有受影響 CVP 時，使用普通生成入口。
未連接者仍須有成分處置：新觀點、實際連接、明確背景或未決，不能默默略過。
單元過大時沿既有容量出口保留未完成，不截斷必需成員。

下游清單、policy、原指示與正常 CVP request 只凍結一次，保存於 `downstream/`。
CVP 的 GPT／Opus 多輪、授權、SQL transaction 與回讀完全使用既有執行器。
即使 CVP 文字不變，也處理其現有論證 handoff；逐一消費精確的 Route job，
沿用既有租約與 worker，不建立第二套模型調度或無界重試。
已完成 job 必須通過原審閱、實際 ChangeSet、當前依賴及論證記錄的保存結果核對。
例外或仍在運行的 job 保留未完成，不自動換 job 或增加預算。

CVP／論證都完成後再次核對來源與全部 Claim 資格，才寫整體 `completed`。
CVP 已提交但論證未完成時保持 `paused`，其中的實際 CVP 結果仍披露 `arguments_pending`。
獨立品質審計另記 `not_performed`，不追加 Gemini 終審、全庫備份、文章生成或部署。
