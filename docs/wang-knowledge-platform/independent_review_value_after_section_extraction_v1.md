# 分段抽取之后，AI 複審還抓到什麼（WKP-F01.9 / #98）

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

## 五、結論

1. **重心確實從補漏移到消重**，卡片猜對了。逐句自檢接管了遺漏（複審兩次都報 0 條），複審在新抽取下唯一無可替代的產出是跨章節重複與錨點語義不足。
2. **刪掉的不是檢查條目，是一個過期的詞彙表。** 七條檢查一條沒少；假陽性從 50% 降到 0，是因為停止讓複審做程序已經在做的事。
3. **複審的語義意見不穩定**：兩次同 prompt 只重疊 1 條。單跑一次的複審結果不能當作「這個包只有這些問題」的證據。真要當閘用，得跑兩次取聯集，或接受它是抽樣而非普查。
4. **下一件該做的是機械閘**：`scripture_refs` 的章節號是否出現在來源正文，程序一次掃完 14 條，複審抓 3 條。這屬於抽取端驗證，另開卡片。

## 覆現

```bash
B="$DATA_BASE_DIR/wang-knowledge-platform/staging/claim-layer/matthew-16-notes"
PYTHONPATH=. .venv/bin/python -m backend.pipeline.corpus_ai_review_runner \
  --claim-layer-package "$B/v3-sections/notes_manuscript_16-72483dc200ad.detailed-knowledge.json" \
  --claim-layer-output  "$B/v3-sections/notes_manuscript_16-72483dc200ad.independent-review.json" \
  --spot-check-percent 0 --max-output-tokens 48000
```

歷代複審留在 `v3-sections/review-generations/`，檔名帶 reviewer fingerprint 與內容 hash；v2 基線在 `v2-reextraction/notes_manuscript_16-72483dc200ad.independent-review.json`。
