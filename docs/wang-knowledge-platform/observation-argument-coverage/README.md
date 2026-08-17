# observation 進入論證層的覆蓋率

報告本身**不進版本控制**：review queue 逐條列出教授的觀察原文，屬於教授材料,依專案規則不得進入 Git。

產出位置：`$DATA_BASE_DIR/wang-knowledge-platform/staging/reports/observation-argument-coverage/`

重跑方式：

```bash
export KNOWLEDGE_DATABASE_URL='postgresql:///smart_answer_knowledge'
R="$DATA_BASE_DIR/wang-knowledge-platform/staging/reports/observation-argument-coverage"

PYTHONPATH=. .venv/bin/python -m backend.pipeline.observation_argument_coverage \
  --output "$R/baseline-20260817.json"

PYTHONPATH=. .venv/bin/python -m backend.pipeline.observation_type_migration \
  --output "$R/observation-type-review-queue-20260817.json"
```

兩個工具都是確定性的，不呼叫 model，相同輸入產出相同結果。兩者都**不寫入資料庫**。

## 為什麼舊的那個數字量錯了

舊口徑是「observation 的 source fragment 也被某條 evidence step 引用」：430 條中 55 條，12.8%。

那不是一條邊，是**引文碰撞**。fragment 以 `(segment_index, verbatim_excerpt)` 為 key 去重，所以 observation 和 evidence step 只有在引了**逐位元組相同**的一段字時才會共用 fragment。evidence step 的引文多切四個字，同一句話就變成兩個 fragment，連結消失。

實例：`OBS014` 引文是 `25、26、27 節，在原文中各以 γὰρ 開頭`，`E023` 引文是
`緊接著的 25、26、27 節，在原文中各以 γὰρ 開頭，形成三個連續的理由子句。`——同一句，舊口徑判為孤兒。

## 2026-08-17 基線（僅數字，不含內容）

| 狀態 | 條數 | 意思 |
|---|---:|---|
| `in_argument` | 55 | 真的共用 fragment（＝舊口徑的全部） |
| `paired_by_excerpt` | 90 | 同段落、引文互為子字串。內容到了論證層，只缺邊 |
| `same_paragraph_unpaired` | 170 | 同段落有 evidence，但看不出對應。需人工判斷 |
| `paragraph_has_no_evidence` | 115 | **該段落一條 evidence step 都沒有**。論證層有洞 |
| `no_anchor` | 0 | |

**內容真正進入論證層：145 / 430（33.7%）**，不是 12.8%。舊口徑低估了將近三倍。

依正規化後的 `observation_type`：

| 類別 | 條數 | 進論證層 | |
|---|---:|---:|---:|
| scripture_text | 97 | 48 | 49.5% |
| original_language | 131 | 48 | 36.6% |
| literary_context | 33 | 10 | 30.3% |
| historical_cultural | 55 | 11 | 20.0% |
| narrative_structure | 41 | 8 | 19.5% |
| literary_form | 25 | 2 | 8.0% |
| (未對映) | 48 | 18 | 37.5% |

### 對 issue #55 那兩個數字的修正

#55 記錄的是「原文觀察 36 條，只有 4 條進得去（11%）」。本工具在**相同口徑**（`observation_type` 字面等於 `original_language`、只算 `in_argument`）下重現了 4/36，兩者一致。

但那個口徑在兩端都失真：

- **分母太小。** 246 種 type 裡有 68 種在描述原文觀察。正規化後是 **131 條**，不是 36 條。
- **分子太小。** 那 36 條裡另有 13 條是 `paired_by_excerpt`——內容其實到了論證層。同口徑下應是 **17/36（47%）**。

正規化後的真實數字是 **48/131（36.6%）**。

## 2026-08-17 已套用的 type 遷移

change set `KCS-3a5dc2f24483efd3a7e1`，`updated: 315`。只改寫規則可判定（`CERTAIN`）且目前值與目標不同的記錄；已經是正確值的 67 筆不動，以免無謂 bump revision。48 筆判斷題原封不動留在 review queue。

每筆改寫都保留 `observation_type_original`，折疊可稽核，不銷毀任何資訊。

```
observation_type 取值數    246 → 26
  六類詞彙涵蓋              382 筆
  仍待人工判斷              48 筆（20 種值）

where observation_type='original_language'    36 → 131
```

覆蓋率在遷移前後都是 145/430（33.7%）——改標籤不該動到它，這是驗證遷移沒有副作用的不變量。

## 這個量測能證明什麼、不能證明什麼

四個狀態都是**結構**判斷，全部以「同一來源、同一段落」為範圍。這是它的邊界，讀數字時必須記住：

**`paragraph_has_no_evidence` 不等於「內容沒進論證層」。** 它只說這一段沒有產生 evidence step。教授常常在一段陳述事實，隔幾段才推出結論——太16:19 未來完成式就是這樣：

```
notes_manuscript:16章釋經
  OBS014  S0063   太16:19的ἔσται δεδεμένον被標識為未來完成式
  E016    S0068   未來完成式表示神在天上決定，彼得在地上執行 → 有 claim
```

隔五段。工具判為 gap，但論證層是完整的（這個點另外還在三份來源各被抽過一次，每份都有 evidence 與 claim）。Petros/petra、δεδεμένον 同樣如此。

因此 **`same_paragraph_unpaired` 170 與 `paragraph_has_no_evidence` 115 都是損失的上界**，抽驗顯示高估可能相當多。真實損失未知。

確認的損失目前只有一例：太16:23 的 φρονέω。教授的推論句（「耶穌責備彼得的，是他在思維與關注的方向上偏向人的意思」）以 `責備彼得`／`絆我腳`／`體貼` 逐字掃全庫為 0 筆，只存在於 `CP-matthew-16-21-23` 的 `required_argument_steps` 合約文字，claim 層沒有。#45 的手工提升補進去的是**字典釋義**，不是那句推論。

要把上界收成實數，必須改問語意問題——「這條觀察的內容，有沒有出現在論證層任何一條 claim 裡」——結構比對答不了。`extraction_gap_paragraphs` 是待判定清單，不是已確認的缺口清單。
