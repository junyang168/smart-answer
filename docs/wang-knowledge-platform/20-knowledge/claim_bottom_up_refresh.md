# 自下而上的 Claim 與關係更新

> **讀者**：Developer
> **類型**：流程
> **狀態**：當前
> **與代碼對齊**：2026-11-07，核對同工作樹 `claim_refresh.py` 與 `claim_relation_refresh.py`；尚未正式啟用。
> **權威範圍**：新入口的材料、模型分工與保存方式；不授予舊 Claim Management 審閱資格，不維護 CVP。


下一階段目標見 [Claim Update Flash](./claim_update_flash.md)。本文記錄現有試驗入口；
全來源下層檢查、分項 Jev 問題及新分工尚未完整實作。

## Claim 層

先完成 [Evidence 層](./evidence_refresh.md)，再讀取更新後的 Evidence。
每組限同篇講道，原目標與可新增 Claim 合計最多十條。
已核實的 Evidence 可重用；只追加使用者不觸發來源重審，表述、來源或語境變化則不能重用。

Jev 分別判斷 Claim 是否有未獲完整 Evidence 清單支持的部分，以及 Evidence 中是否有
值得檢查保存方式的實質判斷候選。肯定結果不是漏抽結論。既有 Claim 只以表述與歸屬作查重參考，不帶入整篇的私人證據。
程式先排除逐字／繁簡／空白等明確重複，再由 Jev 確認疑似同義判斷的承載關係。
不把「相關」當作「已明說」。

Claim 與 Evidence 是論證角色，不是互斥的句子類別。同一判斷可以是某條論證的結論、
另一條論證的依據；Evidence 含有實質判斷不代表一定缺少 Claim。
這些由上層 Claim 的 Evidence 提出的候選，只有教授另給經文、觀察、例子或推理支持，
且需要獨立引用／連接、現有內容未充分承載，才提議另建卡。
只作上層論據的斷言保留為 Evidence；原文直接說了本句，只證明教授說過，不能冒充另一層論據。
Jev 對新候選作一次這項窄檢查，只讀候選掛載的完整原文清單；不從附近段落補入未綁定的新論據。
沒有另給支持就保留 Evidence，不追加 GPT 修訂或循環審核。
這項升級條件不強迫教授原始主張另找平台虛構的論據，也不改寫其他 Claim 建立流程。

GPT 批量處置目標疑點。每個判斷候選必須明示保留在 Evidence、由既有 Claim 承載、
必要新建或來源不足，並附簡短理由；可將同一論點連貫補入本組既有 Claim。
粒度上的合理選擇不算漏抽，保留 Evidence 不產生新 Claim 配額或額外覆核。
新建上限不足時必須明示未完成，不以截斷清單冒稱完整。
程式配置 ID、重建來源綁定，保留有效舊定位；不讓模型生成 ID、時間或雜湊。
舊 Claim 的來源 ID 若恰好是來源文件明載的 `transcript_id`，可對應至正式來源 ID；
不靠相似名稱猜測。查重、交易內身分核對及關係材料使用同一對應。
新增 Claim 所回答的既有 Question 追加答案連線，保留原答案。

## 直接附屬記錄

只讀取本組 Evidence 的直接 KnowledgeRelation 端點、共享自身片段的 Observation、
目標明列的 Position，以及直接問題／答案；不沿端點的其他邊展開。

程式核對片段定位。Jev 分別檢查 Observation 的內容與經文用途、Position 的立場歸屬、
Question 的提問與已有答案連線。有具體疑點才批量交 GPT，未改者不重寫。
GPT 只可修改該記錄列出的內容欄位及已提供的來源／答案 ID；不能更改資格或其他物件。
失效的多餘片段可以從所屬記錄移除，原片段與其他使用者保留；沒有有效來源則保留未完成。
共用原文、片段和 Claim 摘要在一批輸入中只提供一次。

KnowledgeRelation 沿用下述關係判斷與理由處置。若未升為 Claim 的 Evidence 直接回答了問題，
可核實並建立 Evidence → Question 的 answers 連線，不因缺少下層論據而丟掉教授的回答；
不在其他 Evidence 節點間全面推測新邊。
依既有資料模型限制來源與目標類型，例如 answers 的目標可以是 Question；
反方立場與設問不充當教授斷言。檢查的是直接附屬範圍，不宣稱整篇所有記錄均完成。

## 關係層

材料限目標的直接既有關係、本組可能缺少的關係，以及新 Claim 與共享 Evidence 使用者
之間的候選關係。不沿鄰居的其他關係遞迴擴張，也不做全庫兩兩比較。

1. Jev 判斷直接關係的類型和方向，包括「沒有關係」與「不能確定」。
   不能因為同主題就建立支持，跨來源印證不能冒稱同段論證。
2. 程式依這個決定修改類型／方向、提議刪除不成立的舊關係，或準備新增關係。
   明確禁止某種類型的既有約束及人工保護仍有效。
3. Jev 檢查既有理由在選定方向／類型下是否仍成立。有效理由保留。
   新增、失效或不能確定的理由才交 GPT，每批最多八項。
4. GPT 只輸出理由或材料不足；輸出契約不允許改端點、類型或任務清單。
   根據兩個 Claim 和本組已核實 Evidence 寫一兩句理由，不補新神學前提或經文論據。
   不追加一輪模型去重判這份理由；材料不足則本組保留未完成。

## 保存與恢復

Claim、Evidence 使用者、Question 答案連線及關係在同一個受版本保護的 SQL transaction
保存，回讀實際寫入內容；Evidence 層自己的修正有獨立交易記錄。
新 Claim 去重還會在交易內核對同來源 Claim 身分清單，防止期間另有新增而重複建立。
舊理由與退役關係保留在資料庫歷史中，不覆寫審閱結論。

模型輸入、開始記錄、原答覆及待寫內容保存在同一 root。
預覽後 apply 重用已保存答覆；結果未知的呼叫不自動重發。
GPT 使用訂閱 medium、共用容量限制；Jev 使用 API。本入口不調用舊 Update executor 或 Opus。
這些步驟沒有授予舊流程的獨立審閱資格，也沒有宣稱 Jev 不會漏判。

```bash
PYTHONPATH=. backend/.venv/bin/python -m backend.pipeline.claim_refresh \
  --claim-ids /absolute/path/claim-ids.json \
  --root /absolute/path/run \
  --capacity-file /absolute/path/shared-capacity.json
```

預設保存候選，`--apply` 才寫配置的資料庫；`--database-url` 可指向隔離測試庫。
可用 `--evidence-root` 重用已完成、仍匹配的 Evidence 結果。
