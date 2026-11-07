# Evidence 層的獨立修復

> **讀者**：Developer
> **類型**：流程
> **狀態**：當前
> **與代碼對齊**：2026-11-07，核對同工作樹的 `backend/pipeline/evidence_refresh.py`；實測結果另存執行目錄，不代表正式資料已更新。
> **權威範圍**：新自下而上流程的 Evidence 修復步驟；不取代 Claim 資格、CVP 或來源校訂規範。


新的獨立入口見 [Claim Update Flash](./claim_update_flash.md)。本文仍記錄原試驗入口；
不會自動啟用 Flash 的全來源下層檢查、分項 Jev 問題及新分工。

## 目標

直接接收 Evidence ID 清單，完成「Jev 檢查 → GPT 修正 → 校驗與保存」。
不調用舊 Claim Management 的 intake、executor、budget 或 qualification。
共享資料模型、訂閱客戶端、引文校準函式和資料庫交易仍使用既有底層元件。

這是自下而上更新的第一層。Claim 保存教授的判斷，Evidence 保存支持、限定、
反駁或說明判斷的論證步驟；意義見[共享模型](../00-overview/shared_knowledge_model_v1.md)。
本步不把 Evidence 摘要整句升成 Claim，也不新建 Claim。

## 執行

1. 凍結原 ID 清單、模型、修訂上限、實際資料及完整自身來源。
   只讀取這些 Evidence、其來源片段／文檔及直接使用者；不遞迴展開整個 Claim 圖。
2. 程式核對原文、來源版本、段落、時間與雜湊。不合格時先處理定位，不能交語義模型猜位置。
3. Jev 接收 statement 和完整掛載原文清單，判定是否有任何實質部分缺乏支持。
   片段共同支持表述，不是一句摘要對一句引文。不要求逐字改寫，也不補未表達的意圖或因果。
4. 有疑點才調 GPT，每次至多八條，合併成一次處置。GPT 用同一份完整原文及附近段落
   作必要修正，也可有據判定預檢異議不成立。
   模型只能給表述、說話者、立場、逐字片段和理由；程式產生 ID、定位和雜湊。
5. 程式還原可唯一定位的機械引文誤差，核對欄位、原文綁定和完整處置清單。
   GPT 對每項只處置一次：修正、保留並澄清、或指出來源不足。
   不追加 Jev 復審來否決 GPT 的有據處置，也不循環加額。
   相同輸入的預檢及 GPT 答覆復用；零次修訂模式只做預檢。
6. 所有原項通過後，修正和新片段在同一個 SQL transaction 保存，核對實際後像。
   原文與讀取記錄的版本／不存在狀態必須仍與凍結時一致。

沒有必要修改時不產生內容寫入。GPT 不能改 owners、Claim、來源文件、審閱資格或其他欄位。
舊來源片段不會被原地改寫；新的逐字摘錄建立來源版本綁定的新片段，保留其他使用者。

## 呼叫

```bash
PYTHONPATH=. backend/.venv/bin/python -m backend.pipeline.evidence_refresh \
  --evidence-ids /absolute/path/evidence-ids.json \
  --root /absolute/path/run \
  --capacity-file /absolute/path/shared-capacity.json
```

預設只保存修正候選。明確加 `--apply` 才寫入配置的資料庫；隔離測試可指定
`--database-url`。同一 root 驗證後再加 `--apply` 會復用答覆，不重跑模型。
Jev 使用 `TYPESAFE_API_KEY`；GPT 使用 ChatGPT 訂閱，預設 medium，沒有 API fallback。
實際 GPT 呼叫沿用指定的共享容量限制。

## 恢復與完成

每個模型輸入、開始記錄、原答覆、候選及機械引文差異保存在同一目錄。
同一目錄不可同時執行；已有開始記錄卻缺少完整答覆，不自動重發。
SQL 回執丟失時，先查該 ChangeSet 和實際後像，不再寫一次；只缺回讀時只補回讀。

`verified` 表示這份 Evidence 已完成預檢／必要 GPT 處置及程序檢查；結果逐項記錄
是 Jev 未發現疑點，還是 GPT 處置及程序綁定校驗，不冒稱 GPT 修後已獲 Jev 批准。
`applied` 表示修正已交易並回讀。
兩者都不表示 Claim 已完成或資格已獲批准。結果另列 `affected_claim_ids`，
由下一層以更新後 Evidence 處理，既有資格不會被這個工具悄悄改成通過。
`needs_attention` 保留未解決原因，本清單不會部分提交。

目前 Jev 的通用問題仍有已知漏判，沒有宣稱能取代所有內容審閱，也沒有隱藏的
0.75 機率門檻。[新 Claim 入口](./claim_bottom_up_refresh.md) 接續本層處理主張、新建及關係；
它仍是獨立的新流程，未授予舊 Claim Management 資格或正式啟用。
