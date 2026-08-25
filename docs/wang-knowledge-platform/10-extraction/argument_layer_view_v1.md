# 論證層檢視 v1

一張圖，看一個來源的論證層長什麼樣。兩個入口，同一份資料。

## 後台頁面（即時）

`/admin/wang/argument-layer`，在 Wang 文庫的次導覽裡。直接讀 PostgreSQL，
不必產生任何檔案；權限走 `/admin` 既有的 next-auth（role 需為 editor 或 admin）。

後端是 `backend/api/argument_layer.py`：

| 端點 | 回傳 |
|---|---|
| `GET /admin/argument-layer/sources` | 24 個來源的計數，**不含教授原文**（約 8KB） |
| `GET /admin/argument-layer/sources/{key}` | 單一來源的完整論證層（中位數 57KB，最大 143KB） |
| `GET /admin/argument-layer/search?q=` | 跨來源找節點，回傳 kind、id、來源 |

整個語料的組裝結果會快取，以 `objects` 與 `edges` 的筆數與 `max(updated_at)`
當指紋，store 一改就重建，所以看到的不會是舊的。冷啟約 0.1 秒，之後 8 毫秒。

## 獨立檔案（快照）

```bash
export KNOWLEDGE_DATABASE_URL='postgresql:///smart_answer_knowledge'
export DATA_BASE_DIR=/opt/homebrew/var/www/church/web/data
PYTHONPATH=. .venv/bin/python -m backend.pipeline.argument_layer_view
```

產出 `$DATA_BASE_DIR/wang-knowledge-platform/staging/reports/argument-layer-view/argument-layer.html`，
單一自足檔案，瀏覽器直接開。加 `--json-output` 可同時輸出同一份資料的 JSON。

**產出含教授原文，依專案規則不進 Git**（先例：commit `9e914be`）。程式進 Git，產出不進。

兩個都留：獨立檔案是唯一不需要後端跑起來就能看的形式。

## 為什麼要另做一份

`/admin/thought-review` 的論證資料來自 `argument_graph.json`，而那份 JSON 是
`export_claim_argument_graph` 從手工撰寫的 `claim-graph.html` 解析出來的，只涵蓋
馬太福音釋經（五）第 3、4 講。PostgreSQL authoring store 現在有 24 個來源分組、
1020 個 evidence_step，工作台看不到其中大部分。

本檢視直接讀 store，不經過 staging JSON，因此顯示的就是審核權威資料的現況。

## 三個畫面

**全庫總覽**（進來就是這一頁）：24 個來源一列一列排開，每列給步驟／孤立／關係／主張／
問題／反方／觀察／觀察未入論證。「孤立」與「觀察未入論證」不是資料品質分數，
是還沒有人做過的判斷有多少。點任一列進入該來源。

**論證圖**：一個來源的一張圖，讀法見下。上方細帶是縮圖，整個來源的節點分布壓成一條，
點或拖曳即可跳到該處；泳道名稱固定在左欄，捲到最右邊也還看得到哪一列是結論。

**全庫搜尋**：搜尋框查的是 24 個來源的全部節點，不只當前這一個。點結果會切換到
對應來源、選取該節點並捲到它。

## 讀法

- **橫軸＝教授講述順序**。每個節點一欄，依 `source_fragments.paragraph_key` 排序。
  往右就是往後講。
- **縱軸＝論證動作**，五條泳道沿用手工那份圖的分法：問題・背景／經文證據／
  解經・推理／結論／神學・應用。143 個 pilot step 有 `argument_lane` 欄位可直接用；
  其餘 877 個由 `step_type` 推得，再沒有就看 `discourse_role`。
- **主張列在最上方**。一條 claim 畫在它最後一步證據的位置——教授走到結論的地方——
  底下的細線往左延伸到它第一步證據，所以「取材遍布全篇」和「取自相鄰三步」
  一眼可分。點主張才顯示它與各步驟的連線；全部同時畫會把論證埋掉。
- **觀察列在最下方**，與論證層分開。430 條 observation 只有 6 條有關係邊，
  所以這一列多半是一排沒有任何線連上去的卡片。這是 #55 的缺口在畫面上的樣子，
  不是畫面壞了。
- **紅框節點**是 `support_eligibility` 為 `withheld_*`／`context_only` 的步驟。
  依 `reviewer_ui_guide.md`，沒有合格錨點者不得作為論證證據，所以邊界畫在卡片上，
  不只寫在清單裡。

點任一節點，右欄顯示教授原話（含段落鍵與時間碼）、審核狀態、上下游關係與關係理由。

## 這是檢視，不是工作台

本頁不寫回任何資料：沒有批准、沒有 `record_review`、沒有 change set。
它回答的是「現在論證層長什麼樣」，不回答「誰決定了什麼」。
人工決策點的盤點見 #63。

`reviewer_ui_guide.md` 的界線在此照樣成立：未批准的主張右欄會標明
「批准只代表可在當前語料範圍內代表教授，不等於完成事實或神學核查」，
自動判定一律不呈現為人工批准。

## 已知限制

- 每個節點各佔一欄，時間軸因此很寬；50 個節點約 7000px，需橫向捲動或縮小。
  保留時間軸是刻意的：壓縮欄位會讓橫軸不再等於教授的講述順序。
- 來源分組靠 id 裡的 hash。三份手稿的 `source_id` 不含 hash，靠 fragment 反查標題。
  完全不指名來源的 6 筆記錄集中在「未歸屬來源」，不靜默丟掉。
- 跨來源的關係目前不畫。`knowledge_relations` 579 條全部是來源內部的邊；
  跨講關係走 `claim_relations`，不在本檢視範圍。
