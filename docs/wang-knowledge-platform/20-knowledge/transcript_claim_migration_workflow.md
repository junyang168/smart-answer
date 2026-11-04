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

`run-unit` 在模型呼叫前，將固定分母、共用容量及這組確切 action 登記到現有
Claim refresh 管理頁；保留其他 campaign。每次返回讀取原 SQL action、呼叫帳、
資格與未完原因，不把 batch 函式的空返回值當作完成。管理頁只觀察，不負責恢復。
已有正式 successor 時，沿 SQL `continued_by` 鏈接續，驗證 predecessor、固定來源／組員、
原執行目錄及無循環；保留原請求，僅將 SQL 已接受的同一 head 編成 batch 輸入。
這不建立新 action、不補模型額度，也不以新 root 重跑舊工作。

既有 Claim 先經完整自身來源與證據審閱；需修改時由既有 GPT／Opus 有界流程處理。
負責人不逐條核對；執行者處理材料、程式、普通模型異議及合法接續，只有原文確實不能
裁定的內容才交負責人。真正人工 hold 保留；舊 AI 抽查標記依既有精確投影規則核實。

舊來源的 AI 原文疑問可在實際新來源登記後，使用
`wang_claim_source_epoch_reassessment_v1` 重新進入同一 Update 審查。
程序核對原 AI provenance、完整事件歷史、歷史版本 SHA、已提交來源 ChangeSet、
新舊原件、完整目標分母及原始指示；任何人工事件、來源漂移、只有時間／metadata 改動、
非指定 Claim 均拒絕。舊疑問保留為正式 review question。
這只將待審候選交給獨立審閱，不是放行舊疑問、不增加舊 action 預算，也不授予新批准。
若先前只有零呼叫、零 action 的輸入預檢失敗，保留原 prepared 回執，另存同一任務的
一次 preflight repair 接續；已產生 action 或呼叫則必須走原正式恢復契約。

軟刪除不進有效來源：來源投影與精確引文校驗沿用共同 `source_projection`，
Claim 摘要也不得重新引入只有已刪文字才支持的教導。另有未刪除教授原話支持同一判斷時，
須重新明確綁定該證據。聽眾的問題／插話只作語境，不能直接冒充教授斷言。
經文、否定、條件、反方、論證步驟及自身證據充分性均由既有完整審閱處理。

新單元明確啟用 `include_question_evidence`：與目標直接相連的同源 Question 及其字面
來源片段一起取得內容權限，列入既有 required maintenance 分母。不能只修 Claim 的
Evidence 而留下問題的舊引用。被保護、缺失或跨來源的問題依據不自動取得權限。
已準備的舊單元保留原 intent；它留下的上下文問題以既有正式 context maintenance
接續處理，不能改舊權限或把先前未執行的資料維護冒充已完成。

小組按來源順序推進。程序停止後由執行者查保存答覆、實際 worker、SQL checkpoint 與
原剩餘預算，依既有恢復接點續跑；不清空舊錯誤或盲重送。後組修改共享材料時核對前組資格。

明確的 returned-rejected 答覆可用 `resume-rejected-unit --execute`，先取得原 controller、
維護、registry、同源及 worker 鎖，驗證確切 action 與固定組員，再交既有
`resume_rejected_output` 認證原材料、答覆、錯誤和剩餘額度。此步零模型呼叫、零新增預算；
成功後仍以原 `run-unit` 執行普通修訂與獨立審閱。未知呼叫、人工疑問及預算不足不由此解除。
權限可達性核對採共同只讀 footprint；真實寫入、共享使用者及 transaction pins 仍另行
檢查。已通過的答覆若停在程式提交門檻，修復後以既有 returned-review recovery 核驗
並接續原答覆，保留原始 SHA、模型消耗及實際寫集，不為純程式修復重送內容審查。

## 4. 回讀與下游交接

`verify --execute` 消費現有當前資格，另外核對實際有效正文中的引用，不建立第二套批准。
原分母每條都有保留、更新、替代、退役或未完成去向；新建項另列。
程序沿已登記接續取得原目標及新建項，退休項須通過既有 completed action 的
Opus retirement verdict 與精確當前回讀；缺失／未證明退休仍是不完整。
引文核對沿用共同只讀自身證據 footprint，不把鄰居私有片段誤算成本條依據。
所有組都真實完成後才可得到 `complete=true`，且 `completion_scope` 明示只涵蓋
Claims 與自身證據。尚未核實未用的來源支援記錄、獨立來源覆蓋、CVP／論證連結及讀者消費端時，
不得宣稱整個 migration 完成。`cvp_route_migration_complete=false` 明確保留該下游邊界。

`comparison --execute` 使用固定 before 與共同批量 current reader 產生
`comparison.json`／`comparison.html`，列出原 Claim 陳述、目前陳述、欄位差異及同源相關資料
的精確變更。它不呼叫模型、不改 SQL、不判定意思等價，也不產生新資格。
若尚無正式 verification，表中不假造通過或新建項完整清單；若已有 verification，
只顯示保存時點的核實結果，不能當成永久當前資格。

CLI 接點：

```bash
backend/.venv/bin/python -m backend.pipeline.transcript_claim_migration prepare --root RUN --source-id SOURCE --item ITEM --beta-root BETA --instruction-file DIRECTION
backend/.venv/bin/python -m backend.pipeline.transcript_claim_migration register --root RUN --execute
backend/.venv/bin/python -m backend.pipeline.transcript_claim_migration prepare-unit --root RUN --unit 0 --execute
backend/.venv/bin/python -m backend.pipeline.transcript_claim_migration run-unit --root RUN --unit 0 --capacity SHARED_CAPACITY --execute
backend/.venv/bin/python -m backend.pipeline.transcript_claim_migration verify --root RUN --execute
backend/.venv/bin/python -m backend.pipeline.transcript_claim_migration comparison --root RUN --execute
```

正式執行前須完成適當測試與提交。隔離測試通過、已登記來源或已啟動 batch 均不等於
真實內容審閱、逐條資格及整批移植完成。
