# #464 原文片段異議與 Evidence 判定校驗修正

> **讀者**：Developer。
> **類型**：記錄。
> **狀態**：當前。
> **與代碼對齊**：2026-10-27，`claim_evidence_review.validate_assessments`。
> **權威範圍**：本次既有 Claim 校驗缺口、隔離反例及原答覆接續驗證；不宣稱真實內容完成。

簡化來源流程已實際完成 P09-CL005、CL006、CL007、CL008。
Q007-05 已在 `KCS-ea96918c0756a1b1d808` 中改成現行逐字引文；
CL005、CL006、CL007 的主張皆保留 revision 1，當前資格已確認有效。
後兩條審閱確實讀到片段 revision 2。

隨後 CL009、P10-CL001、P10-CL002 的原始 Opus 答覆被既有 Evidence 校驗拒絕。
Opus 已在 Evidence 實際綁定的原文片段上給出具體異議；
校驗卻只承認 Claim 或 Evidence 本身的異議，沒有承認其綁定片段。
因此真實問題尚未交 GPT，就被誤判為缺少具體異議。

本次只將 Evidence 的實際 fragment_keys 納入可解釋「證據不足」的異議位置。
仍要求具體異議；無關 Observation、Question、鄰居片段以及借用非自身 Evidence 的情況仍拒絕。
不更改模型原文、不增加來源審閱、不把拒絕變為通過。

隔離資料庫驗證先重現原始答覆拒絕，再使用既有 `recover_returned_review(unrecorded=True)`
將同一份原始 Opus 回答接回原 action。完整 raw 位元組保留，工程接續零模型呼叫，
returned_review_passed 為 false；之後由正常 GPT 修訂、Opus 複核、交易及回讀完成。
呼叫總數仍為 Opus/GPT/Opus，沒有補發初審。

自身證據正反例、來源清單、正常 Update、直接鄰居、共享 objects、範圍接續、批次與既有復原：
**195 passed，49.51 秒**。真實答覆接續與後續內容結果另行核對。
