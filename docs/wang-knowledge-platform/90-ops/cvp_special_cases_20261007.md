# CVP 特殊案例索引 — 2026-10-07

> **讀者**：流程負責人、Solution architect、Developer。
> **類型**：記錄。
> **狀態**：歷史案例索引；不作生產狀態面板，不提供新的修訂授權。
> **與代碼對齊**：2026-10-07 核對到 `4d165bd` 的相應分流、政策及測試；案例結果整理自 #357 已保存交接紀錄，本次未重跑模型、SQL 或審計。
> **權威範圍**：說明正常 Claims → CVP 路徑曾在哪些地方不足，以及它們對應到什麼流程分支。當前執行方式見 [Claims → CVP 現行完整流程](../20-knowledge/cvp_production_process_v1.md)，不從一次歷史成功推導通用許可。

## 1. 內容與處置案例

以下僅記類型與處理結果，不把教授原文、模型全文或完整資料包複製進 Git。原始證據保留在 staging 的各次不可變產物中。

| 案例 | 正常路徑遇到什麼 | 實際處理與不能誤解的地方 |
| --- | --- | --- |
| g015：歷史 duplicate merge | recipient 當前證據與初始審核 input 不同 | 補證完整批次 review、donor 裁決及原 initial applied package；不是把 sealed final 當免檢證書 |
| g015：請求格式／审核格式 | 初始 proposal、reviewer 請求超限；其後 Opus pass 同時有 finding code | 分別完成無損 compact 和一次嚴格格式修正；已保存 proposal／consolidation 復用，格式修正不改兩項實質纠正意见 |
| g015：錯誤等價關係 | 完整組審計的連接異議經 GPT 認可；後續改 supports 的精確审核仍被拒 | 不能審到 pass。另獲 owner 明示授權，退役該錯誤連接並保存 observation-only 處置，再做完整組審計；沒有授權未來自動退役 |
| g031／g032：來源身份與 anchor 歷史 | 註冊 transcript 身份不同；另有已批准 anchor 刪除未被 bridge 消費 | 分別驗唯一來源身份、已審 excluded anchors 與完整 applied package；不能放行任意 alias 或任意刪除 |
| g033：觀點文字與關係 | 審計異議涉及 CVP 身份表述，超出等價改 supports 權限 | owner 限定 CVP、兩連接、三關係修訂。後續一次機械存儲規則誤讀與一次真實關係分歧分別處理，沒有把任一拒絕改標 pass |
| g034：來源有主題但證據有 hold | 字面主題可見，經節／引用資格仍有歧義；調查模型不一致 | 主題支持診斷沒有解除 Evidence hold；只按後來明示批准的成分 supports 後像執行及完整審計 |
| g035／g037：Gemini content-blocked | 受影響項沒有獨立語義判讀 | 按真實 provider receipt 觸發 GPT 訂閱補充判讀；例外組保留 revisit／非完整獨立審計 flags，不冒充 Gemini 全通過 |
| g040／g041：來源版本更新 | 新來源需要傳遞到 Claims、既有 CVPs 及分組資格 | 已授權來源版本更新後重驗受影響組；不是把舊來源的審核身份貼到新版來源上 |
| g052：證據足夠但沒有 CVP | 舊流程允許缺少目標的實質 Claim 保持 deferred | owner 授權該 Claim 新 CVP，正常 GPT／Opus整合及审核、5 creates、完整組審計；後來 v6 規則才把此種遺漏送回正常唯一纠正輪 |
| g054：抽取措辭強於來源 | 某成分的因果強度與自身來源不一致，雙模型調查未達一致 | 先處理精確 Claim 修訂，再補 CVP 覆蓋；不是單憑新 coverage 規則就能改 Claim。其後其他連接異議仍獨立處理 |
| g057：原文有支持、Claim 未綁證據 | 已生成一部分 CVP，但 deferred 成分缺少可用自身證據綁定 | 已授權通用 deferred Claim 流程補證據，6 ops；完整審計又發現另一 Claim 的連接類型問題，須另外的精確 owner 授權，3 ops；最後回正常 CVP 流程補覆蓋，9 creates；完整原 16 Claims／11 CVPs 審計才結案 |

## 2. 恢復與工程案例

| 案例 | 已證明的事實 | 當時正確的接點 |
| --- | --- | --- |
| g013：舊 CLI 報模型不支援 | 舊可執行檔錯誤不能證明帳號權限不足；canonical 訂閱實測可用 | 修正 binary 身份與版本防護，復用同身份 runtime proof，不換模型或 API |
| g035：compact 後仍超限 | 問題不再是 JSON 空白，而是重複的完整長文字 | 已授權共享字串編碼，完整往返校驗，不刪 Claim、來源或改原組 |
| g042：partial readset／分組投影 | 真實已批准版本正確，局部投影漏 child 新身份或仍讀舊 Claim | 修正精確讀集／當前來源投影，不改接受成員，不重新提議內容 |
| g057：磁碟不足 | 新正常 CVP 前置階段空間不足，舊 source／link 已 applied | 精確授權備份歸檔及 SSD 驗證後，只接 normal CVP；不重放先前 6／3 ops |
| g057：有 intent、無回答 | 錯誤發生在 canonical 初始化遗漏，但原請求已留下 unknown intent | 保留原 intent／failure；owner 只批准該精確初始提議一次新重發，不重置未來预算 |
| g057：完整集合指紋漏空集合 | 首次 9 creates 在 SQL 前置 guard 拒絕，證明零提交與前像未變 | 修程式並保存嚴格一次恢復授權，才允許同 plan 首次寫入；後來已 applied 則絕不再次 apply |
| g057：已寫入後 proof／封印交接失敗 | 9 creates 已提交並回讀，後續錯在讀取 marker 或 sealed package 外層欄位 | 修復保存證據的消費，復用模型及已完成審計，不倒退成「尚未寫入」 |
| g057：慢啟動與重複歷史快照 | 歷史 JSON／父子完整證明反覆重建造成大量非模型耗時 | owner 停止生產，改做設計；SQL transaction／changelog v2 仍是待實作，不以局部 cache 成果宣稱全部已修好 |

## 3. 證據定位

詳細原件屬於運行證據，不是新的操作指令。下列以 `$DATA_BASE_DIR/wang-knowledge-platform/staging/` 為起點；閱讀小型 record／handoff／receipt 即可定位，不為查案例重跑原 wrapper 或重建整庫快照。

| 類型 | 原件定位 |
| --- | --- |
| g015 supports 拒絕／退役 | `cvp-production-357/g015-source-upheld-link-repair-20261004-v1/`、`g015-owner-link-retirement-20261004-v1/` |
| g033 整包修訂 | `cvp-production-357/g033-owner-reviewed-relation-package-20261004-v1/repair-record.txt`；更早拒絕留在各原 root |
| g034 主題成分修復 | `cvp-production-357/g034-owner-theme-component-store-repair-20261004-v2/repair-record.txt` |
| g052 新 CVP 與完成 | `cvp-production-357/g052-owner-new-cvp-repair-20261007-v2/`、`g052-applied-new-cvp-completion-20261007-v1/` |
| g054 整組檢查點 | `cvp-production-357/g054-intact-audit-checkpoint-repair-20261007-v1/owner-54-group-checkpoint.json` |
| g057 原來源／子連接／正常 CVP | `source-reconciliation/SR-081e65104c51b5d979a73b7db8a4b0a8e522c7096a35ff7bf176365682539e5e/`；其 `authorized-link-corrections/`、`normal-cvp/` 保存各自原件 |
| g057 性能停止與流程檢視 | `cvp-production-357/g057-scalability-flow-review-20261007-v1/owner-performance-stop.json`、`review.txt` |

這批案例說明三個不同問題：內容真的需要判斷、已批准事實缺少機械連接、以及恢復設計重複工作。它們不能都塞進同一個「請 owner 繼續」出口，也不能都用同一種「再叫模型一次」解決。
