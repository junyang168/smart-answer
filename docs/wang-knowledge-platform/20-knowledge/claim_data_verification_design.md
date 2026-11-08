# Claim data 分層驗證設計

> **讀者**：Developer、流程設計者
> **類型**：規範
> **狀態**：當前設計；不表示以下分工已全部實作
> **與代碼對齊**：2026-11-08 核對 `57991db5` 的 `knowledge_models.py` 型別欄位及 Flash 流程；開放擴充欄位尚未全庫清點
> **權威範圍**：Claim Update Flash 各元素的驗證責任與方法；不新增資料表、資格系統或 CVP 維護授權

## 1. 三層的邊界

| 層 | 負責保證什麼 | 不承擔什麼 |
| --- | --- | --- |
| 原文 | 每筆材料對應的 SourceFragment 清單完整；各片段內容、定位、版本及來源身分準確 | 不修訂 Evidence／Observation statement，不判定它如何論證 Claim |
| Evidence／Observation | 使用原文層交付的完整片段清單，核對 statement 忠實、完整表達教授的同一意思及歸屬 | 不維護來源定位、片段清單，不判定論證或 Claim 建卡 |
| Claim | 主張、全部論證步驟、證據用途與連接、Claim 間關係是否忠於教授的論證 | 不重新全面驗證下層；發現確切缺陷就退回負責層 |

Evidence 的核心是一個 statement；其原文依據由原文層管理。**EvidenceStep 是 Claim 層的論證步驟。**
「原文支持 Evidence statement」是表述忠實性；「Evidence 支持 Claim」是論證關係，不能混為一題。
原文層的清單完整性不是僅查 ID 是否存在；須查應共同掛載的片段是否漏掉。
Evidence 層發現缺據時，可以提出來源缺口，但由原文層補找、重綁並交回；找不到時才按完整材料處置 statement。
不靠刪除 statement 的實質內容掩蓋片段漏掛。

目前沒有獨立 `Evidence` 類別：實體 `EvidenceStepRecord` 同時存了 statement、來源連接及論證欄位。
本設計按**欄位與判斷責任**分層，不把整筆實體都歸中間層，也不聲稱資料表已拆開。
Evidence 與論證步驟正式分開表示的方法為 TBD；此次不為命名直接遷移 schema。

## 2. 原文層的元素與方法

| 元素／現有欄位 | 驗證問題及方法 |
| --- | --- |
| 來源身分：`source_id`, `source_type`, `project_id`, `transcript_id`, `canonical_source_id`, `title`, `source_url` | 程式核對註冊身分、實際原件及連結，不把編輯視圖另當教授來源；語義身分衝突另行處置 |
| 版本：`source_sha256`, `source_body_sha256`, `source_file_sha256`, `editorial_structure_sha256`, `locator_space`, `extraction_record_namespace` | 程式分開正文、實體檔案及編輯結構版本，核對現行 locator space；只改標題不等於教授內容變了 |
| 每筆材料的全部來源連接：`source_fragment_id`, `source_fragment_ids`, `citation_id`, `citation_ids` | 合併單數／複數來源引用去重，核對每項存在及所屬版本；Jev 以 statement、既有片段及同講原文查漏掛或錯掛候選，GPT 對完整候選決定完整清單。不得只保留最相似的一段 |
| 片段身分與逐字內容：`fragment_id`, `verbatim_excerpt`, `paragraph_key`, `paragraph_text_sha256`, `verbatim_excerpt_sha256`, `anchor_state` | 程式直接讀現版原件，查引文、段落及 hash；標點、空白、繁簡的可證明唯一對應可機械校準，保存原值與更正。不按 90% 相似度自動改字或選另一句 |
| 時間：`media_time`、歷史擴充 `media_end_time`, `source_segment_index` | 程式核對段落／segment 座標及時間範圍；未做音訊逐字對齊時不得宣稱已證明逐字時間準確，方法 TBD |
| 投影片：`source_visual_sha256`, `visual_source_assets`, `visual_sources`, `visual_source_attestations`, `source_modality`, `visual_locator`, `visual_block_sha256`, `visual_canonical_sha256`, `visual_renderer_version`, `visual_facts`, `visual_source_path`, `visual_source_file_sha256`, `visual_source_url` | 核對原資產、頁面／區塊與版本。已確認屬教授的 slide 與教授原話同等；使用者補充說明不得冒充教授內容。非文字視覺內容的忠實性判讀方法 TBD |
| 來源遷移及歷史：`source_binding_status`, `deleted_in_source_sha256`, `replacement_fragment_ids`, `split_from_fragment_id` | 先定位後判刪除；機械無法解決時沿 migration 的 Jev 新舊語義影響判定，必要時 GPT 處置。保存歷史，不任意復活刪除項 |
| 其他已見擴充：`source_path`, `source_text_sha256`, `editorial_topology_sha256`, `anchor_binding_sha256` | 核對檔案與來源版本的實際含義，不把不同 hash 名稱當同義；歷史各格式的完整映射 TBD |

教授實際引用或講解的經文須另列原文經文清單，核對承重內容、出處及對應片段；同一經文的重述可復用，但不能只因結論相同就視作經文已保存。
經文章節定位不能代替承重的引用內容，無法定位的引用保留待核對，不猜補節次；與材料表述的對應及論證用途分別交後兩層。
不要求經文每個分句都有自己的 statement。例如提前4:10，此處承重的是所有人的救主／更是信實忠心人的救主；已有此意及4:10出處時，不因缺少勞苦努力前半句而報遺漏。
只預告下一講而未實際使用的經文不算本次漏記；已講出的實質意思仍按實際範圍核對。獨立經文清點已作隔離唯讀試驗，尚未接正式入口。

**本層輸入**：當前來源、材料 statement 作查找目標、全部既有引用及必要舊版；Claim 只在來源變更影響分析所需時作參考，不在本層修改。
**出口**：有版本綁定的完整來源清單，或具體未解決範圍。單純逐字命中不能證明清單完整；沒有找出候選也不能證明教授未講過。
清單完整性與定位各有結果，不能一個通過代替另一個。Jev 查找沿既有 payload 分批覆蓋同講來源，容許多段共同支持；候選不是通過證明。
Citation 與直接 SourceFragment 綁定在新舊記錄的權威對應為 TBD；不臨時增加所有歷史記錄必有 citation 的新門檻。

## 3. Evidence／Observation 層的元素與方法

| 元素／現有欄位 | 驗證問題及方法 |
| --- | --- |
| Evidence `statement`（舊 alias `observation`）、Observation `statement` | 全部實質成分能否由原文層交付的完整清單支持？先以 GPT 僅看 statement 拆分，再由 Jev 分開核對拆分忠實性、各成分及整體忠實性；具體問題交 GPT 修正或有據保留 |
| 歸屬：`speaker`, `stance`；Observation 的實際發言者 | 分別由原文判斷誰說、是否採納／反對／引述／設問；隱藏待檢標籤後問有明確選項的 Jev 分類題。GPT 處置不符或不確定。原文與衍生欄位重複的歸屬判定只作一次 |
| 經文：材料的 `scripture_refs` | 所述經文是否對應原文內容，是否漏記影響表述的重要經文？程式規範化定位；Jev 預檢、GPT 處置語義歧義。它如何支持某 Claim 留到 Claim 層 |
| 觀察類型：`observation_type` | 是否忠實描述這項觀察的性質；類型詞彙及不同歷史格式的語義映射 TBD。不由深奧程度判斷是否為 Claim |
| 反向覆蓋：原文 → 既有 Evidence／Observation statements | 按共用分組，先以單獨 GPT 任務列出原文中應保存的重要概念、承重材料與必要限定，再用 Jev 逐項問是否已保存，不能以整段大部分已覆蓋代替，也不將無關經文分句、重複、口頭語或未展開預告列作必存項。疑點由 GPT 判斷漏記或已有承載。這與原文層「已有材料漏掛片段」分開；全講逐項方法已有隔離唯讀試驗；抽取完整性與對未修資料的召回仍待驗證 |
| Question 的 `question_id`, `text`（alias `question`）, `questioner`, `question_type` | 核對原問題、提問者及問題性質；不補教授未回答的答案。來源清單／定位屬原文層，`answer_state`／`answer_claim_ids` 屬 Claim 層 |
| Position 的 `position_id`, `title`, `attribution`, `corpus_scope` | 忠實保存被引述或反對的立場及其歸屬，不能把反方改成教授主張。與 Claim 的反駁關係留到 Claim 層 |

重要概念須單獨核對是否已有承載，例如「雙重國籍」不能由「給該撒的歸給該撒」自動替代。
同組共享完整上下文、記錄清單並一次發多題；逐項判斷不要求逐項 HTTP 調用，也不要求逐項建卡。

**本層輸入**：本項 statement、已驗證的完整自身來源清單及必要上下文。反向覆蓋另帶負責範圍的原文及既有材料清單；不帶整個 Claim 證據圖。
**允許修改**：本層 statement、歸屬、材料經文及必要新增／退役的材料；不得順手改原文引用、論證連接或新建 Claim。
缺原文就交原文層；新 Claim 線索交 Claim 層，兩者都不是本層有權作出的判決。
**出口**：忠實的材料，或具體缺口。合理同義改寫、口語及不改意思的表達不作錯誤；「很重要」「有沒有聽懂」不能獨立當實質 Evidence。

## 4. Claim 層的元素與方法

| 元素／現有欄位 | 唯一負責的檢查 |
| --- | --- |
| Claim `claim_id`, `statement`（alias `title`）, `claim_type`, `attribution`, `corpus_scope`；擴充 `limitations` | Claim 忠實性：Jev 對完整相關原文檢查主張是否保留對象、條件、範圍、否定、程度、因果及歸屬；GPT 處置具體失真。分類詞彙校準 TBD |
| Claim 整體支持 | 全部已驗證 Evidence／Observation 合起來是否支持 Claim 全部實質內容；獨立 Jev 問題。單條配對成立不等於整個 Claim 支持完整 |
| Claim `scripture_refs` | 教授用於此主張的經文及論證作用是否保留；獨立 Jev 問題，GPT 修正缺失或錯配。不要求將全部經文抄進 Claim statement |
| 論證步驟 EvidenceStep：`evidence_step_id`, `step_type`, `discourse_role`, `produced_claim_ids`, `claim_group_ids`；Observation `argument_role` | 核對教授如何從材料得到結論，前提、限定、反方回應及順序是否完整；GPT 以限定主題作單一論證結構任務。存儲中材料性類型與用途型標籤混用、步驟順序與多前提表示法 TBD，不能假造已有欄位 |
| Claim `evidence_step_ids`、材料→Claim 配對 | 程式查端點／引用；獨立 Jev call 檢查每對類型、方向及理由，允許只支持 Claim 一部分。GPT 補理由及處置歧義；不重審材料 statement |
| `KnowledgeRelationRecord` 的 `relation_id`, `from_id`, `to_id`, `relation_type`, `reason` | **所有論證連接均在本層**，包括 Evidence→Evidence、Observation→Evidence 及 Evidence／Observation→Claim；物理兩端都在材料表不會把論證責任移到 Evidence 層。Jev 分開問關係與理由，GPT 處置 |
| Claim 間連接：`claim_relation_id`, `from_id`, `to_id`, `relation_type`, `reason` | 與上一配對分開的 Jev call，核對直接論證關係及方向，不以共同 topic 當支持；跨講 Claim 只帶必要直接參考，不遞迴展開 |
| 漏接與漏步驟 | 程式全篇清點孤立材料及既有有效路徑；Jev 在同講 Claim 清單找候選，GPT 補理由並另查主題內非孤立漏接。既有充分支持不代表教授其他重要論據可刪；不作笛卡兒積配對 |
| Claim 承載／漏抽 | GPT 區分已有 Claim、必要新 Claim 與只需保留的材料。以教授實際主張及獨立論據判斷，不以有判斷句或固定 Evidence 數量決定；Jev 只提供候選，不作建卡判決 |
| Question `answer_state`（alias `answer_status`）, `answer_claim_ids` | 核對教授實際直接答案及後續結論的連接；保留正確舊答案，新建 Claim 時查本主題相關問題。空清單不必然錯：可能未回答，或答案不需獨立 Claim |
| 擴充 `opposed_position_ids` | 端點存在且教授確實針對該立場；不把被反對的說法當教授採納。語義仍走關係 task，不重判 Position statement |
| 禁止關係：`constraint_id`, `source_id`, `target_id`, `forbidden_relation_types`, `bidirectional`, `composition_role`, `reason` | 程式強制既有約束；GPT 不得以普通連接修訂覆蓋受保護決定。新增約束的詞彙、證據要求及與編輯用途的界線 TBD |
| 複現及高亮：擴充 `occurrences`, `anchors`, `evidence_id`, `observation_id`, `source_fragment_id`, `paragraph_key`, `media_time`, `media_end_time`, `speaker`, `stance`, `assertive`, `evidence_type`, `discourse_role`, `proposed_highlight`, `text`, `status` | 原文位置引用由原文層結果機械派生、核對；關聯用途由本層既有配對結果派生。不能再讓模型獨立猜相同角色／座標；歷史每種 anchor 格式映射 TBD |
| Topic 引用：`topic_ids`、擴充 `topic_terms` | 程式核對 topic 身分；本主張與 topic 的語義適配另列 metadata 任務，精度標準 TBD。不順手啟動跨講 topic discovery |

**本層輸入**：目標 Claim、已驗證下層材料、完整相關原文及任務所需直接連接；相關 Claim 只是參考，除非它本身是明確目標。
每個 task 只處置一類問題；Jev 同請求可共享材料回答多題，不等於 GPT 一次重審所有項目。
Jev 的不足／不確定是待處置線索，不是定罪；GPT 可根據完整材料推翻 Jev，但保留原結果及理由。
**出口**：主張忠實、支持完整、論證與連接各有明確處置，或逐項列出剩餘缺口。下層缺陷退回，不在本層悄悄修掉。

## 5. 共用機械責任與下游邊界

| 元素 | 方法／範圍 |
| --- | --- |
| 所有記錄 `schema_version`, `review_status`, `visibility`, `revision` 及 ID | 程式依現有 schema／政策查合法性、唯一性與引用；狀態不是內容正確的替代。`withheld_unreviewed` 不等於 Opus 已判證據錯誤 |
| `anchor_quality`, `support_eligibility`, `maturity` | 按實際已完成的來源、材料及用途判定推導既有狀態；不讓模型只為清狀態製造修改。全局材料可用性與特定 Claim 用途的精確映射 TBD |
| `extraction_fingerprints`, `record_namespace`, `parent_extraction_record_namespace` | 查來源追蹤及身分範圍；它們不證明內容或關係正確 |
| 擴充 `superseded_by`, `ai_adjudication`, `review_note`, `reviewed_at`, `reviewed_by`, `source_reconciliation_review_sha256` | 查繼任身分、歷史、實際決定與依據；不得把 AI 記錄當人工批准或改寫舊拒絕。受保護人工 hold 沿既有規則處置 |
| `KnowledgeRouteRecord` 的 `route_id`, `claim_id`, `route_type`, `target_id`, `decision_ids`, `canonical_topic_ids` | 查可解析性、當前身分及決定引用，記錄受影響下游；路由語義準入細節 TBD。本設計不生成 CVP／ArgumentRoute 或文章 |
| `TopicNodeRecord` 的 `topic_id`, `label`, `parent_topic_id`, `aliases`, `definition`, `legacy_ids` | 僅消費 registry 身分並查引用，不藉 refresh 改寫整個 topic registry；有歧義交既有身分處置 |
| 實際保存與未知擴充欄位 | 原子交易、版本及實際寫集核對、回讀、互相引用一致；保留未改欄位。模型 `extra="allow"`，尚未盤點的欄位標未覆蓋／TBD，不能宣稱全庫所有欄位均已驗證 |

## 6. 分組、復用與完成

沿用現有共用分組及已保存結果，不為這次責任澄清重新分組。小標題作軟線索，保持完整相關材料。
容許少量原文交疊：第一組讀 1–3 段，第二組讀 3–5 段不自動構成執行依賴。
同一 Evidence 的同一忠實性判定只做一次；兩個 Claim 使用同一段原文的不同論證判定是不同 task。
共享修改、實際上游結果依賴才需要順序或協調。當前 grouping 程式仍保守地把共同唯讀資源列作依賴；此處是設計方向，未聲稱已解除執行限制。

每項結果綁定目標、判斷種類、實際輸入與來源版本。只重做實質輸入改動所影響的項目；
新使用者、記錄版本單純增加或另一組讀相同段落不使舊內容判定失效。
片段清單變更後重新核對受影響 statement，再接續受影響 Claim 用途；不全講掃描或重審。
每組在正常提交／回讀時記錄完成結果，結尾只清點未完成項，不再要求所有 Claim 在最後一刻重新審過。

失敗具體區分機械缺陷、缺來源、語義未決、模型未知及資源限制；保留既有輸入／答覆與預算。
修程式不等於內容已完成；設計 TBD 也不能冒充通過。工程問題沿既有有界恢復處置，不新建資格／控制器或無限重試。

## 7. TBD 與驗證這份設計

尚待確定：Evidence／EvidenceStep 表示分離；多前提與有序論證步驟；歷史來源及 anchor 格式；
Citation 權威映射；視覺內容及逐字音訊定位；類型詞彙、Jev 校準；用途資格與全局狀態的對應；
下游路由準入；全庫開放擴充欄位清點。以上不以預設值假裝已解決。

設計評估須分別包含：逐字位置正確但漏一段支持、清單完整但 statement 加料、statement 正確但 Claim 連接反向、
單對有效但 Claim 整體支持不完整、多人共讀但不共寫、共享材料改動只影響部分判定等反例。
欄位名盤點只能證明命名覆蓋；語義方法仍須真實已知錯誤與安全反例驗證，不能以 schema 通過或模型一致代替。
