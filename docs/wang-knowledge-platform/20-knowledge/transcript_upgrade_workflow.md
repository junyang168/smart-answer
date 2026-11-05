# 新逐字稿 → 合併舊校對 → 正式稿 → Claim 搬遷

> **讀者**：維護執行者、Developer。
> **類型**：流程。
> **狀態**：當前。
> **與代碼對齊**：2026-11-05，核對 `transcript_three_way_merge`、`transcript_review_promotion`、`transcript_claim_migration`、`transcript_locator_migration` 與 `ReviewStore`；通用接點的隔離驗收不代表新講道已完成 production 遷移。
> **權威範圍**：同一講道新版逐字稿的合併、editor 採用、發布交接及相關 Claim 來源搬遷；不授予內容批准、不自動開始 CVP／文章維護。

執行者收到「講道 ID + 新逐字稿」後，負責找到舊稿、校對前母稿、編輯歷史和相關 Claim，
完成下面所有階段；不把找檔案、技術定位或處理正常版本衝突的工作交給負責人。
單一任務目錄 UPGRADE 保存各階段輸入、結果和去向。**已合併、已採用、已發布、定位已搬遷、
Claim 內容已核實是不同狀態**，不能用最後一個 SQL 成功回應代表整件事完成。

## 目錄

- [1. 凍結輸入與辨識人工修改](#1-凍結輸入與辨識人工修改)
- [2. 三方合併及衝突處理](#2-三方合併及衝突處理)
- [3. 寫入新稿審核工作區](#3-寫入新稿審核工作區)
- [4. 正式 editor 採用與發布](#4-正式-editor-採用與發布)
- [5. 登記新來源版本](#5-登記新來源版本)
- [6. 搬遷 Evidence 和 Claim 定位](#6-搬遷-evidence-和-claim-定位)
- [7. Claim 內容與下游交接](#7-claim-內容與下游交接)
- [8. 效率、恢復與完成條件](#8-效率恢復與完成條件)

## 1. 凍結輸入與辨識人工修改

先確認講道 `item`、登記的 `source_id` 和原始音訊／視頻身份一致，不靠相似標題認定。
從既有系統取得三份原件：

| 原件 | 準確含義 | 用途 |
| --- | --- | --- |
| BASE | 舊稿被校對之前的確切祖先 | 找出既有編輯真正改了什麼 |
| EXISTING | 當前權威舊稿，包括已保存而未發布的人工修改 | 必須逐項交代去向的既有修改 |
| INCOMING | 新轉錄管線產生的新稿 | 新版段落、時間、slides 和新辨識結果的基礎 |

祖先從 `script_patched`、原始採用記錄、`legacy-baseline` 和不可變 revision 歷史交叉核實。
它必須是 EXISTING 的校對前祖先，**不能拿任意一個較舊的發布版代替**；SourceDocument
也可能已經含人工修改，不必然是 BASE。缺少祖先時先查歷史及備份，不能聲稱已完整找出
人工變更，或直接整篇用新稿覆蓋。編輯前後差異先稱「既有修改」；是否人工所改依作者及
實際歷史判斷，不能把 AI 差異標成人工決定。

INCOMING 先走現有轉錄候選、時間驗證和 editor import，在隔離的新稿工作區採用。
若收到單一 `script` JSON，先補齊既有管線所需的原始媒體、時間邊界及候選資料；不能替
缺少時間的內容捏造起訖，或跳過 `require_verified_timing`。工作區隔離不改講道身份。
三份輸入可為現有 rows 陣列或帶 `script` 的文件；合併輸入 INCOMING 使用該工作區實際
`ReviewStore.document(...).script`，使物理 IDs 與 editor 完全一致。

保存舊來源文件及 SourceDocument revision／SHA、兩邊編輯 revision／owner epoch、當前
發布檔、三份原件及原始指示。檔案 checksum 和歷史是備份／並行版本依據，不是內容批准。
現有尚未同步的本機修改須先恢復或備份；不能因伺服器稿已抓到就丟掉它。

## 2. 三方合併及衝突處理

```bash
backend/.venv/bin/python -m backend.pipeline.transcript_three_way_merge prepare --root UPGRADE/merge --item ITEM --baseline BASE --existing EXISTING --incoming INCOMING --instruction-file DIRECTION
```

`comparison.json`／`comparison.html` 逐項列出既有修改、新版位置、處理規則及未決項，
`merged-script.json` 是保留原格式的候選 rows。全部既有文字 delta 及軟刪除意圖都有一列，
包括「新稿已涵蓋」，不只列實際寫入的修改。小標題不參與口述對齊，保留新稿標題；slides、
段落 IDs、時間、段落其他 metadata 都來自 INCOMING，不拿舊段號去覆蓋。

| 狀況 | 處理 |
| --- | --- |
| 舊稿未修改 | 採用新稿，保留新辨識及分段 |
| 既有改字在新稿尚未涵蓋，且唯一上下文完全對應 | 將既有改字帶入新稿 |
| 新稿已包含同一校正 | 列作已涵蓋，不重複插入 |
| 既有軟刪除 | 搬遷刪除意圖；跨段時各段獨立包 `~~...~~`，不產生跨段未閉合標記 |
| 既有編輯直接刪掉文字 | 定位可靠時以軟刪除保留來源，舊版本／精確差異仍在歷史 |
| 新稿與舊校正同一處改法不同 | 列出兩邊及上下文，禁止任選一邊 |
| 重複短句、原文詞、跨段改寫、找不到文字或無法判定刪除邊界 | 執行者進一步核實，不能因找不到就當已刪除 |

自動規則只用唯一字面上下文，或共用 `literal_layout` 所允許的標點／空白正規化；
保留否定、數字、經節、希臘文／希伯來文等實質差異。另可由前後唯一、至少 12 字元且
距離受限的順序錨點夾定同一位置，但改字的兩端仍須精確匹配，待寫內容必須等於舊字或
已校正字，不能把中間任意改寫當成可搬遷。整段字面相似率不能當語意一致。
技術候選位置可由整篇順序對齊提出，候選不是已解決。新稿某段本來已軟刪除時，校正文字
不能使它重新變成有效內容；對部分刪除邊界不清的改字仍列衝突。

執行者依完整新舊上下文、已保存音訊位置、錄音及來源中真正出現的經文核對；這些不同於
拿一般聖經知識補寫教授沒說的話。大模型如需協助，只處理程式仍無法處理的有限衝突，
保存精確材料及結論，再經字面核查；不讓模型重寫全篇。真正來源意思／講者衝突才需要
負責人裁決，交付具體原文、時間和兩個可選解釋，不交一串無說明的段號。

已核實的決定存為 `RESOLUTIONS`：

```json
{
  "input_sha256": {"baseline": "SHA", "existing": "SHA", "incoming": "SHA"},
  "changes": {
    "TM-hunk-id": {
      "decision": "transfer",
      "target_span": [120, 122],
      "expected_incoming": "將在",
      "existing_span": [110, 112],
      "basis": "saved_audio_and_source_context",
      "reason": "已核對同一處原聲，保留舊校正"
    }
  }
}
```

span 為忽略小標題、去除 `~~` 標記後按口述行順序連接的字元位置，不含合成段落分隔字元。
`existing_span` 如需使用，必須覆蓋該項既有修改，替換文字直接取 EXISTING 的切片，
不是填入自由生成的文字。`keep_incoming` 另記 basis／reason，明示此項不搬遷的決定。
決定綁定三份輸入 SHA，不能套到其他版本或講道；未知 hunk ID 和不同原文字面切片會被拒絕。

用 `--resolutions RESOLUTIONS` 在同一 UPGRADE 下準備 `merge-resolved`，保留原 `merge` 的
未解決提案，不覆寫歷史。尚有 conflict 時禁止 apply。實際後續以已解決的 MERGE 路徑接續。
合併全程沒有 SQL／production 寫入，也不新增人工校對確認。

## 3. 寫入新稿審核工作區

```bash
backend/.venv/bin/python -m backend.pipeline.transcript_three_way_merge apply --root MERGE --review-root NEW_REVIEW_ROOT --actor ACTOR
```

只接受與 INCOMING 精確一致、已採用的新稿工作區，使用目前認領人的權限、owner epoch、
完整 version CAS 和 `ReviewStore.mutate` 一次寫入實際修改段落。段落的舊確認依既有 editor
規則清除；不冒充新的人工審核。歷史、操作 journal 和確定性 operation ID 沿用原機制。
提交成功但回應遺失時消費同一 receipt，不再次加 revision；實際後像或非目標 rows 不符就停止。

之後仍由現有 markdown editor 校對。後續新增的修改另成 revision，不回改原 merge 對照表。
原 merge receipt 只證明它寫入時的結果；不能拿它當後來版本已核對的證明。

## 4. 正式 editor 採用與發布

新稿工作區的 merge 寫入完成後，先做精確備份。來源工作區與正式資料均備份，驗證 checksum，
保留舊已發布逐字稿和完整 editor 歷史。這是一次來源升級的備份，不是在每個 Claim 前重做。

```bash
backend/.venv/bin/python -m backend.pipeline.transcript_review_promotion backup --beta-root NEW_REVIEW_ROOT --production-root PRODUCTION_ROOT --item ITEM --backup-root UPGRADE/backup
```

正式站尚未採用新版 editor 時，沿用原 `promote` 接點。正式站已是新版 editor 時，使用
`replace`，不能刪除舊 context／revision 後偽裝首次 import：

```bash
backend/.venv/bin/python -m backend.pipeline.transcript_review_promotion replace --beta-root NEW_REVIEW_ROOT --production-root PRODUCTION_ROOT --item ITEM --backup-root UPGRADE/backup --merge-run MERGE --actor ACTOR
```

`replace` 核對 EXISTING 就是正式站當前稿、來源工作區就是已解決 merge 的完整後像、
兩邊認領權限、備份和相同原始媒體。它透過既有 `ReviewStore.commit`／journal 建立正式站
下一個 revision，保存舊 parent chain。認領人／epoch 保持原正式站值；新段落與 slides
使用新稿資料，校對標記只沿已保存的新稿工作區記錄，不把舊段號的確認硬套到新段號。
舊基準檔保存在備份，新的 `script_patched` 使用新稿採用前基準，供下一次三方比較。

新 slides 以內容 checksum 驗證後加入，保留舊 assets 供歷史查閱；ID 碰撞但內容不同、原聲
身份不同、任何一邊新改稿或 owner 變更都拒絕。原發布檔不動。中斷後先由 editor journal
恢復，再依同一 replacement intent／operation ID 回讀，禁止覆蓋較後版本。

**發布仍走現有 editor 發布操作。** 同工完成需要的校對、時間和圖片處理後發布；
過去的人工校對不等於對新時間／新句子已確認。發布後驗證 public payload 與新發布快照
一致，軟刪除、editor-only 內容不出現在讀者和下游有效來源。此時才進來源與 Claim 搬遷。

## 5. 登記新來源版本

採用與發布不會自動改 SQL SourceDocument。保存正式發布檔，確認目前 draft 和其發布快照
完全一致；若還有未發布修改，不得拿 draft 冒充公開版本。正式發布檔可能附有 publisher
產生的圖片／SVG 包裝；口述搬遷只剝離版本化、圖片 SHA 相符、位於段首且分隔符完全相符的
`data-transcript-slide="v1"` 包裝，再逐字核對口述、物理 IDs 和時間。原發布檔及完整 SHA
保留，不將派生 OCR／SVG 宣告為已審核的視覺來源；裸 SVG、未知包裝或真正視覺 Evidence
仍須另有視覺來源 adapter，不能由此轉成口述證據。用既有來源交接凍結新舊來源及
完整同源 Claim 分母，不啟動模型：

```bash
backend/.venv/bin/python -m backend.pipeline.transcript_claim_migration prepare --root UPGRADE/source --source-id SOURCE --item ITEM --beta-root PRODUCTION_ROOT --instruction-file DIRECTION
backend/.venv/bin/python -m backend.pipeline.transcript_claim_migration register --root UPGRADE/source --execute
```

`--beta-root` 是既有參數名稱，此處指已採用合併稿的正式 editor 資料根。來源必須是本次
發布的段落／時間，保留已刪段落物理身份及小標題；機械來源投影排除軟刪除有效文字。
若來源本來已登記為同一版本，核實後沿用，不能為製造一次交易換 ID。若此來源沒有 Claim，
不強迫建立空 Update campaign；依一般 SourceDocument 登記接點處理，明示 Claim 分母為零。

只改同一 SourceDocument 的版本及不可變快照位置，保留 source ID。來源檔、已核實的
revision／SHA 或發布內容漂移就停止；不覆寫原公開稿、不建立重複來源或 Claim。

## 6. 搬遷 Evidence 和 Claim 定位

```bash
backend/.venv/bin/python -m backend.pipeline.transcript_locator_migration prepare --root UPGRADE/locators --source-id SOURCE --old-source UPGRADE/source/source-before.json --old-source OTHER_HISTORICAL_SOURCE --published PUBLISHED_FILE --instruction-file DIRECTION
backend/.venv/bin/python -m backend.pipeline.transcript_locator_migration run --root UPGRADE/locators
backend/.venv/bin/python -m backend.pipeline.transcript_locator_migration verify --root UPGRADE/locators
```

`--old-source` 可以重複，用來辨識相關 Evidence 實際仍綁定的各歷史來源；沒有額外歷史版本
就省略該組參數。不能只用最舊一份，漏掉已部分搬遷的記錄。細節與定位校準見
[已發布版本的程式搬遷](./transcript_claim_migration_workflow.md#2-已發布版本的程式搬遷)。

先重綁逐字切片、段落、時間、來源版本，並搬遷 Evidence、Question、Observation、Position
的相關引用，再同步 Claim `occurrences.anchors` 的重複原文／定位。保留 Claim 和 Evidence
既有 ID、論點及歷史；跨段拆精確來源片段，不把同一實體另建成另一條 Claim。
同源舊 source-name aliases 也必須處理，不能因與 registry ID 不同而漏掉。

軟刪除標為已刪除，撤回支持資格，移出 Claim 有效 anchors；歷史仍保留。
「很重要」「有沒有聽懂」等獨立口頭語只作 context。聽眾的話保存講者身份及課堂上下文，
不冒充教授斷言。定位已搬遷不表示舊 Claim 的論斷已由新版證據支持。

## 7. Claim 內容與下游交接

在 SQL 更新前後，所有相關 Claim 都必須有去向：保留／定位已換／內容待核實／已替代／
已退休／真正內容爭議，不能只數寫入幾條。定位層的結果不能把 `needs_claim_refresh` 或
`withheld_unreviewed` 改成通過。新來源刪掉核心證據或改變講者、否定、經節、條件時，
Claim 的陳述也可能需要修改或退休，不能只清掉 anchors 而留下有資格的無依據論點。

需要內容維護的使用既有 Claim Management Update，同一來源按段落順序、小組處理；
[既有來源移植接點](./transcript_claim_migration_workflow.md#5-另行啟動的內容審查與修訂)
的 `prepare-unit`／`run-unit` 接普通 action、原 policy、容量和持久呼叫帳。
該接點保守地固定整篇 live Claim 分母；不能為了省時間私自裁掉它已綁定的成員。
若另有已核實且已授權的精確受影響清單，可走現有 `claim_management_intake`；
它必須包含共享 Evidence 的直接使用者及需要維護的同源 Questions，不能只按有沒有改字篩。

全篇不重建 Claim；沒有變更需要的論點不重寫。模型只處理確實需要內容核實的範圍，
但凡 qualification 綁定整篇新來源、現有規則要求完整 own-source review，就保留該門檻，
不以「字面沒變」假造 current qualification。模型復核結果和實際 SQL 回讀分開記錄。
重新定位、舊 content review、新 source review 三者不可混用。

CVP／Route、觀點關係及文章依賴另外列出受影響清單。由各自的既有維護流程接收新版 Claim
及來源；沒有完成時明示尚未完成，不因 Claim 搬完就自動更新文章、重寫觀點或部署網站。

## 8. 效率、恢復與完成條件

正常路徑先做純程式辨識：每對版本各做一次順序對齊，快取唯一上下文搜尋，整批產生文字及
刪除意圖對照。重跑已固定的 merge 計畫只驗證 SHA，不重新對齊；定位流程同樣復用固定
計畫，批量讀 SQL，避免每條 Evidence／Claim 讀一次來源或呼叫模型。備份只在本次稿件
升級時建立一次，不在每階段複製媒體或整庫。兩邊相同 checksum 的媒體在備份內去重，
只對備份副本建立 hardlink，絕不將備份 hardlink 到可變的 live 檔案。`metrics.json` 和定位階段 metrics 保存耗時，
大模型及人工核實時間另記；小型合成測試不能代替實際講道耗時。

快不等於猜測：三方文字差異大時，程式可識別未決並產生上下文，但不承諾全部自動解決。
執行者先消化例外，已有精確版本決定則直接重用，不重問負責人或重新呼叫模型。
所有重試沿同一 UPGRADE 的原 stage、intent 和 action；來源真的更版或明確決定改變時，
新增有前後關係的不可變計畫版本，保留舊失敗，不能靠新 root 取得新模型預算。

跨 editor 檔案、發布檔及 SQL 沒有一個大 transaction。每階段用自己的 journal／ChangeSet
完成，再核實精確交接；某一階段失敗只停止未完成部分，不盲目回滾同工後來修改。
若已發布但 Claim 搬遷未完成，舊來源資格不可用作新版通過；完成清單明示這段缺口。

本次任務交付同一目錄的四份對照：

| 對照 | 必須證明什麼 |
| --- | --- |
| 三方合併表 | 每項既有校正／刪除已帶入、已涵蓋或有具體決定，零不明遺漏 |
| editor／發布交接 | 原認領與歷史保留，新稿逐字／時間／slides 正確，公眾看不到軟刪除 |
| Evidence／Claim 定位表 | 同源分母、舊新原文、來源版本、段落時間、已刪／context 去向完整 |
| Claim 與下游狀態 | 論點保留或改動、內容核實／qualification、CVP／Route 未完成項明列 |

只有自己實際完成的範圍標為 complete。發布和定位完成但 Claim 內容仍待核實，須直接說明；
資料庫已提交但回讀或必要內容核實未完成，也不標整個 upgrade 完成。
