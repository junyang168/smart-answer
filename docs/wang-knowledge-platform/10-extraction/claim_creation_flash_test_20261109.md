# #723 分組 Creation 與隔離 Flash 測試

> **讀者**：Solution architect、Developer。
> **類型**：記錄。
> **狀態**：2026-11-09 測試記錄，不是正式庫發布或資格批准。
> **與代碼對齊**：2026-11-09 核對至 9da69bb0。
> **權威範圍**：本次固定原文、保存答覆與隔離 SQL 的驗證結果；不推論整篇講道的穩定速度。

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
