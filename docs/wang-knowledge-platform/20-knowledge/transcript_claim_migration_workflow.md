# Beta 逐字稿 → Claim 來源版本移植

> **讀者**：維護執行者、Developer。
> **類型**：流程。
> **狀態**：當前。
> **與代碼對齊**：2026-11-04，核對 `backend.pipeline.transcript_claim_migration`、`backend.pipeline.transcript_evidence_migration`、`backend.pipeline.transcript_evidence_alignment`、`backend.pipeline.transcript_evidence_calibration`；隔離 SQL 驗證與真實內容審閱分開記錄。
> **權威範圍**：已獲授權的 Beta 來源凍結、來源版本交接及既有 Claim Update 接續；不授予內容批准、公開發布或 CVP／論證層批准。

## 目錄

- [1. 純程式證據搬遷 dry-run](#1-純程式證據搬遷-dry-run)
- [2. 來源與範圍](#2-來源與範圍)
- [3. 來源版本登記](#3-來源版本登記)
- [4. 另行啟動的內容審查與修訂](#4-另行啟動的內容審查與修訂)
- [5. 回讀與下游交接](#5-回讀與下游交接)

## 1. 純程式證據搬遷 dry-run

先做來源定位，再由負責人另外啟動現有 Update／Claim refresh。這兩步不綁成同一次模型任務。
本節入口沒有 apply 模式、不呼叫模型、不寫入 SQL，只產生固定輸入、搬遷提案及對照表。
先前已完成的內容交易保留，不自動撤銷。停止 refresh 後，在同一 run 保存
`refresh-stopped.json`；`run-unit` 與 `resume-rejected-unit` 見到此記錄即拒絕呼叫。
既有 worker 須先查實際 process、保存答覆及 transaction，不能以停止檔代替中止中的 worker。

```bash
backend/.venv/bin/python -m backend.pipeline.transcript_evidence_migration --root RUN --output PREVIEW --align-sequence --calibrate-context
```

輸入沿用已凍結新舊原件、同源片段及目前正式記錄，包括此 run 已提交的新建物件；
不把模型提案或已預留 ID 當已寫入。輸出 `current-records.json`、`dry-run.json`、
可篩選的 `comparison.html` 及 `comparison.csv`，保留執行程式 SHA、來源 SHA 與每條當前版本。
相同輸入重跑產生相同結果；來源或目前記錄漂移時不覆寫舊 preview。
這是 dry-run 輸出版本，不另建模型 action、預算或重試 root。

`--align-sequence` 啟用第二版：先做原有的逐字／排版比對，再對例外建立一次整篇順序對齊。
候選須有前後唯一的相同上下文、受限的距離、字元重合及段落範圍；程式保留規則、
具體上下文位置、對齊範圍和差異，不以相似比例冒充意思等價。
另外不重跑對齊的字面核查會檢查上下文唯一性、新引文逐字切片、時間和明確刪除線位置。
程式 SHA 與核查結果一起保存。省略該選項則沿用第一版，舊輸出不覆寫。

`--calibrate-context` 是第三版補充，只處理第二版未可靠定位的項目，不能單獨省略
`--align-sequence` 使用。它交叉核對舊引用的音訊時間區間、新段落時間、唯一前後文，以及
原件內實際出現的書卷／章／節；經文辨識沿用共用 scripture parser，不查標準經文來補寫原話。
「音訊位置」指已保存時間碼，本階段未重聽、未重新轉錄，也不因時間接近就放行。

舊 `（問：）`／`（答：）` 等前綴可明確另存為 metadata；只有移除這些標記後的文字完全
對得上時才提出自動搬遷。短句局部搜尋只能在唯一前後文夾出的受限區間內、時間相符且
只有一個候選時使用；允許一字修正來找位置，但不以此自動批准改動的引文。
原引用有多個出現位置時保存所有候選，只有每次都指向同一新段落、刪除及講者結果一致時
才能按段落層級合併。不會任選第一次，也不以寬廣的舊時間區間裁定重複短答。

文字差異大時仍可在前後文和時間一致下定位來源段落，標示引文邊界待 refresh，不提出
自動替換文字。若舊引文明指唯一經節，在該候選段落中也只出現一次，則以那一節在來源裡
的實際子句縮小候選。例如同段先後提到五章十五節和十九節時，不能只取最早的相似句。
所有校準依據、實際來源切片、問答前綴及時間核查結果均隨輸出保存，供機械回查。

分類規則：

- 已綁定新版且引文、有效定位均可驗證：保留，列作已搬遷。
- 舊來源引文可驗證，原段落／上下文或足夠長的引文在新舊來源均唯一：提出新定位。
  比對可忽略限定的中文敘述標點、逗號與排版空白，保留英文字詞邊界、數字分隔、
  否定文字、經節冒號、小數點與數學符號。待綁引文逐字取自新版，不自行改寫文字。
  這證明文字對應，不證明標點改動前後語義相同，也不授予新的 Claim 資格。
- 唯一對應的那次引文全部或部分落在 `~~刪除線~~`：擬將片段的
  `anchor_state`、`source_binding_status` 標為 `source_deleted`，顯示「已經刪除」。
  保留歷史引文及原始定位，不將其冒充新來源的有效證據；不能因別處同一句被刪就標記本條。
- 一個 Evidence 的所有引用均如此失效，擬標為「已經刪除」；仍有其他引用則擬標為
  「部分證據已刪除」。不因一個片段失效就自動退休整條 Claim。
- 位置已由順序與唯一上下文定位，但引文用字、邊界或分段改變：列為「已定位／待 refresh」。
  列出新版逐字切片、各段時間、文字差異及否定／數字／原文詞變動，沒有自動寫入提案。
  跨段引文保留每個實際來源切片，不自行合成一個新的段落或來源錨點。
- 新版明確標為聽眾說的：只標記「聽眾」，不因講者不同而阻擋位置搬遷。
  Evidence 所連片段全部可確定歸屬時才提出整條講者標記；只有部分能確定時保留片段標記，
  不替其餘推定講者。這不改寫 Claim，也不判定聽眾的話可作教授主張的依據。
- 引文找不到、重複位置不能消歧、來源版本不明、對齊依據不足或視覺內容：
  列為「尚未可靠定位」，保留未確認候選供後續處理。找不到不等於已刪除。

時間直接取已凍結 Beta 的段落起訖，不宣稱逐字對齊。若日後實作 apply，須使用此精確
preview 的來源、記錄版本與實際寫集，走既有 transaction／ChangeSet／歷史及回讀；
未通過內容 refresh 的 Claim 不得因此取得新資格。生產消費端排除刪除證據、撤銷舊資格及
CVP／論證層接續須在真正 apply 前完成驗證；本 dry-run 不宣稱那些寫入或下游移植已完成。

## 2. 來源與範圍

每次指定現有 `source_id`、確切講道 ID、Beta 根目錄、固定輸出 root 及原始負責人指示。
重跑沿用同一 root、清單、來源、policy 與普通 Update action，不換 root 重置預算。
來源真的再改版時另作新版本交接，保留前次結果與尚未完成項。

`prepare` 讀取已登記舊檔及其正文雜湊、完整 Beta rows/context 和反向依賴，
凍結全部同源 Claim，將已被取代者單列。排序依原先綁定的來源段落，只決定執行安排，
不證明新引文位置。保留物理段落 ID、空白／全刪除段落與小標題，從 sidecar 取得確切時間。
人工校對狀態不由此流程更改，也不因 AI 處理而冒充人工通過。

## 3. 來源版本登記

`register --execute` 使用共同 store 的 plan/apply、版本 CAS、ChangeSet 及歷史，
只更新該 SourceDocument 的來源版本與內部不可變快照位置。
舊公開逐字稿與 Beta 編輯檔均不覆寫。新快照是內部候選來源，不是公開發布或 Claim 內容批准。
原件變更、Beta 漂移或多餘寫集均拒絕。若交易已提交但回執丟失，按精確 intent、
ChangeSet 前後雜湊與當前行恢復回執，不重複寫入。

來源版本改變後舊 Claim 資格不得充作新版本通過。歷史 action、模型答覆、預算和人工作出的
決定均保留；不得將一個舊的未知呼叫重新發出。新版本維護須有這次的明確指示。

## 4. 另行啟動的內容審查與修訂

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

## 5. 回讀與下游交接

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
新建清單消費本任務正式 SQL action 的已提交 `created_keys`，包含新 Claim 與支援物件；
模型提案不算已新建。新增 Claim 與原分母的陳述修改分開計數，缺少當前物件明示為缺口。
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
