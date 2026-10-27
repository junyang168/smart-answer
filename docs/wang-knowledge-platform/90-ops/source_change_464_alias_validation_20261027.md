# #464 既有來源資格的目錄搬遷驗證

> **讀者**：Developer。
> **類型**：記錄。
> **狀態**：當前。
> **與代碼對齊**：2026-10-27，`cvp_sql_commit.check_binding` 及來源歷史 program-readback 核對。
> **權威範圍**：本次目錄連結相容性與隔離測試，不批准真實內容。

既有 staging 原路徑經目錄連結指向搬遷後的同一檔案時，`check_binding` 不應因
`resolve()` 改變路徑字串而拒絕相同位元組。新核對保留原絕對路徑及檔案 SHA；
仍拒絕相對路徑、額外欄位、SHA 不符，原始 artifact 沒有改寫。
備份類 artifact 原有專用 archive reader／卷驗證仍執行。

歷史 Claim 的 program-readback 另核回讀檔案確實是該原 round 的實體檔案；
目錄連結可保留，其他 round、回讀身分、候選、原始 Opus、完整來源及 SQL pins 仍核對。
新來源的「無影響」結論仍不能補成缺失的內容資格，只讀鄰居不取得目標資格。

- 共享 binding、來源、Claim、CVP、論證回讀及 runtime 相關測試：**279 passed, 35 skipped，63.87 秒**。
- 隨後加入完整的舊 Claim 審閱目錄搬遷測試，核對其資格可由正常來源影響審閱接續；
  對應來源、binding、下游提交／恢復：**17 passed，9.75 秒**。
- 全部模型回答為合成資料，沒有真實模型呼叫或真實內容寫入。

前一批完整工程與來源預檢記錄見
[來源變更自動接續工程驗證](./source_change_464_engineering_validation_20261027.md)。
