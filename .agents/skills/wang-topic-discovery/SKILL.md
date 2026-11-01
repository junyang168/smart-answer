---
name: wang-topic-discovery
description: Run or resume Wang Knowledge Platform topic discovery from existing topics and seed Claims, expand adjacent groups from borderline Claims, and reconcile corpus coverage. Use for cross-sermon Claim grouping campaigns, not Claim cleanup, CVP generation or article authoring.
---

# Wang Topic Discovery

用既有 discovery agent 收集同一問題的 Claims，再以邊緣 Claims 開始相鄰 group，直到本次範圍得到明確處置。Codex 負責調度與對帳，不另造分類模型、controller 或 Agent 框架。

建立或修改本 skill 不代表啟動 discovery。使用者要求執行後，沿已授權範圍持續處理，不逐組重新索取批准；尊重「先一組」「只討論」「停止」等邊界。知識庫只讀，產物寫 staging；本技能不啟動 CVP／Claim 維護或文章生成。

## 取得當前依據

定位 Wang repo（本機通常 `/Users/junyang/app/smart-answer`），遵守 `AGENTS.md` 的 ticket、獨立 worktree 與 Python 環境規則。先讀：

- `docs/wang-knowledge-platform/00-overview/project_mission_statement.md`
- `docs/wang-knowledge-platform/00-overview/solution_architecture.md`
- `docs/wang-knowledge-platform/20-knowledge/topic_discovery_agent_solution.md`

讀取本次 ticket、現成主題清單和已完成 discovery 產物，不重新發明主題普查。

- [#517](https://github.com/junyang168/smart-answer/issues/517)：主題問題、種子與來源。讀實際產物；它已由馬太延伸至其他書卷、神學、生活與護教，不以 ticket 標題限制範圍。
- [#459](https://github.com/junyang168/smart-answer/issues/459)：單主題 discovery 實作。
- [#531](https://github.com/junyang168/smart-answer/issues/531)：本技能。後續執行使用當次適合的 campaign ticket，不重開已完成實作卡。

目前授權流程不加獨立模型分組審核，也不要求負責人逐組批准。不得從舊 partition grouping 匯入互斥成員、每組 20 條或逐組人工確認限制。不得引入 embedding Top-K 硬過濾或 JEV。分組不是 CVP 身份裁決，不把支持、限定或共同主題當成同一觀點。

執行前讀 [現有入口與恢復](references/runtime.md)，核實所用 checkout 的實作；未合併 worktree 中的功能不假定 main 已有。

## 起點與清單

1. 將現有主題問題和 1–3 條種子整理為工作隊列；保留原始主題來源。按「回答什麼問題」辨認重複／相鄰主題，不按名字相似合併。教授的理由、限定、例外都屬於該問題的材料，不能只收結論。
2. 核實種子目前版本、原文與問題的關係。既有 agent 直接收入 seeds，不另判定；失效／退役／替代種子需核實 successor，不能只按 ID 或舊 statement 繼續。
3. 從當前資料庫建立本次 Claim 分母：ID、版本或內容指紋及上游待處理狀態。排除已退役者但保留排除原因；待修復／hold 的 Claim 不從盤點消失，也不能把 discovery 收入當作合格寫入 CVP。
4. 全庫任務可沿現有圖跨來源、跨舊 partition 探索。局部任務遇到範圍外材料，記為上下文或新主題線索，不把它算入原分母，不默默擴大 campaign。

在本次 staging 目錄維護簡單可接續記錄即可：主題問題與 seeds、狀態、run 路徑、邊緣路由及剩餘 Claim 處置。用 JSON／JSONL 與原生 `result.json`，不建第二個知識庫。狀態至少能分辨待跑、運行、已收斂、未收斂／失敗；不得把啟動成功算完成。

## 主題隊列與邊緣擴展

- 取一個待跑主題，沿既有 CLI 跑 discovery。只從新收入的 Claims 擴展，判定始終對照原種子的問題，避免主題逐輪漂移。
- 每個主題完成後，讀 `result.json`、`claims.json`、`borderline.md`。程序核對每片候選都有判定、成員 ID 有效，核實停因；達輪數保護上限只算未收斂。
- **邊緣 Claim 是相鄰 group 的起點，立即接入隊列，不等最後清理。** 讀其問題與原文：已有 group 就關聯它；尚無 group 就將同一問題的邊緣 Claims 組成新 seeds。單條也可以成為起點，不規定湊滿幾條。
- 對已有但尚未跑的 group，補充能代表新來源／斷開圖區的種子。對已完成 group，先查該 Claim 是否已被判定；只有帶來未覆蓋入口、新材料或原判定需要重訪時才補跑。既有 CLI 不支援單純增補 frontier，不假稱它有此功能；補充 run 保留原產物和去重後的成員聯集。
- 已去過 A 的邊緣由 B 再指回 A，記錄這條路由，不因往返就反覆重跑。追蹤的是「主題問題＋種子／Claim 版本＋既有判定」，不以全局 seen Claim 阻止它在另一主題被評估。
- 一條 Claim 可以進多個 group，或為多個相鄰 group 提供起點。保留重疊，不強指定唯一主題。`out` 只表示不屬於當前主題；`borderline` 也不是全局排除。
- 邊緣只是背景、缺材料或問題仍不清楚時，保留具體原因與待辦，不為清空隊列隨意造新主題。不要把所有其他 group 都放進 `--other-topic` 強制排斥重疊。

不新增全面模型複審或讓人逐條分類。Codex 按材料處理普通路由；只有無法從來源判定且影響後續範圍的實質疑問才詢問使用者。

## 全庫覆蓋與斷開的材料

隊列走完仍須對帳。KG 收斂不是完整性證明：沒有 CVP 連接、引用不同經文且措辭不同的同題 Claim 可能走不到。

用當前 run 產物做集合核對（seeds 也計入觸及）：

- `touched`：全部 seeds 與實際判定過的候選聯集。
- `grouped`：全部 group 成員聯集；跨 group 重複允許，覆蓋數用去重 ID。
- `untouched = universe - touched`：從未進過任何判定的 Claims。
- `unassigned = universe - grouped`：包括被各組拒收、邊緣未落組及完全未觸及者；逐項對照有理由的待辦／非主題處置，不把 touched 當 grouped。

對未處置的剩餘項，按來源與原文順序分批閱讀，覆蓋全部剩餘 ID，不用關鍵字或 Top-K 再截斷：屬於已有問題的補入口，回答新問題的產生 seeds 並回隊列，背景／例證／上游材料缺陷明確記錄。已在 A 分組也不能證明它在 B 沒被漏收；用既有來源清單、不同來源 seeds 和歷史已知案例檢查此類缺口，不把 ID 全覆蓋宣稱為所有語義關係全找齊。

結束時核對與起始分母的資料變動。中途 Claim 修訂／替代、來源更新或新 Claim 另列增量，只重訪受影響部分；不將舊判定冒稱當前有效，也不悄悄讓分母移動。

## 交付與停止

交付主題問題／成員／理由／來源路徑、跨組重疊、邊緣路由、仍未歸組及上游待處理項。報告起始去重 Claim 數、觸及數、歸組數、未處置數、未完成主題及增量，不把各組條數相加當全庫覆蓋。

完成須同時滿足：本次應跑隊列無未完成項，所有候選有判定，邊緣有明確去向，分母內每條 Claim 已分組或有具體處置。仍有待定／上游缺陷如實披露；不要求每條都變成主題，也不宣稱教授全語料已整理完。

向 CVP 階段交付可追溯的 group Claim 清單和重疊／資格說明即可。CVP 召回仍面向全庫，不以 topic 限制 CVP 身份；是否開始 CVP 處理由使用者的執行範圍決定。
