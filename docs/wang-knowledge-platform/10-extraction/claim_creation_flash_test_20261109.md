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
