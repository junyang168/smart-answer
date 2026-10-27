# 來源段落變更後更新 Claim

> **讀者**：Developer。
> **類型**：流程。
> **狀態**：當前。
> **與代碼對齊**：2026-10-27，`backend.pipeline.source_claim_updates` 及已合併 Claim ID intake。
> **權威範圍**：已登記來源版本的段落變更如何列出 Claim，並依序呼叫既有完整 Update；不取代來源登記或 CVP 維護。

來源流程只做三件事：比較已登記的新舊來源、列出受影響 Claim、依原文順序呼叫普通 Claim ID Update。
不增加來源影響模型審閱，不以「主張沒有變」省略 Update，也不更改 Claim 管理的既有契約。

```bash
PYTHONPATH=. backend/.venv/bin/python -m backend.pipeline.source_claim_updates \
  --source-id SRC-ID --before /absolute/before.json \
  --change-set-id KCS-ID --root /absolute/task \
  --instruction '來源校訂後完整 refresh 相關 Claim'
```

`--prepare-only` 只產生清單，不呼叫模型、不建立 Claim action 或分配新 Claim ID。
`--detach` 沿用既有 batch 的背景啟動。結果及進度保存在 task root。

來源版本登記必須已完成。入口核對 applied ChangeSet 的來源 before/after、current SQL 後像及實際檔案 SHA。
舊檔必須是原檔，或完整正文、純文字、標題結構、標題位置四項投影全部吻合原登記的恢復檔。
兩者 SHA 不冒充相同。新增、刪除、搬移段落或改變座標須另有映射，這個入口不猜測。

程式逐條檢查本篇 Claim 的完整自身證據及問題等相關 objects。
若任一使用變更段落，該 Claim 就列入完整 Update；共享 Question 的所有回答 Claim 都會列入。
單純 Claim 直接鄰居不因相連而入選。清單不判斷主張應否改寫；內容及相關 objects
由既有 GPT、Opus、多輪修改、交易及回讀流程負責。教授投影片與原話具有同等證據地位。

同一 Claim 使用多個變更段落時，歸入最早一段。先呼叫第一段的普通 Claim ID batch，
確認全部完成後才建立下一段的 batch，讓下一段讀到前段已提交的共享資料。
最後重新檢查本次全部 action 及當前資格；後段使前段失效不能顯示完成，也不自動買新預算重審。
重用 root 必須維持原來源、ChangeSet、方向與清單，已完成 action 沿用原記錄。

找不到任何既有 Claim 承載的變更段落，列為 `unowned_paragraphs` 並保持未完成，不能宣稱已補足新教導。
整篇來源的語義覆蓋、初次抽取及來源版本登記不由本清單入口推斷。

完成狀態只叫 `claims_completed`，明示 CVP／論證 `downstream_handoff.status=not_executed`。
不得把 Claim 階段完成寫成整個來源操作完成。
