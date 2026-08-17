# 三篇已發布文章的 base contract 對母本覆蓋率（總表）

> 由 `python -m backend.pipeline.base_contract_coverage` 確定性產生。

| 文章 | contract | 經文 | required steps | 已成為 step | 範圍內未成為 step | 完全在範圍外 | 相關句合計 |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: |
| DRAFT-M16-001-V1 | `BMC-matt16-1-12-v1` | 太16:1-12 | 9 | 12 | 31 | 0 | 43 |
| DRAFT-M16-002-V1 | `BMC-matt16-13-20-v1` | 太16:13-20 | 14 | 18 | 26 | 53 | 97 |
| DRAFT-M16-003-V1 | `BMC-matt16-21-23-v1` | 太16:21-23 | 5 | 5 | 10 | 11 | 26 |

## 承重候選（未成為 step 的句子）

| 文章 | 範圍內未成為 step 且承重 | 範圍外且承重 |
| --- | ---: | ---: |
| DRAFT-M16-001-V1 | 10 | 0 |
| DRAFT-M16-002-V1 | 10 | 25 |
| DRAFT-M16-003-V1 | 6 | 3 |

## 方法

1. **母本範圍**：`base_source.path` 的 `final.md`，加上任何 required step 所引用的其他母本，
   再加上該文章 knowledge snapshot 內其餘 `notes_manuscript` 來源。
2. **相關段落判定（依經文引用，不依章節標題）**，依序套用：
   `required_step`（contract 自己已指認的段落）→ `direct`（段落含本篇範圍內的經文引用）→
   `heading`（所屬小標題含本篇引用）→ `continuation`（緊接相關段落、且自身完全沒有經文引用）→
   `section_dominant`（整個 `##` 章節的經文引用有 ≥60% 落在本篇範圍，且該小標題區塊沒有只引到別段經文）。
3. **契約範圍**＝`base_source.section_anchor` 指向的那一個 `##` 章節，僅適用於 base_source 母本。
4. **分類優先序**：對應到 required step → `已成為 required step`；否則在契約範圍內 → `在契約範圍內但未成為 step`；
   否則 → `完全在契約範圍外`。
5. **step 對應**：句子與 `source_excerpt` 正規化後互相包含即算對應，否則以最長共同子字串佔句子 ≥60% 計；
   片段短於 12 字不算；經文引用區塊（`>`）不算 step；一個 step 只認分數最高的那一個段落。
6. 承重候選：`原文觀察`（希臘／希伯來文字符或原文、語法、詞義等用語）、`交叉經文`（引到本篇範圍外的經文）、
   `推論橋梁`（因此／由此／正是因為 等推論連接詞）。

## 已知限制（讀數時請一併考慮）

- 字數統計不含 markdown 標題本身，因此比人工逐字計數略低。
- `section_dominant` 的 60% 門檻是人為選定的；章節內交叉經文較多時，母本中確實在解釋本篇、但完全沒有
  經文引用的段落可能被漏掉（偏保守）。
- `已成為 required step` 是「句」數而非「step」數：一條 step 的 `source_excerpt` 常橫跨母本兩句。
- 契約範圍只由 `base_source.section_anchor` 定義。若 required step 引用了 anchor 以外的段落
  （太16:13–20 即如此），那些句子會被歸為 `已成為 required step`，但其所在章節其餘內容仍算 `完全在契約範圍外`。
- 本工具只做確定性字串／引用比對，不判斷語意；`在契約範圍內但未成為 step` 不等於「應該成為 step」。

