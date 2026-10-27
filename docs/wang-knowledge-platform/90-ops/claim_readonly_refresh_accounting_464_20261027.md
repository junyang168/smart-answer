# 共享資料更新後的只讀資格更新計數

> **讀者**：Developer。
> **類型**：記錄。
> **狀態**：當前。
> **與代碼對齊**：2026-10-27，`claim_management.continuation_budget`、`claim_review_recovery.consumed_invalid_review`。
> **權威範圍**：本次 S0049 批次交接所發現的工程問題及驗證，不是內容審閱批准。

S0049 七個原始 action 均已完成。但 CL002 的正常 Update 新建 CL012，並在共享 E003 增加 owner；
這使 CL009、CL001 的先前讀回證明失效。原始 action 完成與當前資格分開保留，批次沒有進入 S0050。

不放寬共享資料沿用規則，不把新舊異議按措辭猜成同一問題。沿用既有、明確綁定前任 action 的
`qualification_refresh`，以當前 SQL 候選重新作 GPT／Opus 只讀核對。來源、歷史呼叫、舊內容寫入仍保留。
本記錄寫下時，真實只讀更新僅準備，尚未呼叫模型。

兩個小修正：只讀接續可移除原先的新建權限，不分配新 Claim ID；原已返回但未被接納的初輪答覆，
須重新驗證原材料、呼叫 reservation、供應商答覆及明確引文契約錯誤，才可算作已消耗的呼叫。
它不變成批准，原 started slot 不改寫，不確定呼叫仍不能按這條路放行。
真實兩個前任 action 的只讀檢查均計得 GPT 2、Opus 3 次，含原未接納答覆。

另外，來源入口的 CVP 交接資料只列當前 ready 的 active Claim；退休 Claim 另列 `retired_claim_ids`，
未執行 CVP 仍為 `not_executed`。

驗證使用 `backend/.venv` Python 3.13 與隔離 PostgreSQL：相關回歸中 94 項通過；
另兩項新增測試因測試授權少寫 `required_maintenance_keys: []` 失敗，補齊測試授權後，
`test_source_claim_updates.py` 全部 8 項通過，包括原答覆不可篡改、不可冒充批准、
只讀接續移除 creation grants 仍計入原耗用，以及退休／未 ready 的 Claim 不進 active 交接清單。
