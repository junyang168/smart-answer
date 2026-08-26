# 分段抽取之后，AI 複審還抓到什麼（WKP-F01.9 / #98）

> **讀者**：Developer
> **類型**：記錄
> **狀態**：歷史記錄，只說明測量當時
> **與代碼對齊**：不適用
> **權威範圍**：無。本文是 #98 的一次實測，不約束今天的做法。

> 狀態：實測記錄。輸入是太 16 母本的兩個抽取包（23 條 claim 的 v2、61 條 claim 的 v3），複審是 `corpus_ai_review`（`claude-sonnet-5`，`--spot-check-percent 0`，`--max-output-tokens 48000`）。所有數字來自實際調用，不是推算。

## 一、先確認的事：母本走得通同一條載入路徑

`corpus_ai_review_runner --claim-layer-package` 對逐字稿和母本用的是同一個入口，但解析分兩路：`load_knowledge_source_document` 看 `source_type`，`notes_manuscript` 走 Markdown（按空行切塊），其餘走逐字稿 JSON。兩邊都用 `S{position+1:04d}` 命名段落，抽取端 `detailed_knowledge_extraction_runner.segment_locator` 是同一個式子。

在不調用模型的情況下驗過一次：117 個 Markdown 區塊，116 個 anchor 的 `paragraph_key` 全部落在範圍內，每一段 `verbatim_excerpt` 都逐字出現在它宣稱的段落裡。**母本不必改任何載入程式碼即可複審。**

## 二、成本

| 包 | claim | 調用 | 輸入 token | 輸出 token | 牆鐘 | 成本 |
|---|---:|---:|---:|---:|---:|---:|
| v2（23 條，原 prompt） | 23 | 1 | 26,824 | 18,584 | 3m31s | $0.25 |
| v3（61 條，原 prompt） | 61 | 1 | 57,326 | 32,840 | 5m29s | $0.47 |
| v3（61 條，改後 prompt） | 61 | 2* | 115,145 | 56,340 | 9m33s | $0.72 |

\* 第一次輸出未通過程序驗證，重試一次。被拒的那次照樣計費（$0.28），只是輸入靠快取重讀省了九成——這正是 usage 要連被拒次數一起記的原因。單次通過約 $0.44。

claim 從 23 漲到 61（2.65x），複審成本只漲到 1.9x：輸入裡的來源全文是固定的，漲的只是候選 JSON 和輸出。**複審沒有變成瓶頸。**

順帶修掉兩個量測本身的缺陷：`usage_row` 只讀 `input_tokens`，而 Anthropic 把兩條快取腿排除在該欄之外，於是一次 5.7 萬 token 的複審被報成 1,134；`_archive_existing_review` 只用 reviewer fingerprint 命名，同一複審員跑兩次時後一次會覆蓋掉前一次——而那兩次正是穩定度比較所需的一對。

## 三、複審提了什麼（原 prompt，61 條 claim）

同一個包、同一個 prompt 跑了兩次，因為單跑一次無法分辨「沒抓到」和「這次沒抓到」：

| 意見類別 | 第一次 | 第二次 | 機械閘能不能做 | 判定 |
|---|---:|---:|---|---|
| 關係類型「不在標準詞彙內」 | 5 | 5 | 已經在做 | **全是假陽性，刪** |
| 錨點語義不足（`insufficient_anchor`） | 2 | 1 | 做不到 | 保留 |
| 主張重複（`duplicate_claim`） | 0 | 2 | 做不到 | 保留並加強 |
| scripture_refs 引入來源外經文編號 | 3 | 0 | 做得到 | 應改為機械閘 |
| 說話者／立場 | 0 | 0 | 做不到 | 保留（母本少有代言） |
| 遺漏（完整性） | 0 | 0 | 逐句自檢已覆蓋 | 未觸發，不動 |
| 產品路由 | 0 | 0 | — | 61 條全回 `unchanged` |
| 要求人工升級 | 0 | 0 | — | 未觸發 |

兩次都是 8 條 claim 被標，重疊 6 條——但重疊的部分幾乎全是關係詞彙那組假陽性。**真正的語義意見兩次只重疊 1 條**（P01-CL012 的錨點不足）。

### 關係詞彙那 5 條為什麼是假陽性

複審 prompt 第 5 條寫死了 `supports、answers、opposes、qualifies、applies`。那是逐字稿管線（`corpus_first_pass_survey`）的詞彙表。母本走的 v4 抽取用的是 `supports、answers、qualifies、applies、refutes、contextualizes`（`detailed_knowledge_extraction.RELATION_TYPES`），兩邊都由程序按 schema 驗證。於是複審對著一份合法的關係表，逐條報告它「不在標準詞彙內」——一個共用 prompt 拿另一條管線的詞彙表去審這條管線。5 條裡有 1 條同時指出了方向問題（CL005 的 `refutes` 應為 `supports`），那部分是真的。

### scripture_refs 那 3 條是真的，但漏得多

複審說 P02-CL007／CL010／CL016 的 `scripture_refs` 裡有來源正文沒出現過的經文編號。逐條核對過：屬實。但機械掃一遍全包，66 個 `scripture_refs` 裡有 **14 個**的章節號在母本正文中根本不存在，分佈在 11 條 claim 上。複審抓到 3 個（21%），另一次抓到 0 個。這一類該由抽取端的機械閘處理，不該指望複審。

### 重複那一組：卡片猜對了，但複審不穩

按標題二元組相似度掃 1,830 個配對，只有 1 對超過 0.30（P01-CL002 / P02-CL003，複審抓到了），第三名開始就不是重複了——機械相似度做不了這件事。而人讀得出來，「身分 vs 性質」這個結論在四個章節裡各出現過一次。第一次複審報 0 條重複，第二次報 2 條。**檢查條目在 prompt 裡，但在 61 條的規模上不穩定觸發。**

## 四、改了什麼，改後測到什麼

只改兩條，其餘七條檢查一條沒刪：

- 第 5 條：不再列舉關係詞彙；明說取值由程序按 schema 驗證、各管線詞彙表不同，只判斷方向與含義，以「不在標準詞彙內」為唯一理由的意見一律刪除。
- 第 4 條：明說候選可能是按章節分段抽取的，同一結論會在各章節各出現一次；必須與**全部**其他主張比對，不能只看相鄰編號；判定重複時寫出對方 claim_id。

同一個包，改前改後各跑一次：

| | 改前（兩次） | 改後 |
|---|---|---|
| `relation_error` | 5 / 5，其中 0 / 1 條是真的 | **2，兩條都是真的**（方向錯、目標指錯段落） |
| `duplicate_claim` | 0 / 2 | **5**，並且指名對方 claim_id |
| `insufficient_anchor` | 2 / 1 | 1（第三次抓到同一條 P01-CL012） |
| 假陽性佔比 | 10 條意見裡 5 條 | **0** |

改後找出的重複是一整組跨章節的：P01-CL002、P02-CL003、P02-CL009、P03-CL005、P03-CL006 圍著同一個結論（門徒的難處不在身分而在性質），P03-CL007 與 P02-CL005 是對太 16:23 的同一段解釋。這是分段抽取的結構性後果，不是抽取變差——61 條裡約 6 條，覆蓋率不會因此被吐回 50%。

## 五、下游能不能執行——原本不能

量出重複之後才發現：**複審最有價值的那類意見，管線下游沒有人能執行。**

仲裁接受一條意見後寫出的 override，能做的事列在 `corpus_ai_adjudication.PATCH_SCHEMA` 裡：改寫 statement／claim_kind／route_type／scripture_refs、排除或新增 anchor、按 id 排除關係。沒有「刪除一條 claim」，也沒有「合併兩條」。重複唯一能落腳的地方是 `structural_notes`——一段自由文字，`knowledge_consensus_applier` 把它抄到 claim 上，然後沒有任何程式讀它。仲裁 prompt 自己寫著：`structural_notes 只解释拆分／合并等后续结构影响；它不能代替可执行补丁`。

所以在這張卡之前，複審找出的 5 條重複，是 5 條沒有人能執行的意見。

### 補上的合併動作

- 複審的 `duplicate_claim` 意見新增 `duplicate_of_claim_id`。指名對方 claim_id 是欄位，不是散文——後面兩級只讀欄位。
- 仲裁 patch 新增 `superseded_by_claim_id`，並且只有在複審已經指名該目標時才可填；不得自己發明合併，不得鏈式合併（A→B→C 會讓結果取決於 override 的套用順序），也不得一邊合併一邊改寫被合併的那條。
- `knowledge_consensus_applier` 執行合併：留下的那條繼承被合併者的 anchor、evidence、scripture_refs 與關係；被合併的那條標記 `superseded_by` 與 `review_status: superseded`，**留在包內**，因為它是合併發生過的證據。

### 為什麼是合併不是刪除

卡片警告過：砍得太狠，#88 提上來的覆蓋率會在這一步被吐回去。所以合併的定義就是「不准弄丟對來源的抓握」，而且這條規則是程式在檢查的：套用後，仍在活躍 claim 上的 evidence id 集合，必須是套用前的超集，否則整次合併失敗。

`summary` 因此多了 `active_claim_count` 與 `superseded_claim_count`；`claim_count` 仍然是檔案裡的總行數。

### 三級跑通一次（真實資料，無手寫補丁）

複審（新 prompt，第三次獨立跑）→ 仲裁 `gpt-5.6-sol`（43 秒）→ 套用：

| | 值 |
|---|---|
| 複審提出 | 5 重複、1 關係方向、1 錨點不足；重複全部指名了 `duplicate_of_claim_id` |
| 仲裁 | 7 條全部 accept（5 條合併、1 條刪關係、1 條補錨點），0 條退回人工 |
| 套用後 claim | 檔案 61 行 → **活躍 56，superseded 5**（都留在檔案裡，各自記著併入了誰） |
| 錨定 evidence | 101 → **102**（沒丟，仲裁還補進 1 條） |
| 關係 | 35 → 32（1 條被仲裁刪除，2 條合併後成為自環或重複邊） |
| 留下的 P02-CL003 | anchor 4 → 6 |

值得記一筆：複審這一次挑的存活者和它上一次挑的不完全相同（P03-CL005 這次併入 P01-CL003，上一次說的是 P02-CL003）。合併的**方向**跟語義意見一樣不穩定，只是這一層的錯代價低——兩條說的確實是同一件事，併入哪一條都不會弄丟來源。

## 六、結論

1. **重心確實從補漏移到消重**，卡片猜對了。逐句自檢接管了遺漏（複審兩次都報 0 條），複審在新抽取下唯一無可替代的產出是跨章節重複與錨點語義不足。
2. **刪掉的不是檢查條目，是一個過期的詞彙表。** 七條檢查一條沒少；假陽性從 50% 降到 0，是因為停止讓複審做程序已經在做的事。
3. **複審的語義意見不穩定**：兩次同 prompt 只重疊 1 條。單跑一次的複審結果不能當作「這個包只有這些問題」的證據。真要當閘用，得跑兩次取聯集，或接受它是抽樣而非普查。
4. **複審的意見要能被執行**：合併動作補上之前，「消重」這個新重心在管線裡沒有出口。
5. **下一件該做的是機械閘**：`scripture_refs` 的章節號是否出現在來源正文，程序一次掃完 14 條，複審抓 3 條。這屬於抽取端驗證，另開卡片。

## 覆現

```bash
B="$DATA_BASE_DIR/wang-knowledge-platform/staging/claim-layer/matthew-16-notes"
PYTHONPATH=. .venv/bin/python -m backend.pipeline.corpus_ai_review_runner \
  --claim-layer-package "$B/v3-sections/notes_manuscript_16-72483dc200ad.detailed-knowledge.json" \
  --claim-layer-output  "$B/v3-sections/notes_manuscript_16-72483dc200ad.independent-review.json" \
  --spot-check-percent 0 --max-output-tokens 48000
```

歷代複審留在 `v3-sections/review-generations/`，檔名帶 reviewer fingerprint 與內容 hash；v2 基線在 `v2-reextraction/notes_manuscript_16-72483dc200ad.independent-review.json`。
