# Claim creation：分組生成完整候選，再交 Flash Update

> **讀者**：Solution architect、Developer。
> **類型**：規範。
> **狀態**：#723 已授權實作；分步候選與隔離 Flash 接續已實作，正式庫接收仍未授權／未實作；不取代既有 extraction 契約。
> **與代碼對齊**：2026-11-09 核對至 c9438256；候選入口為 claim_creation_flash_v4，精確 prompt SHA 綁定本次生成規則，既有 extraction 預設契約保留。
> **權威範圍**：首次 creation 的任務分工、完整候選出口及之後的 Flash 接續；不授予正式資料寫入、舊審閱資格或 CVP 維護權限。

## 目錄

- [1. 背景與目標](#1-背景與目標)
- [2. 名詞與責任](#2-名詞與責任)
- [3. Creation 的三個步驟](#3-creation-的三個步驟)
- [4. Creation 完成與 Flash 的接點](#4-creation-完成與-flash-的接點)
- [5. 復用、恢復與實作範圍](#5-復用恢復與實作範圍)
- [6. 驗收與設計自查](#6-驗收與設計自查)
- [7. 相關依據](#7-相關依據)
- [8. 執行入口](#8-執行入口)

## 1. 背景與目標

現有抽取已按小標題分章，但每章一次要求模型生成 Question、Position、Observation、Evidence、Claim、各類關係及逐句覆蓋記錄。出錯時可能把整包交回重寫。模型同時承擔太多不同任務，修一處可能改壞另一處。

本設計沿用 #702 的原則：**複雜工作拆成簡單、互不重複的任務；每件事做完整，完成結果直接傳遞。** 改善首次生成的品質、總耗時與總 tokens，不以減少調用次數或增加記錄數作目標。保留現有模型／訂閱選擇，不新增雙模型循環審閱、資料表或 scheduler。

**固定順序：分組 → Claim 候選與原文範圍 → 完整候選知識包 → Flash Update。** 前三步是 creation；第四步才按原文、Evidence／Observation、Claim 自下而上校正。不能拿 Flash 當成尚未完成的生成步驟。

## 2. 名詞與責任

| 名詞 | 本設計的含義 |
| --- | --- |
| Claim 候選 | 教授的獨立判斷，尚待完整材料生成及後續驗證，不代表已獲有效資格 |
| 原文範圍 | 段落 ID 的一組首尾範圍，指示結論、論據、限定、反方所在位置；可以不連續，不是 SourceFragment |
| Evidence／Observation | 保存教授的具體理由、經文觀察等材料；statement 忠實性與它如何用來論證 Claim 是不同責任 |
| SourceFragment | 逐字引文及其來源、位置、版本、可取得的媒體時間；模型選引文，程式編譯身份與定位，不猜音訊時間 |
| 完整候選知識包 | 本來源各組的候選節點、來源片段、引用與關係均已生成，通過結構及逐字校驗；不等於語義正確、覆蓋已驗證或已審閱 |

沿用現有模型：`EvidenceStepRecord` 同時存 statement、來源連接和論證欄位，不新拆 Evidence 實體。Claim 可同時作另一 Claim 的論據。只有一句支持性斷言、沒有教授提供的進一步支持，不自動提升成 Claim；另有經文、觀察、例子或推理支持才考慮獨立建卡，不設「至少兩條 Evidence」的數量門檻。灰色案例留具體候選理由，不強迫建卡。

## 3. Creation 的三個步驟

### 3.1 先分組

程式沿用來源投影、穩定段落 ID 與小標題產生初分；一個專注的 GPT 任務按原文及論證轉折調整。首次 creation 沒有既有知識圖，不能把 Flash 已有節點的 coupling 計算當作可用前提。

每段原文有唯一生成歸屬，防止漏段；相鄰組可帶重合的唯讀上下文，也可把後文返回同一論點的範圍歸同組。小標題不是硬邊界，緊密論證儘量同組；必要的跨組依賴保留。這種上下文重合不表示可以同源並行寫入。

組界按原文及合理 payload 定，不先猜 Claim 數。候選過多時可在同 topic 內按原文次序拆成較小生成任務，保留原組與來源範圍；交給 Flash 時再用它現有的 Claim 容量限制產生審閱小組。不得為湊十條而截斷原文，也不因候選變多重新跑整篇分組。

### 3.2 生成候選 Claim 與原文範圍

**第二、三步優先復用現有 creation code，只拆任務，不另寫抽取器。** 第二步沿用 `detailed_knowledge_extraction.py` 的 Claim 欄位、列舉與 anchor 結構，從既有 prompt 拆出 Claim 任務，只新增尚缺的相關原文範圍及支持線索。既有欄位不得換名或另造相似模型。

每次只做 Claim 識別：讀本組原文及必要上下文，輸出 statement、歸屬、相關經文、結論位置、完整相關範圍及簡短支持線索。線索指向教授不同於重述結論的實際論據；此時不生成正式 Evidence、Fragment 或整張關係圖。

範圍須含結論、各項論據、必要限定、反方和問答，不只圈 statement 所在一句。允許本組沒有 Claim，但仍有重要材料；這些原文繼續交第三步，不因沒有 Claim 就排除。

用程式分配穩定的候選別名，模型不編造正式 ID。前組已選候選以簡短 statement／位置索引供後組參考；發現同一來源的同一候選便引用既有別名，新增論據可追加範圍，不複製建卡。索引只幫助查重，不當作額外來源或既有批准。

### 3.3 完成候選知識包

第三步沿用現有 Evidence／Observation／Question／Position 的欄位與抽取規則、anchor 校驗和 Fragment 編譯；連接任務復用已有關係型別及編譯能力。優先由共用 schema 取本任務子集，新增必要的任務輸入／輸出契約及接線，不複製整份 schema、校驗器、模型 client 或包合併器。

本步按組處理共享材料，不對每條 Claim 各抽一套。材料傳輸按句數分批，v4 每批最多 24 句；保留原段落／句子 ID，輸入亦有完整 payload 上限，不能截斷資料。超長單段或超大分組須先走來源切片／計畫準備，目前不自動切斷教授段落。輸入是本組**全部歸屬原文**、Claim 候選與相關範圍、必要上下文及已有共享記錄索引。Claim 的範圍幫助聚焦，不能限制本組重要材料的生成分母。

分批的歸屬範圍只決定本次要生成哪些記錄，不截斷其支持來源。每筆新記錄至少一個 anchor 屬於本批原文；可同時從 reference_paragraphs 選取同組必要前後文。參考輸入包括候選相關範圍、鄰近段落及已有材料的來源，仍受完整 payload 上限約束。Prompt 要求逐條自檢 anchors 是否共同支持 statement 的每個實質部分；比較須保存雙方，短答須保存問題，「這個字」「不是」「沒有矛盾」須保存其明示對象，不只選代表性短句。先修生成指令及可見材料，不加一套事後全面重生成流程。

為保持任務單一，本步內分成兩種依序執行的任務；大組只按 payload 拆各自批次，不塞回一次大包：

| 任務 | 輸出 | 不重做的工作 |
| --- | --- | --- |
| 材料生成 | Evidence／Observation 的 statement、經文、歸屬及完整逐字引文；實際 Question、Position 與其來源；逐句處理記錄 | 不重抽 Claim，不判 argument_role 或整體 Claim 充分性，不編關係理由 |
| 論證連接 | 材料間、材料與 Claim、Claim 間的實際連接；完整端點、類型、方向、理由及所需來源；逐條 Observation 的 argument_role；直接答案／反方引用 | 不重寫材料 statement，不要求 Jev 或另一 GPT 重判剛完成的同一新連接 |

材料生成後先復用獨立的 anchor／身份編譯能力，再交連接任務；它因此使用已存在的候選端點。既有 many-to-many owner／support 雙向索引由程式從同一份模型連接決定同步，不能叫模型各填一份再對不上。各類關係按現有欄位及 collection 存放，不為統一輸出改資料模型。

Creation 與 Flash Update 共用內容品質規則：候選識別留意有獨立論據的解經原則；純轉場不獨立建材料；知識欄位不混入生成／審閱過程。連接理由只簡短描述教授的論證作用。這些要求放入各自既有任務，沒有新增模型覆審或資料刪除權限。

生成摘要時逐個動作保留原文主詞及對象；同行／同場不表示所有人物都具有每個動作或狀態，合併句子不能擴大主詞。這項要求在 Creation 生效，Update 的原有忠實性分類及修訂任務亦沿用，不另加一輪模型審閱。

候選編譯及 Update 共用 `claim_anchor_roles.source_roles`。Evidence 的 speaker／stance 描述其整體判斷，不能複製給全部支持片段。Anchor 只沿用片段自身角色事實或明確來源發言標籤；同段可有多個發言者。無法確定的角色／立場保持未定，不能由教授的總結反推其中問句是教授肯定。來源標籤只能證明誰發言，不能單憑標籤推定其採納立場。這是機械派生，不另發模型調用，也不改原話、來源定位或材料的歸屬判斷。

新生成的 anchor 無資料不預設角色；更新既有復現時，缺少新資料也不能反證已存的角色標記，因此保留既有註記，不追認為新判定。只有明確來源／片段事實支持的角色差異才糾正，避免用 unknown 覆蓋可能由人工確認的既有資料。

完整引文清單允許多段共同支持；逐條保存教授的經文出處及其承重內容，不以整段已引用或結論已存在代替。合理同義概括允許；口頭強調、未展開預告不獨立抽成材料。Question 和 Position 不消失，也不補教授沒回答的答案。

跨組共享記錄由首次生成組持有，後組引用它。相同引文可機械復用 Fragment，但相同引文不證明兩個 statement 相同；語義合併由模型在相關候選中判定。後組確需補前組連接時，只輸出該連接增量，不整組重生成；來源正文、身份與既有人工決定仍受保護。

現有 `validate_response`／`compile_package` 含完整圖的必填引用及對稱性檢查，不能把第二步半成品直接傳入，或為此放寬舊校驗。中間步驟只用適用的欄位／來源校驗；若既有邏輯未獨立，最小抽出共用 helper，讓舊入口也沿用。關係完成後組回既有完整 extraction response，執行完整驗證與 `compile_package`。

模型輸出逐句處理記錄，程式核對漏句、逐字位置及排除理由。這是生成自檢，不能宣稱已證明所有重要意思都有保存；真正反向查漏留到 Flash 的全來源任務，不另加一次全面語義審閱。

## 4. Creation 完成與 Flash 的接點

程式合併各組，核對來源版本、唯一身份、全部引文、端點存在、允許類型、引用／owner 對稱、組間引用及未完成範圍。模型不靠刪除原文重要內容湊校驗；不要求每句各建一條 Evidence，也不要求每條 Evidence 必須直接連 Claim。

有結構錯誤、無法定位的必要引文或未完成的生成任務時，candidate 尚未完成；只修該任務，不能先交 Flash 補做。完整候選包保存來源與組界、各次輸入／原答覆、編譯對照及校驗報告，才形成 `creation_complete` 的實際依據；這是流程結果，不新增正式審閱 status。

Flash 消費完整候選包，依既有規範先完成全來源原文／反向查漏，再完成 Evidence／Observation，最後完成 Claim。它檢查並修正已生成產物，不能先清空重生成。下層完成結果沿用，後層發現確切缺陷才交回相關項；程序驗過逐字定位不代表語義已驗過，creation 也不冒充 Flash 證明。

### 4.1 候選資料與正式庫的邊界

已核實 `reviewed_candidate_contract.validate_store_package_authorization` 會保護帶 extraction authority 的正式匯入。**不能刪掉 extraction metadata、偽造舊 consensus seal，或改 source_kind 繞過保護。** 完整候選與 Flash 完成結果也不自動取得舊 Update／Opus 資格。

`claim_flash_flow.run_candidate(candidate_path, root, ports)` 已實作：保留完整 extraction provenance，由 `claim_candidate_workspace.open_candidate` 建立專用 socket-only PostgreSQL，核對實際 data directory、禁用 TCP、空庫 bootstrap 與候選 SHA。它不接 database URL，不調 canonical ingest，不刪 metadata 或偽造審閱 seal。隔離資料保持 candidate，正常 Flash 交易及回讀在此庫內執行。

每次退出停止本次 PostgreSQL；資料目錄保留供精確接續，位置寫在 workspace/isolation.json，不留背景 daemon。正式庫的原匯入門檻不變。初始載入是明示的隔離候選 bootstrap，不是生產匯入能力或內容批准。

Flash 完成後正式庫接收的版本化 authority／資格標示仍為 **TBD**，本版只交付完整候選及隔離校正產物，不宣稱正式 end-to-end 入庫。直接文字 JSON 來源受支持；投影片 attestation 和母本原格式的 creation adapter 仍沿既有入口，不能以本版略過它們的校驗。

### 4.2 Flash 輸入、執行與輸出的最小整理

現有分層 Python 入口接 store、來源、組別、前層保存結果及模型 ports；`load_source` 直接查 PostgreSQL，多個修改／回讀接點也依賴 SQL。主 CLI 的預設 `run` 仍是較早的 lower＋Claim 組合，不能當作本設計最新逐層接續入口。保留各層判斷，整理薄的共同分層入口與資料讀寫接點，creation 與既有 refresh 共用，不複製驗證流程。

**輸入與輸出都要整理，不能只換讀取來源。** Creation 輸入是完整候選包與來源／組界；既有 refresh 輸入是精確綁定的來源資料。執行中保存每層的有效後像，讓後層讀到真正修正結果；不能只把各層 patch 套回初始 candidate，也不能忽略新增、退役、共享引用與跨層交回。

| 對外輸出 | 內容與用途 |
| --- | --- |
| 完整結果知識包 | 從本次精確後像編譯，沿用既有知識包資料模型；包含保留、修改及新增記錄，正確表達退役／替代與引用，不讓調用者自行拼各層 patch |
| 處理結果 | 全部目標及各層已完成／待接續／hold、具體未解決問題、下一個合法接續位置；有未完成項不能把完整快照說成校正通過 |
| 修改與追溯摘要 | 原 candidate、結果包與來源版本 SHA、實際增修退役 ID、各層結果／交易／回讀的保存引用，以及總耗時與 token；原始輸入和答覆不覆寫 |

由程式從已保存結果編譯完整輸出，不再調模型重寫。輸出明示語義驗證範圍、是否已提交及資格／authority；校正完成、正式庫提交、舊審閱資格是分開事實。正式庫 refresh 仍須保留真實交易／回讀，candidate 模式不能假裝已寫正式庫。候選載入／保存 adapter 已按 4.1 隔離；正式接收 authority 仍待解決，不以統一輸出放寬舊保護。

`claim_flash_flow.run` 是新的共用分層入口；既有 source refresh 明確傳 store／source_id，candidate 則經專用 wrapper。結果的 package 路徑／SHA 指向完整活躍後像，changes 記增修退役，pending／next_action 明示未完事項；原始 extraction 留在包內，另記 flash_application，不能冒用舊 seal。只增加薄的共同結果封裝及既有模型匯出，沿用各層原始產物，不另造事件系統或第二套狀態庫。驗收須包含多輪修改後輸出等於精確最終後像、未變內容保留、新增／退役及引用完整、來源漂移拒絕導出成功結果、有 pending 不誤報完成、重入零模型零重複提交。

Claim statement 的送審材料須保存既有 Observation → Evidence → Claim 路線：直接附件與上游下層材料分開表示，連接保留端點、類型及理由，不能把間接論據冒稱直接附件。只循同來源已有的下層入邊，不穿越其他 Claim，每筆記錄最多帶一次；下層節點沿用其完成證明，不重新審查。實際依據及連接加入本次版本讀集，缺少有效下層證明時在調用前拒絕。

## 5. 復用、恢復與實作範圍

| 現有能力 | 處理方式 |
| --- | --- |
| `extraction_sections.py`、來源投影 | 復用初分、原文座標和來源身份；新增專注的語義組界輸入，不重寫來源解析 |
| `detailed_knowledge_extraction.py`、既有 prompts | 第二步用 Claim 子集，第三步用材料／關係子集；共用欄位、列舉、來源及圖校驗，不複製第二套模型 |
| `detailed_knowledge_extraction_runner.py` | 第二、三步沿用模型 client、來源渲染、身份／Fragment 編譯、usage 和保存機制；最終組回既有 response 再編譯。只增加分步 prompt／schema、範圍及編排；舊入口／cache 不被靜默升級 |
| `knowledge_package_merge.py`、store 校驗 | 復用圖完整性與交易保護，不取消舊入庫審閱門檻 |
| Flash 各分層入口 | 接完整候選後沿用；不另寫第二套查漏、模型覆核或排程器 |

每個任務綁定來源版本、精確輸入、prompt／schema／模型和已耗帳目。重入復用相同輸入的已保存答覆；不完整調用先查事實，不換 root 重發。局部 schema／引文錯誤優先機械校準，不能機械決定語義合併；確需模型修正只送該任務材料和具體錯因，不回傳整講歷史。

預設同一來源按組序生成，不把共享寫入問題和本次拆任務綁成並行重構。GPT／Jev 沿現有共用容量、版本化輸入及有界恢復；本設計不增加模型配額。跨組實際變更只失效依賴該變更的結果，不在結尾全面重審。來源實質漂移時不強交舊候選，走既有來源變更處置。

實作前逐項列出第二、三步要呼叫的現有函式及最小擴充；已有能力先接線，只有耦合到完整圖的部分才抽共用 helper。對抽出的 helper 同時驗證舊入口行為不變，不做無關 refactor。

程式承擔機械約束，Agent 使用現有入口安排任務、分析停因並接續。不新增通用 Agent framework。實作順序是：先分組與 Claim 候選，再材料及連接／編譯，再解決候選載入接點，最後隔離串接 Flash。每步保留可核實產物，不以啟動成功代替完成。

## 6. 驗收與設計自查

| 場景 | 應驗證結果 |
| --- | --- |
| 已測第三講固定原文，首次 creation | 不提供舊修訂後語義答案；先生成完整候選，才啟動 Flash；生成與校正各自計時／記帳 |
| 兩 Claim 共用 Evidence、多段共同支持 | 材料不重複；全部片段、經文與用途保留；不要求一條材料證明整個 Claim |
| 同 topic 跨小標題、後文回返 | 論證不被硬切；重合上下文不重複建卡，跨組連接端點存在 |
| 沒抽到 Claim 的原文 | 仍生成其中重要材料；Flash 全來源查漏可發現漏掉的獨立判斷及經文 |
| 支持性斷言與獨立判斷灰區 | 不因能單獨反駁就必建 Claim；有具體另行論據者才提候選，人工 hold 不繞過 |
| Question／Position／否定／限定 | 原問、直接答案、反方歸屬及教授的「每次」「不是」等實質意思不丟失 |
| 已完成的新關係 | 類型、方向、理由傳到保存端，不額外安排同一配對的 Jev／GPT 決定 |
| 中途退出、重入、來源漂移、身份衝突 | 保存原帳與原答覆；零重複發送／寫入，漂移拒收，局部恢復不重跑前組 |
| 舊 extraction 及 Flash 回歸 | 舊 prompt／cache／審閱與 store authority 保持原契約，不增加批准、正式寫入或下游副作用 |

測試使用 `backend/.venv`，先以必要控制例和程式反例驗證，再用已測第三講的固定資料在隔離庫跑 creation → Flash。不為證明速度再跑舊 GPT／Opus 全流程；利用保存的歷史問題檢查重要錯誤／遺漏能否被發現和修好，粒度分歧不硬算漏抽。

報告 generation、Flash 及端到端 wall time，GPT／Jev input、output、快取與可取得實際費用；快取和 reasoning 子集不重複加總。區分等待、恢復與模型時間，不把少幾次調用或多建幾張卡當品質。尚未實跑不承諾一小時內完成。

原文編譯及包驗證以顯式 native_relations 路徑支持現有模型的 Observation → Claim、Claim → 材料、材料 → Question 等連接；舊預設限制不變。Observation 的論證角色由連接任務一次判定，不能在材料層先判、再因沒有路徑要求重寫材料；load_bearing 必須有真實的有限論證路徑，循環不能冒充支持。

自查已納入的主要缺口：首次分組無圖、候選範圍可能漏內容、沒有 Claim 的組、共享與跨組記錄、第三步再次過載、逐句命中不等於語義覆蓋、creation／Update 混淆，以及舊入庫審閱門檻。正式庫完成資格接點仍在 4.1 明列 TBD，不能隱藏為「直接沿用」。

## 7. 相關依據

- [#723 隔離生成／Flash 測試與經驗](claim_creation_flash_test_20261109.md)

- [#723 工作卡](https://github.com/junyang168/smart-answer/issues/723)
- [Claim Update Flash](../20-knowledge/claim_update_flash.md)
- [Claim data 分層驗證設計](../20-knowledge/claim_data_verification_design.md)
- [詳細知識抽取流程](detailed_knowledge_extraction_workflow_v1.md)
- [完整關係一次交付實測](../20-knowledge/claim_flash_completed_relations_20261109.md)

## 8. 執行入口

生成完整候選，之後在隔離庫執行 Flash（不指定 through-flash 則只生成）：

```bash
PYTHONPATH=. backend/.venv/bin/python -m backend.pipeline.claim_creation_flash \
  --source-id ID --source-file /absolute/path/source.json \
  --root /absolute/path/run --capacity-file /absolute/path/capacity.json \
  --through-flash
```

可用 `--groups` 提供原文範圍分組；它不是既有 Claim ID 分組。對已完整生成的候選，可獨立執行 `python -m backend.pipeline.claim_flash_flow --candidate /absolute/path/candidate.json --root /absolute/path/verification --capacity-file /absolute/path/capacity.json`。Candidate 模式拒絕 canonical `--apply` 或 database URL。原 SQL source refresh 使用 `--source-id`，並明確加 `--apply`；依原授權操作，不因新入口擴大範圍。
