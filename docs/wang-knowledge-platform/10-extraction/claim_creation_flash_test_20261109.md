# #723 分組 Creation 與隔離 Flash 測試

> **讀者**：Solution architect、Developer。
> **類型**：記錄。
> **狀態**：2026-11-09 測試記錄，不是正式庫發布或資格批准。
> **與代碼對齊**：2026-11-09 核對至 9da69bb0。
> **權威範圍**：本次固定原文、保存答覆與隔離 SQL 的驗證結果；不推論整篇講道的穩定速度。

## 目錄

- [1. 範圍與執行](#1-範圍與執行)
- [2. 結果](#2-結果)
- [3. 修正與經驗](#3-修正與經驗)
- [4. 邊界](#4-邊界)
- [5. 後續共用品質規則（1144a42f）](#5-後續共用品質規則1144a42f)
- [6. 使用者要求的 v4 全新生成測試（fffe6255）](#6-使用者要求的-v4-全新生成測試fffe6255)
- [7. Creation 防錯與完整 Update 接續（99ae359a）](#7-creation-防錯與完整-update-接續99ae359a)
- [8. 精簡逐句輸出的獨立試驗（13a36ef9）](#8-精簡逐句輸出的獨立試驗13a36ef9)
- [9. 整組共享材料及完整關係同次生成（f76e0e8d、614b15cd）](#9-整組共享材料及完整關係同次生成f76e0e8d614b15cd)
- [10. Claim 也合併的一次生成實驗](#10-claim-也合併的一次生成實驗)

- [11. 訂閱入口的 token cache 排列試驗](#11-訂閱入口的-token-cache-排列試驗)

## 1. 範圍與執行

使用 2018 NYSC《馬太福音釋經（七）》第三講已凍結的第一組 FG001：原始 S0001–S0110，共 2,729 字元，保留原段落座標。輸入來自保存的原文，不提供舊 Update 的修訂後語義答案。這是本組首次生成候選，再由 Flash 校正，不重跑舊 GPT／Opus 流程。

產物根目錄：`/Volumes/Jun SSD/codex-output/claim-creation-flash-723-20261109`。`creation-v2/candidate.json` 保存完整首次候選，`verification-v2/flash/result.json` 保存分層結果及精確 SQL 後像引用。GPT 使用 Sol medium 訂閱，Jev 使用既有連接；沒有 Opus、API 替換或正式資料寫入。

實測含工程修復：原六個生成答覆保留；Claim／材料中未變的答案按精確契約復用，只移除錯放於材料任務的 argument_role，交給完整連接任務判定。不是把舊 extraction 結果偽裝成新 prompt 的首次生成。v3 完整候選的常規入口由回歸測試覆蓋；v3 材料 prompt 另對本次四個真實來源缺口做了針對性模型測試。

## 2. 結果

| 項目 | 實際結果 |
| --- | --- |
| 首次完整候選 | 2 Claims、4 Evidence、19 Observations、7 Questions、3 Positions、45 Fragments |
| 短材料反例 | 四筆 fragment 清單由 2→4、1→2、1→4、3→6；statement 不變；補齊比較雙方、字詞指涉、原問句及否定對象 |
| Flash | 原文、反向查漏、Evidence／Observation、Claim statement、論證連接、Claim 關係與漏抽接續完成 |
| 漏抽 | 新建 `DK-3217083ff048c2c28583-CL003`，保存教授以字義／紙草用例支持的聖經無誤爭議判斷；只在隔離庫 |
| 最終包 | 3 Claims、4 Evidence、19 Observations、7 Questions、3 Positions、52 Fragments；完整原生關係及版本追溯另存 |
| 既有入口回歸 | pinned `backend/.venv`：626 passed，44.56 秒 |
| 完成核實 | 重入零模型；匯出逐筆等於實際 SQL 後像；最後逐字來源核對通過；隔離 PostgreSQL 已停止 |

模型帳目保留全部失敗、排錯及接續，不把它當成穩定版一次正常生成的成本：GPT input 518,288、output 33,748，合計 552,036 tokens；Jev input 103,506、output 14,058，合計 117,564 tokens。GPT 的 reasoning 4,234 是 output 子集，沒有重複加算；沒有可核實的美元費用。完整測試跨度包含工程處理與等待追加授權，不能拿來宣稱正常講道處理時間。

原測試上限及全部答覆保留；額外接續單獨記帳，最後四次 GPT 經使用者明確授權。未用更換 root 或刪除 started 記錄重置消耗。

## 3. 修正與經驗

1. **材料過短先修生成指令及可見材料。** 只要求模型「完整」不夠：比較必須有雙方，簡短回答必須有原問句與對象。批次只界定歸屬，reference_paragraphs 提供同組必要上下文；不新增事後全面重抽框架。
2. **材料抽取不判論證角色。** Observation 的 argument_role 由看得到完整連接的任務一次給出，避免先要求 load_bearing，後來才發現沒有路線。
3. **原生論證不改造成假 Evidence。** 顯式支援 Observation → Evidence → Claim 及合法直接連接；舊 extraction 預設要求不變，新入口明確傳入原生關係。
4. **資料存在不等於已送到模型。** Claim statement 首次送審漏了上游 Observation，誤報缺少字義來源。修正為保留實際下層路線及有效完成證明，不穿越其他 Claim，不重審下層。工程接續保留原異議與輸入，以改正後材料重新判定。
5. **上層完成後不重播過時前像。** 論證階段已消費 statement 結果，並核對改邊後的最終支持；中斷接續使用實際下游 SQL 證明，不能再以改邊前的讀集判斷一切失效。本次使用有界、明確綁定的工程接續，不新增通用恢復框架。

## 4. 邊界

這次測的是一組，不是整篇或批量效能驗收。來源直接文字 JSON 入口、完整候選、隔離校正與匯出已實作；投影片 attestation、原格式母本 adapter 及 Flash 完成後正式庫的 authority 接點仍按設計明列邊界。沒有偽造舊審閱 seal，也沒有把隔離完成說成正式庫已完成。

參見 [Creation 設計](claim_creation_flash_design.md)；原始 request、response、校準、reservation、SQL commit／readback 與最終驗收均保留於上述根目錄。

## 5. 後續共用品質規則（1144a42f）

使用者確認將生成品質要求同步到 Update。Creation v4 與 Flash Update 的既有任務共用內容文字、Claim 查漏及關係理由規則：保存有論據的解經原則；不把純轉場建成新材料；知識欄位不混入系統處理文字；關係理由簡短描述教授論證。Jev 忠實性題仍用原有選項，明確將摘要無據添加的處理說明交給既有 GPT 修訂任務。沒有新增模型角色、審閱輪次、欄位或自動刪除能力。

同範圍回歸 628 passed，44.82 秒，包含實際送審 prompt 接線、下層文字局部修訂且不動來源／owner、舊 prompt 階段不得靜默升級及既有完整新關係不重判。新增案例使用假模型測試流程契約，不作模型語義可靠性證明。本次沒有新增真實模型調用，也未改寫第 2 節隔離產物；不能把舊答覆稱為 v4 乾淨生成測試。

## 6. 使用者要求的 v4 全新生成測試（fffe6255）

使用者隨後指示「測試，評估生成效果」。在原產物根目錄的 `fresh-v4` 保存新的測試：同一份凍結原文、同一組界，六個生成任務全部重新調用，不輸入舊模型答覆或舊材料；先評估首次候選，不啟動 Flash 修訂。不是為恢復未知調用而換目錄，原測試及追加帳目完整保留。

生成用時 **473.021 秒（7 分 53 秒）**，GPT input **145,306**、output **22,441**，共 **167,747 tokens**。其中 reasoning 2,578 是 output 子集；cached input 為 0。沒有 Jev、Opus、啟動診斷、SQL 或正式資料寫入。本次未比較整篇吞吐量或美元費用。

| 對照項 | 上次首次候選 | v4 首次候選 |
| --- | --- | --- |
| 核心主張 | 2 條；解經原則在後續 Flash 才獨立補出 | 直接生成 3 條，包括解經原則 |
| 下層材料 | 4 Evidence、19 Observations | 7 Evidence、12 Observations；按實際內容重組，不以數量判品質 |
| 短片段問題 | 曾有比較雙方、字詞指涉、原問句／回答、舊說／否定缺上下文 | 相關材料已帶上述完整清單；純轉場沒有獨立建卡 |
| 處理過程文字 | 明確有 2 條 Observation、1 條 Question 寫「本組尚未……」 | 本次知識內容及 12 條關係理由未見此類文字 |
| 原文綁定 | 舊候選來源需後續補齊 | 首次候選的既有機械來源核對直接通過；不冒充語義完成 |

Codex 對照固定原文讀完 3 條 Claim、19 條下層表述、7 條 Question、12 條顯式關係及其支持片段，三個核心判斷都有據；沒有補教授未回答的神學結論。關係將蒲草紙出處確認標為 `contextualizes`，沒有再把它當作「兩事相隔一個月」的獨立語義證明。這是本次直接評估，不是額外模型的正式審閱批准。

仍有兩項發現，未改寫首次候選來掩蓋：

1. **生成文字的主詞歧義**：`P02-P02-O001` 寫「耶穌與門徒從伯大尼出來時餓了」，可能將飢餓主體擴大到門徒；S0086 只說「他們從伯大尼出來，耶穌餓了」。可明確寫成「他們從伯大尼出來時，耶穌餓了」。三條 Claim 的主張不受此歧義影響。
2. **既有程式派生的角色標籤問題**：CL0003 的 S0107、S0108 是聽眾問句，Claim anchor 卻有 `speaker=professor`、`assertive=true`。已核實 `claim_refresh.anchors` 將 Evidence 整體 speaker／stance 複製給全部支持片段，沒有區分作為上下文的聽眾發言。Evidence 的教授判斷本身正確，這不代表每個來源片段都由教授說出；需處理程式派生責任，不能靠模型重寫正確 Claim。

完整首次包 SHA：`ffaac65f9754721e6c56aaf813eda9273a838bb0185868aa9074704669556dc3`。`fresh-v4/summary.json`、`mechanical-source-verification.json`、`quality-evaluation.json` 保存時耗、機械核對及具體發現。此次結論是**生成品質改善、已形成完整候選，但仍有待校正項**；不宣稱 v4 全流程或正式庫資格完成。

## 7. Creation 防錯與完整 Update 接續（99ae359a）

使用者要求優先在 Creation 防錯，並明確要求測試同時涵蓋 Update。共用品質指令補入逐個動作保留主詞／對象，Jev 原有忠實性分類亦明列主詞與動作對象改變。沒有新增題型、模型角色或審閱輪。來源標籤則由共用 `claim_anchor_roles.source_roles` 機械派生，Creation 編譯與 Update 均使用，不繼承 Evidence 整體發言角色／採納立場。單一標籤也不證明教授採納每個片段；未知資料保持未定。

**Creation 單項實測。** 對 S0086 用一般材料生成任務重測，不提供錯誤摘要，新規則直接生成「第二天他們從伯大尼出來，耶穌餓了」，沒有擴大到門徒。結構、逐字引文、逐句清單均通過。此額外反例使用 23,509 tokens，不混入首次六個生成任務的數據，也不聲稱整組已用最新主詞規則重新生成。

**編譯器修復。** 保留第 6 節首次候選及六份原答覆，零模型重新投影角色：兩個聽眾問句正確標為 audience／非教授肯定，其他沒有片段級依據的採納標籤保持未定。14 個 anchor 的派生欄位發生變化，其他知識內容、片段、時間碼及來源完全不變。`fresh-v4/anchor-projection/projection.json` 綁定原包、改後包、允許欄位及固定版本。這是已證明編譯缺陷的重編譯，不是假裝舊答覆使用了新 prompt。

**完整 Update。** 以重編譯後的完整候選啟動全新隔離 SQL，執行原文查漏、Evidence／Observation、Claim statement、論證連接、Claim 關係及漏抽。第一次候選的主詞歧義刻意原樣保留，測 Update 實際能否修正，不預先填好答案。結果完成、無 pending，沒有增加或退役 Claim，三條 Claim 的 statement 全部不變，SourceFragments 也未改動。

| 階段 | 執行時間 | 實際總 tokens |
| --- | --- | --- |
| 首次 Creation | 473.021 秒 | GPT 167,747 |
| 完整 Update | 274.813 秒 | GPT 297,167；Jev 120,640 |
| 正常兩階段相加 | 747.834 秒，約 12 分 28 秒 | 585,554 |
| 額外 Creation 主詞反例 | 未獨立記錄完整階段時長 | GPT 23,509；加上本項共 609,063 |

以上時間是兩個執行階段相加，不包含中間修程式、單元測試與等待使用者的時間。不是同一版本無干預從頭一次跑完的宣稱。Update 有 12,416 cached input tokens，已在 input 中，不重複計數；全部無 Opus，沒有可核實的美元帳單。正常兩階段的 GPT 合計 464,914 tokens，Jev 另計；首次 Creation 與 Update 的成本分開可追溯。

實際內容改動只有：一筆 Evidence 的引用角色改為 quoted_source；兩條 Observation 的主詞表述修訂，其中真正的飢餓主體擴大已改成「他們從伯大尼出來，耶穌餓了」，另一條保守保留原文「他們」的指涉。另補一條下層 explains、兩條 Claim 關係（普通希臘文提供字義解讀背景；具體字義個案支持解經原則）。不把全部變更都算成嚴重內容錯誤。

**預檢限制。** Jev 對飢餓主體那條選 faithful／component supported，並沒有獨立抓出主詞歧義；它因另一個 component 疑點將該筆送到 GPT，GPT 按本任務的完整忠實性要求修正了主詞。這證明這次 Update 最終修對，但不能證明 Jev 已可靠攔截所有同類歧義。Creation 預防、Jev 分流及 GPT 處置的效果須分開評估，不能把最終修好算成 Jev 命中，也不因此新增全面模型複審。

驗收：678 項回歸通過（52.47 秒）；完成結果零模型重入；完整匯出逐筆等於 SQL；三條 Claim 主張不變；SourceFragments 不變；聽眾標籤正確；隔離 PostgreSQL 已停止；正式寫入為 0。最終包 SHA `88e922ef2f952beb4b0bf20ad991886e754025ce4fe3ccba8dab3f88135daade`，完整依據在 `fresh-v4/end-to-end-acceptance.json`。正式庫接收 authority 邊界仍按設計，不標舊審閱資格通過。

後續防回歸 c9438256：新增「新來源沒有角色資料時，不抹除可能由人工確認的既有復現註記」控制例。最終同範圍回歸 **679 passed，53.23 秒**。離線重編譯逐筆證明候選投影未改；已保存 Update 的論證階段沒有改 Claim occurrences，亦沒有新建 Claim，因此此保留分支不改本次模型輸入／後像。檢查保存於 `fresh-v4/existing-role-preservation-check.json`，沒有新模型調用，也不重播已完成內容階段。


## 8. 精簡逐句輸出的獨立試驗（13a36ef9）

同一份凍結原文及組界，不提供舊生成答案。此中間版只改逐句輸出：模型按材料列出覆蓋句子及排除理由，程式展開原有完整 ledger；仍沿用四批材料及獨立連接任務。三條候選生成 10 Evidence、9 Observation、7 Question、3 Position、45 Fragment；91 句清單完整通過既有校驗。材料數量變化不是改善品質的證據。

Creation 343.772 秒，對照第 7 節的 473.021 秒減少 27.3%；GPT input 143,557、output 15,940，總計 159,497 tokens，對照 167,747 減少 4.9%。Output 約少 29%，但不能把輸出縮減當成總成本同幅下降；不同次生成內容亦有隨機差異。

此中間版完整 Update **未完成**：第二條 Claim 已有第一條 Claim 的直接 supports 前提，第一條攜帶字義實例原文；程式卻只投影 Evidence 路徑，送審漏帶直接 Claim 前提的來源，GPT 因此報 needs_source。原 pending、答覆及帳目保留，不能算成生成真的缺來源，也不能以未完成的時間／用量與成功版本比端到端效率。已新增 source-local、單一 incoming Claim 跳數的唯讀來源投影與反例，不遞迴帶入第三層 Claim；不假定鄰卡已通過。

使用者其後要求第三步改成整組材料及完整關係同次生成。本節中間版存於 `compact-audit-trial/`，後續整組模式另保留自己的原始輸入與帳目，不重置本節試驗或將其追認為完成。


## 9. 整組共享材料及完整關係同次生成（f76e0e8d、614b15cd）

使用者指出 Creation 的材料與 Claim–Evidence 連接互相關聯，應同次生成。本次 `--grouped-package` 不再按 24 句切四批，也沒有後續獨立生成連接的模型任務；先識別三條候選及各自範圍，再把同一組的 91 句、候選及完整相關範圍交給一次材料／關係任務。完整請求 45,852 bytes，在既有上限內；只刪除傳輸中的空白段落，不改原文或位置。Sol medium 訂閱輸出上限 16,000 tokens，沒有要求模型填滿；此上限亦用於本次 Update，與第 7 節的 6,000 上限不同，實際輸出仍獨立記帳。

首次完整候選直接生成 3 Claim、6 Evidence、12 Observation、7 Question、3 Position、45 Fragment、20 knowledge relations、2 Claim relations。完整結構、逐字及 91 句 ledger 均通過。三項核心教導均保留，飢餓主體正確為耶穌，沒有將門徒一起寫為餓了；獨立解經原則、兩卷敘事比較、字義用例、普通希臘文背景、聽眾問答及教授引用的經文都在候選中。記錄少不等於省略，關係多亦不自動代表品質提高。

| 階段 | 第 7 節四批材料＋獨立連接 | 本次整組材料＋關係 |
| --- | --- | --- |
| Creation 執行時間 | 473.021 秒 | 242.034 秒（4 分 2 秒） |
| Creation GPT input／output | 145,306／22,441 | 44,625／11,726 |
| Creation GPT 總 tokens | 167,747 | 56,351 |
| 完整 Update 執行時間 | 274.813 秒 | 280.168 秒 |
| Update GPT 總 tokens | 297,167 | 335,237 |
| Update Jev 總 tokens | 120,640 | 152,350 |
| 兩階段執行時間合計 | 747.834 秒 | 522.202 秒（8 分 42 秒） |
| 兩階段全部 tokens | 585,554 | 543,938 |

Creation 時間減少 48.8%、GPT tokens 減少 66.4%；完整兩階段時間減少約 30.2%、總 tokens 減少約 7.1%。Update 的用量並沒有下降，不能將 Creation 單段節省誤報為總成本同幅改善。兩次內容有隨機變化、prompt／來源投影及輸出上限亦不同，本表是相同凍結原文上的實際比較，不是單一因素的嚴格實驗，也不是原始 legacy creation 的直接 benchmark。沒有可核實美元費用；cached input 24,960 及 reasoning output 2,035 已包含在本次總 tokens，不能再相加。

完整 Update 最後只有三項圖變更：普通希臘文與字義實例的 supports 改為反方向 contextualizes；乾燥環境對蒲草紙保存背景的 contextualizes 改為 explains；補一條觀察對第三條 Claim 的 supports。沒有新增／退役 Claim；所有 Claim、Evidence、Observation、Question、Position 及 SourceFragment 的正規化內容均不變。SQL 匯出補齊 schema defaults 不算來源內容變更。

**接續事實。** 首次執行在最後漏抽檢查拒收：模型使用提供的 `claims/ID` 字典鍵，parser 卻無條件再加 `claims/`。614b15cd 只接受精確已提供物件鍵或其裸 ID；未知鍵、其他 collection 及雙重前綴仍拒收。原答覆、錯誤、消耗及帳目保留。修復後從最後完成的關係階段接續原漏抽答覆，0 次新 GPT／Jev、0.372 秒完成；不把後階段已改的圖倒送前階段重新審閱。兩次只讀頂層恢復檢查分別被輸出上限 pin 及較早的 statement 讀集保護攔住，沒有模型調用或內容寫入；最終用既有末階段 consume 校驗、正式 missing 入口和同一 export 完成。此表排除工程暫停及只讀恢復診斷時間，不宣稱單一版本無干預一次跑完。

驗收：**702 tests passed，53.00 秒**；包括同次生成圖、答案及 owner 同步、未知端點、缺支持、load-bearing 缺路徑、舊模式、單跳 Claim 來源、精確 ID 格式與零調用重入。完成結果零模型回放通過、匯出逐筆等於實際隔離 SQL，PostgreSQL 已停止，正式資料寫入 0。完整依據在 `grouped-package-trial/completed-summary.json`、`exact-identity-recovery.json` 及 `normalized-end-to-end-acceptance.json`。最終包 SHA `72389c3343306ee6393b4c735742e852cc3a81a01302b4218970c4160b899412`。

本次經驗：驗證的任務拆分不能機械套用到 Creation。Claim 已先識別並圈定範圍後，共享材料、完整引文及它們如何連到 Claim 是同一個有邊界的生成任務；程式處理機械輸出與身份，不再要求下一個模型從頭重建同一張圖。完整候選後的分層 Update 仍保留，沒有用生成成功冒充語義驗證。

## 10. Claim 也合併的一次生成實驗

按使用者要求，0f6a3952 增加單組 `--single-pass` 對比模式；同一份凍結原文、組界、Sol medium 訂閱及 16,000 輸出上限，一次產生 Claim、完整相關範圍、共享材料、引文及全部關係。沒有提供前次 Claim 或舊修訂答案；原答覆與局部 Claim ID 機械投影分開保存，不改舊預設。

| 指標 | 第 9 節兩步生成 | 本次一次生成 |
| --- | --- | --- |
| Creation | 242.034 秒 | 284.055 秒 |
| Creation GPT input／output | 44,625／11,726 | 24,434／14,028 |
| Creation 總 tokens | 56,351 | 38,462 |
| Update | 280.168 秒 | 326.151 秒 |
| Update GPT tokens | 335,237 | 351,692 |
| Update Jev tokens | 152,350 | 149,193 |
| Creation＋Update | 522.202 秒（8 分 42 秒） | 610.235 秒（10 分 10 秒） |
| 全部 tokens | 543,938 | 539,347 |

一次生成的 Creation tokens 少約 31.7%，但 Creation 慢約 17.4%；兩階段合計慢約 16.9%，總 tokens 只少約 0.8%。Cached input 49,664、reasoning output 2,478 已包含，美元成本未知。只有一組樣本，模型延遲及粒度有隨機性，不作穩定因果結論；本次沒有理由取代兩步。使用者看完 Creation 時間後選擇保留兩步，已開始的 Update 完成後亦支持這個實務選擇。

首次包有 3 Claim、5 Evidence、11 Observation、11 Question、3 Position、49 Fragment、22 knowledge relations 及 1 Claim relation。三項核心判斷均有生成，Claim 較短；原本第一條 Claim 的「立刻可涵蓋一個月」這次只留在下層材料。Update 將其另建 CL004，連接支持、背景及兩個問題答案。這是層級／粒度差異，不是原文內容完全沒抽出，不能用多一張卡斷言更好。另新增 2 條既有節點連接、修改 3 條材料關係的類型／理由；原有 Claim、Evidence／Observation 文字及全部 Fragment 不變，Evidence／Question 寫入是 owner／答案同步。

獨立讀樣的限制：一次生成將「證明這麼講對不對？」另存為缺乏獨立語義的 Question；新 CL004 的 owner 只有確認文獻出處的 EV003，具體「一個月」的 OB007 仍未直接連到新卡。來源片段雖完整保留，這些粒度及論據連接仍是品質疑點；程式 completed 不等於排除全部語義問題。不為挽救未採用方案再增加模型循環或直接修改資料。

本次從生成到完整 Flash 在同一版本連續完成，無工程恢復、無 pending；零模型重入通過，匯出等於實際隔離 SQL，PostgreSQL 已停止，正式寫入 0。707 項回歸通過（54.43 秒）。依據在 `single-pass-trial/result.json` 及 `end-to-end-acceptance.json`；最終包 SHA `56c00f9737709c5a47c27c76153d9d5d2290f8fef3037dc720c61776324c02da`。選定方案仍是先識別 Claim 與範圍，再一次生成共享材料及關係，最後交 Flash。

## 11. 訂閱入口的 token cache 排列試驗

使用者要求測試將共同材料放前、當次任務放後能否改善快取。本次只做四次 Sol medium 訂閱調用，不執行 Flash、Jev 或資料庫寫入；沿用 #723 同一 worktree／既有 runtime。取第 9 節保存的真實 Claim 與完整材料，分別詢問支持與經文，移除舊 Jev 標籤及預期答案，避免暗示結果。兩種排列的解碼 JSON 完全相等，只改根及 job 字典欄位順序；system、schema、模型、6000 輸出上限相同。兩組均固定同一 cwd，控制環境差異；因此是受控重建，並非逐字重播原 production 請求。

| 排列／任務 | input tokens | cached tokens | output tokens | 耗時 | 提案 |
| --- | ---: | ---: | ---: | ---: | --- |
| 任務在前／支持 | 21,846 | 0 | 131 | 6.764 秒 | keep |
| 任務在前／經文 | 22,840 | 0 | 149 | 7.701 秒 | keep |
| 材料在前／支持 | 21,845 | 12,416 | 136 | 6.823 秒 | keep |
| 材料在前／經文 | 21,847 | 0 | 264 | 10.281 秒 | repair，建議收窄經文 |

原排列合計 14.465 秒、44,966 tokens；重排合計 17.104 秒、44,092 tokens。後組第一個請求命中，下一個本應復用共同材料的請求反而沒有命中，不能將第一個請求命中的 12,416 tokens 歸因於該組材料。usage 不揭示具體哪段命中，亦無法清空服務端快取或排除順序／機器路由影響。樣本只有四次，且未隨機重複；不能據此宣稱提高了穩定命中率或縮短延遲。經文判定 keep／repair 出現分歧，原答覆均保留、不寫入；這是語義相同輸入的生成差異，不能歸因於快取本身。兩組耗時亦受輸出長度差異影響。

實驗記錄曾遇到一次純機械保護：共用 request digest 對 JSON 鍵排序，忽略 wire 欄位順序；重排因此撞上控制組的 transport 身份，byte-bound invocation 在發出前拒收。保存原 marker、原 driver 及 `wire-identity-recovery.json`，不重做控制組。只在重排的模型外 metadata 綁實際序列化 SHA，區分傳輸；metadata 不進入模型。獨立核對四份實際 invocation、成功 exit 和輸出 SHA，證明實際只有四次，不把被攔 marker 當成新調用或重複計入舊 provider usage。

全部實際 input 88,378、output 680、合計 89,058 tokens，其中 cached input 12,416 已包含。四份輸出均符合 schema；stdout 均只有 agent_message，沒有工具調用，沒有資料庫 workspace／寫入，沒有 API 切換。資料存於 `token-cache-order-trial/result.json`，原始輸入、答覆、usage、exit receipts、語義等價與位元組身份恢復均保留。

結論：目前訂閱 CLI 未在此入口暴露 API 的顯式 cache breakpoint；本試驗沒有證明僅靠欄位重排能可靠利用共同材料快取，**不接入正常流程**。官方 API 的前綴／斷點機制不能直接當作目前訂閱入口已具備的控制；若未來繼續研究，先核實實際入口的控制能力，不加材料、反覆預熱或擅自轉 API。
