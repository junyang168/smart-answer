# 王教授文庫 營運總表 v1

> 狀態：規格，尚未實作。程式在這份文件被人看過之後才動。
> 日期：2026-08-20（America/Chicago）
> 範圍：文庫這一條線——抽取 → 複審 → 仲裁 → 合併 → 入庫 → 文章。講道稿與筆記轉講稿不在內。
> 不重做「馬太進度」「論證層」「來源覆蓋」三頁；它們保留在原路由。

## 1. 為什麼要做

現在要回答「哪幾篇抽取過」，只能去掃 staging 目錄。掃出來的東西長這樣（2026-08-20 實測）：

- 28 個 `*.detailed-knowledge.json`，散在 **7 種不同的目錄佈局**裡：`detailed-extractions/`、`matthew-16-13-20-sources/detailed-extractions/`、`matthew-16-notes/`、`matthew-16-notes/v2-reextraction/`、`matthew-16-notes/v3-sections/`、`research-batches/<批次>/detailed-extractions/`、`transcript-sections/`。
- 這 28 個檔只對應 **19 篇**不同的來源。`2019_3_24_3_21_31` 同時躺在兩個 research batch 裡；`notes_manuscript_16` 有 3 個不同的 SHA、出現在 4 個地方。
- 其中只有 **2 篇**是現行流程（分段 + 逐句自檢）的產物——`2016_NYSC_3` 與 `notes_manuscript_16-72483dc200ad`，都是 2026-08-20 跑的。其餘 26 個包沒有 `sections`、沒有 `usage`、沒有 `sentence_exclusions` 這三個欄位，也就是說它們不知道自己是怎麼被切的、花了多少。
- 已發布文章 3 篇：`DRAFT-M16-001-V1`、`DRAFT-M16-002-V1`、`DRAFT-M16-003-V1`，全部在太 16。

跑過什麼、花了多少、有沒有失敗，沒有任何地方記著。`llm_usage.usage_summary` 只把數字 `print` 到 stdout，終端機關掉就沒了；而且它算的是 token，全庫沒有任何一行程式把 token 換算成錢。

所以第一件產出物不是頁面，是**執行記錄表**。頁面讀表；表由 runner 寫。

## 2. 六個階段，各自的權威

| 階段 | 誰產生 | 產出 | 目前的權威 |
| --- | --- | --- | --- |
| 抽取 | `detailed_knowledge_extraction_runner` | `<source_id>-<sha>.detailed-knowledge.json` | 檔案 |
| 複審 | `corpus_ai_review_runner` | `*.independent-review.json` | 檔案 |
| 仲裁 | `corpus_ai_adjudication_runner` | `*.adjudication.json` | 檔案 |
| 合併 | `knowledge_package_merge_runner` | 合併包 JSON | 檔案 |
| 入庫 | `knowledge_store_runner ingest-package --apply` | `wang_knowledge.change_sets` 一列 | **資料庫**（已經是可信的） |
| 文章 | `matthew_exposition_authoring_runner` | `repository/editorial_drafts/<draft_id>/` | 檔案 + publication decision |

入庫這一格今天就有真權威可讀：`change_sets` 記著 `package_id`（形如 `DETAILED-2016_NYSC_3-3d012c24a542`）、`status`、`applied_at`。其餘五格沒有。記錄表要補的就是這五格。

**記錄表不取代這些產出物。** 它記的是「誰在什麼時候跑了什麼、結果如何、花了多少」，產出物本身仍然是內容的權威。兩者對不上的時候，總表要說出來（見第 9 節）。

## 3. 執行記錄表

新增 migration `backend/api/canonical_repository/migrations/003_pipeline_runs.sql`，沿用既有機制：`wang_knowledge` schema、冪等 SQL、`migrate()` 每次重播全部檔案。

```sql
CREATE TABLE wang_knowledge.pipeline_runs (
    run_id            text PRIMARY KEY,          -- RUN-<26 碼>
    batch_id          text,                      -- 同一次勾選的多篇共用；CLI 為 NULL
    source_id         text NOT NULL,             -- 講道／母本，例：2016_NYSC_3
    stage             text NOT NULL              -- extraction | review | adjudication
                        CHECK (stage IN ('extraction','review','adjudication',
                                         'merge','ingest','article')),
    trigger           text NOT NULL CHECK (trigger IN ('cli','panel')),
    triggered_by      text,                      -- 面板：登入 email；CLI：$USER
    status            text NOT NULL              -- queued 只有面板會用
                        CHECK (status IN ('queued','running','succeeded',
                                          'failed','cancelled','interrupted')),
    started_at        timestamptz NOT NULL DEFAULT now(),
    finished_at       timestamptz,
    heartbeat_at      timestamptz NOT NULL DEFAULT now(),
    cancel_requested  boolean NOT NULL DEFAULT false,
    model_id          text,
    usage             jsonb NOT NULL DEFAULT '[]'::jsonb,  -- llm_usage.usage_row 的原樣陣列
    cost_usd          numeric(10,4),
    price_version     text,                      -- 算這筆錢時用的價目表版本
    input_sha256      jsonb NOT NULL DEFAULT '{}'::jsonb,  -- source/prompt/上游產出的指紋
    output_paths      text[] NOT NULL DEFAULT '{}',
    command           text,                      -- 重跑得出來的那一行
    error_message     text,
    metadata          jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX pipeline_runs_source_stage_idx
    ON wang_knowledge.pipeline_runs (source_id, stage, started_at DESC);
CREATE INDEX pipeline_runs_live_idx
    ON wang_knowledge.pipeline_runs (status, heartbeat_at)
    WHERE status IN ('queued','running');
```

**寫入的責任在 runner，不在 API。** 每個 runner 開工前寫 `running` 一列，每處理完一個段落更新 `heartbeat_at`，結束時寫 `succeeded` / `failed` 加上 `usage`、`cost_usd`、`output_paths`。CLI 跑的一樣寫——目前所有工作都是 CLI 跑的，只記面板觸發的話，總表會是空的而機器其實在忙。

作法是一個共用的 context manager，六個 runner 各加三行：

```python
with run_record(source_id="2016_NYSC_3", stage="extraction", trigger="cli") as run:
    ...
    run.usage(usage_rows)
    run.outputs(package_path)
```

`KNOWLEDGE_DATABASE_URL` 沒設或資料庫連不上時，`run_record` 印一行警告就放行，不讓記帳擋住幹活；那次執行不會出現在總表，這是**已知的說謊來源之一**，第 9 節列著。

**花費怎麼算。** 新增 `backend/pipeline/model_prices.py`：一份有日期的價目表，每個 model 三個數字（input / cached input / output，每百萬 token）。`cost_usd` 在寫入時算好並連同 `price_version` 存下來，之後改價不回頭改歷史。今天實測的兩次抽取，是價目表上線後第一批有錢的數字：

| 來源 | 呼叫 | 段落 | prompt | 其中 cached | completion |
| --- | --- | --- | --- | --- | --- |
| `2016_NYSC_3` | 4 | 3 | 81,133 | 9,051 | 90,644 |
| `notes_manuscript_16-72483dc200ad` | 4 | 4 | 29,596 | 23,943 | 59,785 |

## 4. 畫面一：總表（`/admin/wang` 首頁）

現在的首頁是四張連結卡，導覽列已經叫「總覽」卻沒有總覽的內容。改成表。四張卡縮成頁尾一行連結。

```
王教授文庫  [總覽] 馬太進度  論證層  來源覆蓋  出版單元
─────────────────────────────────────────────────────────────
系列 [全部 ▾]   狀態 [全部 ▾]   [ ] 只看有問題的        131 篇
─────────────────────────────────────────────────────────────
      來源                     抽取 複審 仲裁 合併 入庫 文章
 [ ]  011WSR01 馬太福音書釋經(1)  舊   舊   舊   ✗    ✗    ✗
 [ ]  2016_NYSC_1 …              舊   舊   舊   ✓    ✓    ✗
 [x]  2016_NYSC_3 …              ✓    ✗    ✗    ✗    ✓    ✗
 [ ]  notes_manuscript_16 …      ✓    舊   ✗    ✗    ✓    ✓
 [ ]  2019_3_31 …                失敗  ✗   ✗    ✗    ✗    ✗
                                 ⋮
─────────────────────────────────────────────────────────────
 已選 1 篇                                    [ 執行… ]
```

每一格四種狀態，定義如下——**這是整份規格最需要被挑錯的一段**：

| 顯示 | 意思 | 怎麼判定 |
| --- | --- | --- |
| ✓ | 有最新的成功結果 | 這一篇這一階段最後一列是 `succeeded`，而且 `input_sha256` 與現在重跑會得到的指紋相同 |
| 舊 | 有成功結果，但輸入或流程已經變了 | 最後一列是 `succeeded`，但 `input_sha256` 對不上：來源改過、prompt 改過，或那次執行根本沒有記指紋 |
| ✗ | 沒跑過 | 這一篇這一階段沒有任何 `succeeded` 的列 |
| 失敗 | 最後一次跑壞了 | 最後一列是 `failed` / `interrupted` / `cancelled`，不論之前有沒有成功過 |

「舊」不是灰色地帶，是**明確的待辦**：上面草圖裡 26 個舊包會全部落在這一格，因為它們沒有 `sections`，重跑一定得到不同的產出。

滑鼠移到一格上，浮出最後一次執行的時間、觸發方式與花費。點一格，進單篇詳情頁並捲到該階段。點來源名稱，進單篇詳情頁頂端。

篩選：系列（讀 `sermon_catalog.json` 的 `series_title`）、狀態（任一階段是失敗 / 有舊 / 全部完成 / 完全沒動）。「只看有問題的」＝任一階段是失敗，或有 `running` 但 `heartbeat_at` 已經超過十分鐘。

第一版**只讀**。勾選欄與 `[執行…]` 按鈕在第五步才出現。

API：`GET /admin/wang/operations/overview` → `{schema_version, generated_at, rows[], warnings[]}`。每次請求重算，不存第二份狀態。

## 5. 畫面二：執行記錄（`/admin/wang/operations/runs`）

```
篩選  階段 [全部 ▾]  狀態 [全部 ▾]  觸發 [全部 ▾]  最近 [7 天 ▾]
──────────────────────────────────────────────────────────────────
 開始時間          來源              階段  觸發  用時    花費   狀態
 08-20 03:50      2016_NYSC_3       抽取  cli   4m12s  $0.48  成功
 08-20 01:05      notes_manuscript…  抽取  cli   3m38s  $0.31  成功
 08-19 22:14      2019_3_31         抽取  cli   1m02s  $0.06  失敗
   └ DetailedExtractionValidationError: sentence S0142 …
──────────────────────────────────────────────────────────────────
                                       7 天合計  $12.40 · 26 次
```

一列一次執行，最新在上。失敗的列直接把 `error_message` 第一行攤開，不用點；點開才是全文加 `command`（那一行可以複製去重跑）。`running` 的列顯示已經跑了多久，`heartbeat_at` 超過十分鐘的加一個「可能已中斷」標記。

API：`GET /admin/wang/operations/runs?stage=&status=&trigger=&since=&limit=`。

## 6. 畫面三：選擇執行（總表上的動作，第五步才做）

勾選 → 選階段 → **先看試算，再決定**：

```
┌──────────────────────────────────────────────┐
│ 執行「複審」                                  │
│                                              │
│ 12 篇                                        │
│ 估計花費   $5.8 – $7.1                       │
│ 估計時間   約 50 分鐘（同時跑 2 篇）          │
│                                              │
│ 依據：最近 9 次複審，中位數 $0.53／篇，        │
│       依來源字數調整                          │
│                                              │
│ 其中 3 篇已經有最新結果，會被重跑。            │
│                                              │
│              [ 取消 ]  [ 確認開始 ]           │
└──────────────────────────────────────────────┘
```

試算取記錄表裡同一階段最近 20 次 `succeeded` 的 `cost_usd`，按來源字數線性調整，取中位數與四分位距當區間。歷史少於 3 次時，直接說「僅有 N 次紀錄，估計不可靠」，區間照給，確認照樣要按。

**上限**（後端擋，不是前端）：單一 batch 上限 20 篇；估計花費超過 $20 要在確認框輸入篇數才能送出；同時執行上限 2 個 run；當日累計花費超過 $50 拒絕新的 batch 並顯示為什麼。131 篇跑一輪複審約 $65，勾錯一次不該直接開跑。

確認後，API 為每篇寫一列 `queued` 就回應，不等執行。

## 7. 畫面四：單篇詳情（`/admin/wang/operations/sources/<source_id>`）

上半：這一篇是誰（標題、系列、經文、字數、來源檔路徑）＋ 六個階段的當前狀態，跟總表同一份判定。

下半：這一篇的全部執行記錄，按時間倒序，每列可展開成該次的 `usage`、`cost_usd`、`output_paths`、`command`、完整錯誤。產出檔路徑只顯示相對於 `DATA_BASE_DIR` 的部分，不把本機絕對路徑吐給瀏覽器。

## 8. 執行怎麼跑

**執行必須在 API 行程外。** 後端由 launchd 管（`com.smart_answer.fullarticleservice.plist`），`scripts/deploy.sh` 的 `restart_backend()` 是 `launchctl unload` 再 `load`。一次抽取 5–10 分鐘，跑在 API 行程內會被部署直接殺掉，錢花了還不留紀錄。現有 `series_index_refresh.py` 的記憶體 dict + `BackgroundTasks` 就是這個問題的活樣本：重啟之後連「跑過」都不知道。

作法：

1. API 只寫 `queued` 一列，然後回應。
2. 一個獨立的 worker 行程輪詢 `pipeline_runs` 裡的 `queued`，自己的 launchd plist（`com.smart_answer.wangworker`），**`deploy.sh` 不碰它**。部署時 worker 繼續跑手上那一篇，用的是舊 release 的程式碼——這是刻意的：中途換程式比跑完舊的更糟。
3. worker 每 30 秒更新 `heartbeat_at`。
4. 取消＝把 `cancel_requested` 設 true；worker 在段落之間檢查，收到就停在下一個段落邊界並寫 `cancelled`。不做強殺。
5. **中斷偵測**：任何一列 `running` 且 `heartbeat_at` 超過 10 分鐘，由 worker 啟動時與 overview API 讀取時判定為 `interrupted`，寫進 `error_message`。這樣「部署殺掉了一次執行」會變成表上看得見的一列，而不是永遠停在 `running`。

## 9. 它會在什麼情況下說謊

一份不肯講這一節的規格不值得批准。

1. **runner 沒寫記錄**——`KNOWLEDGE_DATABASE_URL` 沒設、資料庫連不上、或有人跑的是還沒接 `run_record` 的舊 runner。那次執行不存在於總表。緩解：overview API 比對 staging 目錄裡的產出檔，發現「有檔案但沒有對應的成功列」時，在頁面頂端掛一條 warning 說明有幾篇對不上；不會自己補一列假的。
2. **歷史補不回來**——今天這 28 個包、3 篇文章，記錄表裡沒有它們的執行歷史，也不打算捏造。第一版上線時，這些會顯示為「舊」（沒有指紋可比），而不是「✓」。表上第一天的 ✓ 只會有兩三個，這是正確的。
3. **指紋比對只看得到它記過的東西**——`input_sha256` 記哪幾個 SHA 是寫程式時決定的。漏記一個上游輸入，那個輸入變了也不會讓格子變「舊」。
4. **花費是算出來的，不是帳單**——`cost_usd` 是 token 乘價目表。API 端的 cache 計費規則變了、價目表沒跟上，數字就會偏，而且偏得很安靜。價目表要標日期，總表頁尾要顯示現行 `price_version` 與生效日。
5. **`interrupted` 是猜的**——heartbeat 停了 10 分鐘，可能是行程死了，也可能是一個 30 分鐘不回頭的 API 呼叫。這一列標成中斷可能是錯的；標成中斷的列不會被自動重跑。
6. **掃目錄的部分仍然在第 1 點裡**——warning 靠掃 staging，而 staging 有 7 種佈局。有人把批次寫進第 8 種目錄，warning 就會漏報。這正是總表本身不掃目錄的理由。

## 10. 順序與各步驟的驗收

| 步驟 | 做完的標準 |
| --- | --- |
| 1. 規格 | 這份文件被人看過並同意 |
| 2. 記錄表 | migration 上線；六個 runner 都寫；用 CLI 跑一次抽取，不做任何額外動作，它出現在表裡並帶花費 |
| 3. 總表（只讀） | 列出全部來源，六格狀態全部來自記錄表，不掃目錄；對不上的掛 warning |
| 4. 執行記錄 / 單篇詳情 | 失敗訊息看得到；`command` 複製得出來 |
| 5. 觸發執行 | 扣錢前看得到篇數／花費／時間；執行中部署一次，那次執行要嘛跑完要嘛被記成中斷；未登入的請求打不開執行 API |

只讀的部分先上線，是為了在按鈕出現之前，先確認表上的字是真的。

## 11. 權限

後端目前**沒有任何一個 route 要求管理員**。角色檢查只在 `web/src/app/admin/layout.tsx` 裡，是前端的，繞過它只要直接打後端。現在唯讀的頁面這樣還過得去；有了會花錢的按鈕就不行。

第五步之前必須落地：後端接受並驗證 session（cookie 已經由 Next.js 的 proxy route 轉過去了，後端現在只是不看），`/admin/wang/operations/*` 的所有寫入端點要求 `editor` 或 `admin`，未登入回 401、角色不足回 403。只讀端點同樣要求登入。

這件事的範圍超出本卡（後端從零開始做認證），但驗收第 6 條掛在這裡，所以要嘛在本卡做，要嘛拆一張卡並在本卡的 PR 裡指名它。

## 12. 未定：131 是哪 131 篇

卡上說總表 131 行，我今天量不出 131：

- `sermon_catalog.json` 有 **205** 筆 records；
- `source_coverage_catalog.py` 的註解說語料是 **203** 篇講道；
- `script_published/` 有 **115** 個已發布逐字稿；
- `notes_to_surmon/` 有 **35** 個帶 `unified_source.md` 的筆記專案；
- PostgreSQL 裡有 **25** 個 `source_documents`。

沒有哪一個組合等於 131。總表的行從哪裡來，是這一頁最根本的定義，也是驗收第 2 條的內容，所以在動程式之前要定下來。三個候選：

- **A**：`sermon_catalog.json` 全部 205 筆 + 35 個筆記母本＝240 行。最完整，也最多噪音。
- **B**：115 個已發布逐字稿 + 35 個筆記母本＝150 行。「有穩定可抽取原文」的才進表。
- **C**：另一個既有的名單——如果 131 是從某個地方數出來的，指給我看，照它做。

不做的事（照卡）：不做通用工作流引擎；不把編輯審核、改主張、改文章搬進來；不做自動排程，第一版全部由人按下去；不重做既有三頁。
