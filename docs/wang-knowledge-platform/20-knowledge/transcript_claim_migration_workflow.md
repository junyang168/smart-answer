# Beta 逐字稿 → Claim 來源版本移植

> **讀者**：維護執行者、Developer。
> **類型**：流程。
> **狀態**：當前。
> **與代碼對齊**：2026-11-04，核對 `backend.pipeline.transcript_claim_migration`；隔離 SQL 驗證與真實內容審閱分開記錄。
> **權威範圍**：已獲授權的 Beta 來源凍結、來源版本交接及既有 Claim Update 接續；不授予內容批准、公開發布或 CVP／論證層批准。

## 1. 來源與範圍

每次指定現有 `source_id`、確切講道 ID、Beta 根目錄、固定輸出 root 及原始負責人指示。
重跑沿用同一 root、清單、來源、policy 與普通 Update action，不換 root 重置預算。
來源真的再改版時另作新版本交接，保留前次結果與尚未完成項。

`prepare` 讀取已登記舊檔及其正文雜湊、完整 Beta rows/context 和反向依賴，
凍結全部同源 Claim，將已被取代者單列。排序依原先綁定的來源段落，只決定執行安排，
不證明新引文位置。保留物理段落 ID、空白／全刪除段落與小標題，從 sidecar 取得確切時間。
人工校對狀態不由此流程更改，也不因 AI 處理而冒充人工通過。

## 2. 來源版本登記

`register --execute` 使用共同 store 的 plan/apply、版本 CAS、ChangeSet 及歷史，
只更新該 SourceDocument 的來源版本與內部不可變快照位置。
舊公開逐字稿與 Beta 編輯檔均不覆寫。新快照是內部候選來源，不是公開發布或 Claim 內容批准。
原件變更、Beta 漂移或多餘寫集均拒絕。若交易已提交但回執丟失，按精確 intent、
ChangeSet 前後雜湊與當前行恢復回執，不重複寫入。

來源版本改變後舊 Claim 資格不得充作新版本通過。歷史 action、模型答覆、預算和人工作出的
決定均保留；不得將一個舊的未知呼叫重新發出。新版本維護須有這次的明確指示。

## 3. 自動審查與修訂

`prepare-unit --execute` 接既有 `claim_management_intake.prepare` 的固定同源小組，
`run-unit --execute` 接既有 batch、Update、共用模型容量與持久呼叫帳。
沒有另一套生成、審閱、重試或寫入器。訂閱 backend 與 policy 在準備時凍結；
不默認使用其他工作曾獲准的 API 費用。

既有 Claim 先經完整自身來源與證據審閱；需修改時由既有 GPT／Opus 有界流程處理。
負責人不逐條核對；執行者處理材料、程式、普通模型異議及合法接續，只有原文確實不能
裁定的內容才交負責人。真正人工 hold 保留；舊 AI 抽查標記依既有精確投影規則核實。

軟刪除不進有效來源：來源投影與精確引文校驗沿用共同 `source_projection`，
Claim 摘要也不得重新引入只有已刪文字才支持的教導。另有未刪除教授原話支持同一判斷時，
須重新明確綁定該證據。聽眾的問題／插話只作語境，不能直接冒充教授斷言。
經文、否定、條件、反方、論證步驟及自身證據充分性均由既有完整審閱處理。

小組按來源順序推進。程序停止後由執行者查保存答覆、實際 worker、SQL checkpoint 與
原剩餘預算，依既有恢復接點續跑；不清空舊錯誤或盲重送。後組修改共享材料時核對前組資格。

## 4. 回讀與下游交接

`verify --execute` 消費現有當前資格，另外核對實際有效正文中的引用，不建立第二套批准。
原分母每條都有保留、更新、替代、退役或未完成去向；新建項另列。
尚未逐項核實退休證明、未用的來源支援記錄、獨立來源覆蓋、CVP／論證連結及讀者消費端時，
不得宣稱整個 migration 完成。`cvp_route_migration_complete=false` 明確保留該下游邊界。

CLI 接點：

```bash
backend/.venv/bin/python -m backend.pipeline.transcript_claim_migration prepare --root RUN --source-id SOURCE --item ITEM --beta-root BETA --instruction-file DIRECTION
backend/.venv/bin/python -m backend.pipeline.transcript_claim_migration register --root RUN --execute
backend/.venv/bin/python -m backend.pipeline.transcript_claim_migration prepare-unit --root RUN --unit 0 --execute
backend/.venv/bin/python -m backend.pipeline.transcript_claim_migration run-unit --root RUN --unit 0 --capacity SHARED_CAPACITY --execute
backend/.venv/bin/python -m backend.pipeline.transcript_claim_migration verify --root RUN --execute
```

正式執行前須完成適當測試與提交。隔離測試通過、已登記來源或已啟動 batch 均不等於
真實內容審閱、逐條資格及整批移植完成。
