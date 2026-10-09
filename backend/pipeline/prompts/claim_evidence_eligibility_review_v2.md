# Evidence 資格復核：不是解除 hold 或直接批准

本次 policy 明示允許 context_only 資格復核。舊標籤是待核查的分類，不是永久禁止。
使用既有 claim_assessments.cause=evidence_binding 分類資格錯誤，提出完整 Evidence 後像，
不新增另一個判讀階段。Claim 正確時保持正文、類型、歸屬和成員；不要求先改 Claim。

先查原標籤／角色、精確片段及完整自身正文，再判斷它是否支持教授在此提出的主張。
教授以耶穌口吻轉述其解釋，不等於逐字經文，但可支持「教授作了這個解釋」；
必須保留 speaker、stance、discourse_role、原 statement、全部引文及 fragment 綁定。
不要因 dramatic_paraphrase 標籤直接判為無效，也不要因 Claim 有 pass 就直接升級。
只能對 permissions 明列的既有 context_only Evidence 提議 eligible_with_label，
如已有 valid_for_identity_review=false 且本次純分類復核成立，可提議 true。
不得改反方／聽眾歸屬來獲得資格；withheld、rejected 及受保護審核狀態不在此分支。
片段缺失、錯來源、正文歧義或真實裁決衝突仍保留未決，不添加假引文。

每個共享該 Evidence 的 Claim 必須有精確問題及自身正文判讀；全部相關 CVP 影響仍要核查。
資格修訂不批准 CVP 身份、角色、端點或退役，也不把另一並列結論變成 supports。
GPT 提議不批准自己；Opus 必須獨立審核這個精確標籤變化的依據、歸屬及全部後像。
新資格只有共同 SQL 交易、歷史與回讀通過後才生效，完整組獨立審計仍必要。
舊 raw、拒絕、intent 與 once 預算保留；新版政策不能重問舊呼叫或把舊未決改為通過。
