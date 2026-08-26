# 文庫獨立完整性與正確性審計

> **讀者**：Developer
> **類型**：說明
> **狀態**：當前
> **與代碼對齊**：2026-08-26（核對到 `scripts/audit-library.py`）
> **權威範圍**：無。這條約束的權威在 [Solution Architecture 第 2 節](../../00-overview/solution_architecture.md#不通过独立审计就不再往下跑新的经文)；本文只說明怎麼跑、輸出長什麼樣。

報告本身**不進版本控制**：它逐條列出引文與主張原文，屬於教授材料，依專案規則不得進入 Git。

產出位置：`$DATA_BASE_DIR/wang-knowledge-platform/staging/reports/library-audit/<UTC 時間戳>/`

## 怎麼跑

```bash
scripts/audit-library.py                       # 四層全跑，抽 20 條主張、10 個觀點
scripts/audit-library.py --layers 1,2          # 只跑確定性的兩層，不呼叫 model
scripts/audit-library.py --scope all           # 連更早批次留下的來源一起查
scripts/audit-library.py --claims 40 --viewpoints 15 --seed 241
```

**預設只查這一輪的來源。** 庫裡有 35 份 source document，而目前這一輪 ingest 成功的
是 20 個來源（22 筆記錄，見下）——其餘 13 份是更早批次留下的羅馬書材料。把它們平均進
同一個比率，答出來的是沒人問的問題，而且會把要往下建的那一批和已經放下的那一批混在
一起。範圍由 `wang_knowledge.pipeline_runs` 裡 `stage='ingest' and status='succeeded'`
的 subject 決定。

這是全篇唯一一處審計採信流水線自己的紀錄，而且只採信到「**查哪些列**」為止；那些列對
不對，判斷一律仍來自磁碟上的原件。

用系統的 `python3` 就能跑，不需要 `backend/.venv`：整支程式只用標準函式庫，資料庫走
`psql` 子行程，model 走 `urllib`。這是刻意的——審計要有自己的代碼路徑，不 import
任何 `backend/` 模組，也不與流水線共用驅動程式或 SDK 的設定。

設定從環境變數讀，讀不到就退回 repo 根目錄的 `.env`：`KNOWLEDGE_DATABASE_URL`、
`DATA_BASE_DIR`、`GEMINI_API_KEY1`。

第 3、4 層用 `gemini-3.7-flash`。提議模型是 `gpt-5.6-sol`、複核模型是
`claude-opus-5`，兩者都參與過被審的判定，用它們任何一個等於自己審自己。`--model`
可以換，換掉之前先確認新的那個沒參與過原判定。

只讀。不寫入 PostgreSQL，不修改任何記錄。

## 四層查什麼

| 層 | 查什麼 | 方式 |
| --- | --- | --- |
| 1 逐字對得上 | 每條 `source_fragment` 的 `verbatim_excerpt`，在磁碟上的原件裡真的存在於所記位置 | 確定性，全查 |
| 2 覆蓋誠實 | 當前物件的依賴全部可解析；聲稱覆蓋的來源與實際用到的對得上 | 確定性，全查 |
| 3 主張站得住 | 這條主張能否從它所引的證據推出 | 抽樣 + 獨立 model |
| 4 觀點歸併對 | 判為同一觀點的主張，真值條件是否真的一致 | 抽樣 + 獨立 model |

三件事值得單獨說明，因為它們決定了比率的意義：

**第 1 層有兩個定位器，兩個都查。** 片段記了 `paragraph_key`（`S0016` 是原件的第 16
段）和 `source_segment_index`（逐字稿裡那一段自己的 `index` 欄位）。兩個都可能單獨過
期——來源重新校對之後段落數會變，序號跟著失效而 `index` 還指得對。片段自己帶的
`source_sha256` 只當成待查的宣稱，拿它跟磁碟上這份檔案現在的雜湊比。

**第 1 層照樣遵守刪除線。** 校對者刪字的方式是劃掉（`~~…~~`），不是刪掉。一條只存
在於劃掉的文字裡的引文，判 `deleted_text_only`，不算通過。

**第 4 層只問成員。** 只有 `equivalent_full` 與 `equivalent_component` 是身份，
`supports`／`qualifies`／`extends` 是關係（見 [CanonicalViewpoint 設計第 4
節](../../20-knowledge/canonical_viewpoint_design.md)）。拿一條 `qualifies` 去問「這
是不是同一個觀點」，model 當然說不是，而那是審計自己問錯了。

## 在哪裡看

`/admin/wang/library-audit`（導覽列「獨立審計」，#245）顯示**最新一輪**：四個比率、範
圍，以及分組後的待跟進條目。它只讀審計已經寫好的 `audit.json`，不代跑、不改任何記錄
——跑一次要呼叫模型、要花錢，那屬於 CLI。

沒跑過時它說「還沒有跑過」並給出指令，不顯示 0/0：一頁 0/0 讀起來像「沒問題」。

## 輸出

每次跑產生三個檔案：

```text
<時間戳>/
├── report.txt          # 比率，就是下面這張表的樣子
├── audit.json          # 每一條判定的完整記錄，可重放（同 --seed 抽到同一批）
└── human-queue.json    # 有異議的條目，D4 第 4 步的出口
```

`report.txt` 的第 1、2 層是比率，第 3、4 層是「N 條中 X 條有異議」。有異議的不會被
自動修掉，也不會被自動接受——它進 `human-queue.json`，等人判斷。

## 2026-08-26 的量測結果（僅數字，不含內容）

`--claims 20 --viewpoints 10 --seed 241`，範圍 `current-run`：22 份來源記錄、7,343 條
片段、1,365 條在範圍內的主張、31 個觀點。

| 層 | 結果 |
| --- | --- |
| 1 逐字對得上 | 7,333／7,343（99.9%）。10 條不通過的全部落在 `SRC-L3`——同一篇（五）3 的舊 pilot 記錄 |
| 2 覆蓋誠實 | 23,428／23,485 當前物件（99.8%）依賴全部可解析。47,852 條引用，97 條斷掉 |
| 3 主張抽樣 | 20 條中 3 條有異議，全部是 `overreach`（母體 1,365 條） |
| 4 觀點抽樣 | 10 個中 1 個有異議（`over_merge`，母體 12 個） |

第 2 層是全庫的，範圍縮不了：依賴本來就跨來源，一條指向別批物件的引用不會因為換個範圍
就不算斷。

五件值得看的：

**同一份逐字稿登記了兩次。**（五）3 同時是 `SRC-L3` 與 `SRC-2016_NYSC_3-f35be4755f9b`，
（四）4 同時是 `SRC-2016_NYSC_4-d05cd6cbe477` 與 `SRC-2016_NYSC_4-07c73c44dc11`。舊
pilot 的那一筆沒有 `source_path`，而**庫裡僅有的 10 條對不上的片段全在它底下**。重複
本身無害，但兩筆記錄各自帶錨點，覆蓋率與去重會把同一篇算兩次。

**97 條斷掉的引用全在同一批。** `knowledge_routes` 指向
`CP-RIGHTEOUSNESS-FAITH-ROMANS-VALIDATION-01-…` 的幾份 composition plan 與 decision，
而那幾份不在庫裡——同批的其他 plan 都在。routes 進了庫，它們指向的計劃沒進。

**2 條成分定位是拼接出來的。** 兩條 `equivalent_component` 連結宣稱等價的成分，不是主
張原文裡的一段連續文字，而是把不相鄰的兩截接起來的。抽取的規則本來就寫著「不能改字、
補標點或拼接」（`backend/pipeline/detailed_knowledge_extraction_runner.py:126`），觀點
成員資格就掛在那個拼出來的句子上。

**第 4 層那一條是 `over_merge`。** 觀點把太16:19 與太18:18、以及「譯法沒譯出未來完成
式」與「常見理解錯誤」合成一條，而各成員主張只涵蓋其中一半。這正是
[#232](https://github.com/junyang168/smart-answer/issues/232) 記過的那一類——差別是那
三條是人偶然發現的，這一條是跑出來的。

**896 條引用指向已經 retire 的物件。** 那些記錄還在庫裡，只是不再是當前版本。不算斷
掉，但也不是乾淨的引用，所以單獨計數。

報告另外列出**查無此物的 ID**（長得像物件 id、被引用得到、庫裡卻沒有對應 collection
的值；`occurrence_refs` 與 `occurrence_ref_id` 共 132 處指向 `OCC-…`，而沒有任何物件定
義它們），以及**撞號的路徑**（同一條路徑底下多數值解不開、少數解得開；少數那幾個是撞
號，不是引用）。
