# Beta 逐字稿 → Claim 來源版本移植

**Migration 的目標是把舊 SourceFragment 對齊到新版逐字稿，讓讀者能回到對應段落和播放時間。** 每筆保留新舊版本與定位的關聯。是否能以新版原文支持 Evidence 或 Claim，由 Update stream 判斷。

> **讀者**：維護執行者、Developer。
> **類型**：流程。
> **狀態**：當前。
> **與代碼對齊**：定位入口及段落對齊核對到 `3ab906a3`。
> **權威範圍**：已獲授權的 Beta 來源凍結、來源版本交接及既有 Claim Update 接續；不授予內容批准、公開發布或 CVP／論證層批准。

完整的新稿三方合併、正式 editor 採用、發布及 Claim 交接見
[新逐字稿升級流程](./transcript_upgrade_workflow.md)。本文是其中來源／Claim 階段，不能代替前面的舊校對保留。

## 目錄

- [1. 純程式證據搬遷 dry-run](#1-純程式證據搬遷-dry-run)
- [2. 已發布版本的程式搬遷](#2-已發布版本的程式搬遷)
- [3. 來源與範圍](#3-來源與範圍)
- [4. 來源版本登記](#4-來源版本登記)
- [5. 另行啟動的內容審查與修訂](#5-另行啟動的內容審查與修訂)
- [6. 回讀與下游交接](#6-回讀與下游交接)

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

## 2. 已發布版本的程式搬遷

日常執行使用統一入口 `transcript_locator_migration`。每一篇／每次真實來源改版固定一個
RUN，不以新 RUN 規避失敗或重置舊模型任務。前提是新來源已按第 4 節正式登記，且與
已發布逐字稿的正文、段落及時間一致。此入口只搬遷定位；不發布逐字稿、不登記來源，
也不啟動 Claim refresh、CVP／Route 維護；預設不調模型。

```bash
backend/.venv/bin/python -m backend.pipeline.transcript_locator_migration prepare --root RUN --source-id SOURCE --old-source OLD_FILE --old-source PREVIOUS_BETA --published PUBLISHED_FILE --instruction-file DIRECTION
backend/.venv/bin/python -m backend.pipeline.transcript_locator_migration run --root RUN
backend/.venv/bin/python -m backend.pipeline.transcript_locator_migration verify --root RUN
```

### 2.1. 定位完成條件

| 結果 | Migration 保存什麼 |
| --- | --- |
| 段落與引文均確認 | 保存新版逐字切片、段落、播放時間和來源版本。跨段時按實際切片分開保存。 |
| 段落已確認，引文缺失或邊界未確認 | 保存新版段落、播放時間和版本，保留舊引文作歷史。標記引文待 Update。若新版只保留教授回答，可用它定位問答，並記下聽眾原話缺失。 |
| 對應段落仍有歧義或找不到 | 保留候選及具體原因，列為定位待核對。 |

`Evidence.statement`、Question 文字、同筆其他引文、新舊前後文和時間範圍都可幫助定位。舊版的講者標記和句界只作線索。候選段落須經確認才能保存。新版的講者或措辭有變化時，仍可完成段落對齊，並把內容差異交給 Update stream。

**定位例外清零表示預覽可進入提交。** 正式遷移在定位交易提交且回讀核對後完成。已定位而引文待查的記錄，會在 Update stream 核對逐字引文、講者歸屬、Evidence 是否支持 Claim，以及人工確認所影響的記錄。兩個階段各自報告完成狀態。

### 2.2. 執行與接續

需要模型定位時，對機械預覽的未定位項使用 `resolve --statement-first`，沿用共用容量與 Codex 訂閱。保存原始請求、答覆及校驗結果。若段落候選未涵蓋正確位置，依舊時間範圍及相鄰引文擴大搜尋。已核實的個案可透過版本綁定的校準檔重放。驗證可重複性時，區分使用相同原件重新執行，以及重放保存答覆後得到相同預覽。

既有來源影響評估可在 `prepare` 明示 `--jev-impact`，從環境讀取 `TYPESAFE_API_KEY`。
此選項在 SQL 寫入前，以每條 Evidence 的完整新舊自身片段、直接使用的 Claims 與新版
必要上下文判斷來源變動是否改變支持作用及摘要忠實度；不全面重審 Claim，也不裁定神學。
兩項均判未改變，且所有新版有效引用已由程式驗證段落、逐字切片及來源 SHA 時，才保留
原有 Evidence 資格；任何實質變動或不確定仍交現有 Update。原本未審定、拒絕或人工
hold 不藉遷移升級。此選項是獨立的來源影響評估。定位流程的完成條件與 Update stream 的內容判定仍依 §2.1。

定位、空白、保守標點及簡繁字形差異由程式處理，不以 embedding 相似度定位。
舊引用只有部分字被刪除而新版仍有內容時，保留候選，不能把整段教導標刪除。
啟用 Jev 時，可由已驗證的唯一前後文與來源順序綁定完整新版段落，再作完整 Evidence
影響判定；找不到、重複位置無法消歧或真正全刪除的引用不能由語意投票恢復。

請求固定 `jev-1.14.0`，每批最多六條且受輸入大小限制，不截斷片段。保存精確請求、
原答覆、來源／記錄 pins 及判定；提交與 Claim 定位交接重新驗證保存依據。已保存答覆
直接復用；已發送卻無答覆的 intent 不自動重發。模型信心值保留作診斷，不假稱已經過
本語料準確率校準。舊凍結 run 不追加此選項，不重寫原裁決或歷史。

1. `prepare` 只讀 SQL，凍結來源、原始指示、程式及 Evidence 寫集，產生 `preview.json`、
   `evidence/comparison.html` 和 CSV。已可靠定位、軟刪除、僅語境及已完成項分開列出。
   無法可靠定位的項目列入 `exceptions`，`run` 在任何寫入前停止。先由執行者核對新舊
   上下文、既有時間碼、經文及直接使用者，不把整批例外交給負責人。
2. 例外的技術定位結論沿用下述 `--calibration FILE` 契約；加此選項重新準備一份有明確
   版本關係的計畫，保留原 preview。不得重用其他講道的逐項定位決定，也不得修改原計畫
   使它看起來從未失敗。真正來源意思無法判定才交內容裁決。
3. `run` 先提交 Evidence，再從實際已提交結果準備 Claim anchors 並提交；兩次各有
   獨立 transaction、ChangeSet、歷史、版本比較及回讀。**不是跨兩階段的原子交易**。
   Claim 對照表在 Evidence 成功提交後產生，不能把第一份 dry-run 說成兩階段的完整預覽。
4. 若中途失敗，同一 `run --root RUN` 接續。若 Evidence 已提交但 Claim 尚未開始，先回讀
   Evidence，再準備 Claim；若已有 Claim 計畫，直接核對交易歷史及精確階段交接，不再拿
   Evidence 的舊 Claim 快照核對已同步的 Claim。若另有合法修改造成版本衝突，保留舊計畫，
   查明差異後再凍結新計畫，不覆蓋別人的修改。已提交而回執遺失也沿原 ChangeSet 恢復。
5. `result.json`／`result.html` 彙總兩次寫入、待內容 refresh 的 Evidence，並連結兩份
   詳細對照表。最終回讀包括當前 Claim 及 Evidence 階段之後所有未改依賴；來源、Claim
   論點及原有審核 provenance 保持原樣。`verify` 是顯式的當前再核查，不授予內容資格。

效率：只在首次準備做歷史版本與整篇順序對齊；相同 RUN 重跑復用固定計畫，不再搜尋位置。
使用共同批量 SQL reader，而非逐個物件／逐條 evidence 查詢。各階段 `apply` 內已有回讀，
統一入口不再立即額外跑一次相同 `verify`；Claim 階段的回讀同時覆蓋未改 Evidence。
兩階段交接一次比較保存 preimage 和精確交易後像，報告用連結復用大表，不複製全部引文。
`prepare-metrics.json`、`run-metrics.json` 保存首次階段耗時；重跑零寫入仍須檢查當前版本，
不能只信一個完成旗標。沿用現有完整讀集驗證，成本仍隨同源依賴數增長；尚未宣稱固定時間
或已消除底層所有重複讀取。不同 RUN 可處理不同講道，正式寫入仍使用共同維護鎖，
不可為追求並行速度跳過交易／來源門檻。

下列為兩個底層接點的獨立操作與資料契約；統一入口直接調用它們，不另寫儲存或批准流程。


負責人明確授權只搬遷引文與定位時，使用 `transcript_evidence_apply`，不啟動模型或恢復
已停止的 refresh。它讀取當前 SourceDocument、所有同源片段及反向依賴，逐一識別舊來源
版本。新來源必須與指定公開發布檔的有效正文、物理段落及時間完全相符；舊 preview 不直接
成為寫入權限。當前資料、原件、對照表、精確寫集與程式 SHA 都保存為不可變檔。

```bash
backend/.venv/bin/python -m backend.pipeline.transcript_evidence_apply prepare --root PREVIEW --source-id SOURCE --old-source OLD_FILE --old-source PREVIOUS_BETA --published PUBLISHED_FILE --instruction-file DIRECTION
backend/.venv/bin/python -m backend.pipeline.transcript_evidence_apply apply --root PREVIEW
backend/.venv/bin/python -m backend.pipeline.transcript_evidence_apply verify --root PREVIEW
```

限定寫入 SourceFragment 的逐字引文、來源版本／雜湊、段落與時間，以及相關 Evidence
的來源狀態、講者標記及撤回支持資格；不新建 Claim，不改 Claim 陳述、Evidence 陳述或
論證關係。已綁到新版本的片段保留；依負責人方向，純確認語也不能因先前綁定有效就
繼續作證據。原文找不到不表示已刪除。

精確字面對應可重綁；用字改變時，必須有兩側唯一上下文、對應的引文兩端、受限文字比例、
唯一的單段逐字切片及一致的既有時間碼，才搬遷新版引文。此類 Evidence 標記
`source_binding_status=needs_claim_refresh`、`support_eligibility=withheld_unreviewed`、
`valid_for_identity_review=false`，不能把位置可靠冒充意思等價或新的內容批准。
尚未確定的跨段、邊界、重複位置及未知來源不能直接套用。執行者可逐條核對完整新舊
上下文、時間及直接使用者，將技術定位結論保存在 `--calibration FILE` 指定的不可變
決定檔；每條綁定原片段 revision／SHA、目標正文 SHA、實際逐字切片與具體理由。
程序再獨立核查切片、唯一性及時間，不因技術定位而授予內容批准。

舊 SourceFragment 的音訊起訖若為空值，校準規則可附 `legacy_clock`，綁定 `item`、
`source_file`、`timeline_file`、`backup_manifest` 四項。後三項均為含檔案路徑及 SHA 的
檔案綁定；時間軸必須是該講道已驗證搬遷備份中的 `production/script/<item>.json`。
程式先驗證備份清單及時間軸 SHA、舊來源正文、段落身份、段落文字 SHA 和實際引文，再用
原 editor 的時間換算方法恢復舊段落區間。不覆蓋既有時間、不以新版時間推算舊時間，
也不把此步標成重新聽音訊。恢復出的時間仍只作上下文定位的附加依據。
這些原件會去重後加入搬遷計畫的檔案綁定，在正式寫入與回讀時再次核對；原件改動即拒絕。


跨段或同一舊片段被不同問題共用時，以確定的來源切片新建有決定性 ID 的 atomic
SourceFragment，各有真實段落時間；原片段保留歷史並標為已由精確片段替代。
Evidence、Question、Observation、Position 的來源引用完整重綁；需要依使用者區分
出現位置時逐一保存選擇，不任取一次。CVP／Route 的舊引用不在此獲得新資格。

「很重要」「有沒有聽懂？」「對不對？」等獨立強調或確認語，標 `anchor_state=context_only`。
完整片段只有「什麼意思？」「要怎麼樣？」「就是這樣。」等無獨立論斷的提示語時，同樣只保留語境；
含實質問題的句子不套用此規則。「你們」可能是所引經文的對象用詞，不當作通用口頭語排除。
Evidence 從有效 `source_fragment_ids` 移出它們，另存 `context_fragment_ids`，並撤回
支持資格；Question 的課堂語境仍保留。只做有限的完整字面規則，不因一段實質論述
含有「很重要」就移除整段。整條 Evidence 只剩這些話時標 `source_binding_status=context_only`。

唯一綁定的引文落在明確刪除線時，片段標 `anchor_state=source_deleted`，並保存
`deleted_in_source_sha256`；歷史引文與舊定位保留。Evidence 按所有／部分引用刪除標記
`source_deleted`／`partially_deleted`，同時撤回支持資格。不自動退休整條 Claim。

apply 與 Claim 執行者共用來源／實體執行鎖，不終止其他任務。Claim 可以在同一任務中
讀多篇來源：來源讀鎖可共存，同源 Update 保持原有次序，授權修改的 Claim／Evidence 等實體取寫鎖；逐字稿版本替換
與定位搬遷則取目標來源寫鎖，以及兩階段可能修改的實體鎖。鎖路徑按正式資料根與實體 ID
固定，不按 campaign 或模型容量目錄分開。無重疊工作可並行；真正相同的寫入不得重疊。

全域 recovery／CVP 鎖對這些工作只作共享維護閘門，仍排斥需要獨占的舊維護／CVP 流程。
舊執行中的獨占鎖不降級、不換 inode；它自然結束後才使用新契約。controller、worker 與
模型 transport 傳遞同一組檔案描述符，controller 結束不會令尚在執行的 child 提早釋鎖。
未知來源的舊程式呼叫保留原獨占方式，不能靠省略 source ID 取得較寬並行權限。

寫入前仍核對完整當前讀集，
共同 store 在同一 advisory-locked SQL transaction 再檢查逐項 revision、內容 SHA 與
退役狀態，然後走既有 ChangeSet、歷史及依賴失效機制。任一並行修改或原件漂移都停止，
不能覆寫舊對照表後繼續。已提交但回執遺失時，依同一 ChangeSet 回讀恢復，不重複寫入。
回讀核對實際更新、未改 Claim 與未改原件；不把搬遷稱為完整 Claim／CVP 更新。

Claim 的 `occurrences.anchors` 是另外保存的引文、段落與時間，Evidence 搬遷不會自動改到
這些重複資料。負責人另行授權後，使用以下接點同步。`--evidence-run` 指向已提交的上述
Evidence 搬遷，沿其實際 preimage 辨識舊引用；當前有效 Evidence／片段仍由 SQL 重讀。

```bash
backend/.venv/bin/python -m backend.pipeline.transcript_claim_anchor_sync prepare --root ANCHOR_PREVIEW --source-id SOURCE --evidence-run EVIDENCE_PREVIEW --published PUBLISHED_FILE --instruction-file DIRECTION
backend/.venv/bin/python -m backend.pipeline.transcript_claim_anchor_sync apply --root ANCHOR_PREVIEW
backend/.venv/bin/python -m backend.pipeline.transcript_claim_anchor_sync verify --root ANCHOR_PREVIEW
```

只改同源 occurrence 的 anchors 及不含舊引文的撤回原因／歷史雜湊。講道名稱作為舊
`source_id` 的格式須按已登記 SourceDocument aliases 辨識，不能漏掉或當成另一篇；
真正外源 occurrence 保持不動。已取代／退役 Claim 不改歷史。
每條定位須連到本 Claim 所有的 Evidence 及當前有效來源片段；原來的細引文仍能逐字
核對時保留，否則使用該 Evidence 已綁定的新版逐字切片，跨段則展開為各自的定位。
重疊定位標為同一物理證據基礎只計一次；同段內重複短答只標段落定位，不虛構逐字時間。
新用字的 highlight 維持 proposed，Claim 的判斷、講者／論據角色及舊審核 provenance
不改，也不新增審核事件或資格。已刪除或僅語境的引用移出有效 anchors，舊引文只在
SQL 歷史與私有對照表保留，不放回目前 Claim 供下游當證據使用。

技術校準後仍從實際來源切片計算講者，包含拆出的 atomic 片段；片段以自己的
`**聽眾：**` 標記開頭時，不繼承片段外上一個教授回合的身份。多個回合混合則保留多人對話，
不把同段教授重複聽眾的短答當作同一位講者。此標記不授予聽眾話語作教授主張證據的資格。

儲存層以 `wang_transcript_claim_anchor_sync_v1` 的精確 adapter 重新計算整份定位投影，
拒絕額外集合、Claim 文字或 annotation 變更、偽造 preimage、來源漂移及新的內容批准。
apply 同時驗證凍結 package、完整讀集 pins 與共同交易；不是沿用舊 AI 審核批准新內容。
已提交重跑只回讀；重新 prepare 若已全部同步則零 SQL 寫入。CVP／Route 的更新仍另列。

## 3. 來源與範圍

每次指定現有 `source_id`、確切講道 ID、Beta 根目錄、固定輸出 root 及原始負責人指示。
重跑沿用同一 root、清單、來源、policy 與普通 Update action，不換 root 重置預算。
來源真的再改版時另作新版本交接，保留前次結果與尚未完成項。

`prepare` 讀取已登記舊檔及其正文雜湊、完整 Beta rows/context 和反向依賴，
凍結全部同源 Claim，將已被取代者單列。排序依原先綁定的來源段落，只決定執行安排，
不證明新引文位置。保留物理段落 ID、空白／全刪除段落與小標題，從 sidecar 取得確切時間。
人工校對狀態不由此流程更改，也不因 AI 處理而冒充人工通過。

## 4. 來源版本登記

`register --execute` 使用共同 store 的 plan/apply、版本 CAS、ChangeSet 及歷史，
只更新該 SourceDocument 的來源版本與內部不可變快照位置。
舊公開逐字稿與 Beta 編輯檔均不覆寫。新快照是內部候選來源，不是公開發布或 Claim 內容批准。
原件變更、Beta 漂移或多餘寫集均拒絕。若交易已提交但回執丟失，按精確 intent、
ChangeSet 前後雜湊與當前行恢復回執，不重複寫入。

來源版本改變後舊 Claim 資格不得充作新版本通過。歷史 action、模型答覆、預算和人工作出的
決定均保留；不得將一個舊的未知呼叫重新發出。新版本維護須有這次的明確指示。

## 5. 另行啟動的內容審查與修訂

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

## 6. 回讀與下游交接

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
