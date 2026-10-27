# #464 簡化來源 Claim 清單接續驗證

> **讀者**：Developer。
> **類型**：記錄。
> **狀態**：當前。
> **與代碼對齊**：2026-10-27，新增 `source_claim_updates`。
> **權威範圍**：本次隔離測試及真實來源只讀清單，不宣稱真實 Claim 或 CVP 已完成。

本次不恢復已撤回的 source-impact 模型審閱，不更改任何既有 Claim 管理、SQL、runtime、
prompt 或 policy。只新增來源段落依賴清單及依序呼叫普通 Claim ID Update 的 wrapper。
來源版本已登記是明確前提，不重做先前提交的來源交接。

隔離 PostgreSQL 用共享 Question 連到 A、B、C，另有只與 A 相連的 N。
第一段選出 A、B、C，N 不入選；第二段單獨選出 D。
以真實普通 Update executor 驗證：第一次 Opus 發現舊引文、GPT 修訂共享片段、
第二次 Opus 通過後實際提交回讀。B、C 讀取已修正的共享資料，主張保留。
D 的 batch 在前三條完成後才建立。整次 resume 不增加模型呼叫。
來源物理版本漂移則在準備 Claim batch 前拒絕。

合成模型的實際呼叫順序分別為 A：Opus/GPT/Opus，B：Opus，C：Opus，D：Opus/GPT/Opus。
來源清單、普通 intake、直接鄰居、既有執行、來源 lanes 及段落 batches：
**138 passed，41.91 秒**。文檔檢查只有既有獨立 notes-to-sermon 缺失錨點。

《中保》的 `--prepare-only` 使用已提交 `KCS-1ed04508d4cb67c58072`，
重新核對原完整 before 四項登記投影、目前來源 SQL 後像及檔案 SHA。
得到 S0049 的 P09-CL005、CL006、CL007、CL008、CL009，以及 P10-CL001、CL002；
S0050 另有 P10-CL003。跨兩段的 Claim 歸最早受影響段落。
零模型呼叫、零 Claim action／新 Claim ID 分配；此清單尚未執行內容 Update。

已登記來源下，這個入口只負責依賴所及的 Claim refresh；
不宣稱完成整篇來源語義覆蓋、來源抽取或 CVP／論證維護。
