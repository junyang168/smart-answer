# 王教授文庫 營運總表 v1

> 狀態：規格，尚未實作。程式在這份文件被人看過之後才動。
> 日期：2026-08-20（America/Chicago），同日依負責人意見改版：講道與文章拆成兩條線、兩張表，每一格帶質量。
> 範圍：兩條獨立的線。講道線：抽取 → 複審 → 仲裁 → 合併 → 入庫。文章線：編排 → 寫作 → 編審 → 修訂 → 審計 → 出版 → 上線。講道稿、筆記轉講稿不在內。
> 不重做「馬太進度」「論證層」「來源覆蓋」三頁；它們保留在原路由。

## 1. 為什麼要做

現在要回答「哪幾篇抽取過」，只能去掃 staging 目錄。掃出來的東西長這樣（2026-08-20 實測）：

- 28 個 `*.detailed-knowledge.json`，散在 **7 種不同的目錄佈局**裡：`detailed-extractions/`、`matthew-16-13-20-sources/detailed-extractions/`、`matthew-16-notes/`、`matthew-16-notes/v2-reextraction/`、`matthew-16-notes/v3-sections/`、`research-batches/<批次>/detailed-extractions/`、`transcript-sections/`。
- 這 28 個檔只對應 **19 篇**不同的來源。`2019_3_24_3_21_31` 同時躺在兩個 research batch 裡；`notes_manuscript_16` 有 3 個不同的 SHA、出現在 4 個地方。
- 其中只有 **2 篇**是現行流程（分段 + 逐句自檢）的產物——`2016_NYSC_3` 與 `notes_manuscript_16-72483dc200ad`，都是 2026-08-20 跑的。其餘 26 個包沒有 `sections`、沒有 `usage`、沒有 `sentence_exclusions`，也就是說它們不知道自己是怎麼被切的、花了多少。
- 已發布文章 3 篇，都在太 16。

跑過什麼、花了多少、有沒有失敗，沒有任何地方記著。`llm_usage.usage_summary` 只把數字 `print` 到 stdout，終端機關掉就沒了；而且它算的是 token，全庫沒有任何一行程式把 token 換算成錢。

接下來的工作量把這個問題放大四十倍：**全庫兩百多篇都要走完講道線，之後在上面寫文章。** 所以第一件產出物不是頁面，是**執行記錄表**。頁面讀表；表由 runner 寫。

## 2. 兩條線，各自的階段與權威

卡上把六格畫成一列。負責人已改定（2026-08-20）：講道進知識庫與寫文章是**兩個獨立的 process**，講道與文章是**多對多**——太 16:13–20 那一篇文章引用了兩份筆記講稿加六篇講道；同一篇講道可以被好幾篇文章引用。一張表塞不下兩種單位，所以是兩張表，靠連結互指。

### 講道線（單位：一篇來源）

| 階段 | 誰產生 | 產出 | 目前的權威 |
| --- | --- | --- | --- |
| 抽取 | `detailed_knowledge_extraction_runner` | `<source_id>-<sha>.detailed-knowledge.json` | 檔案 |
| 複審 | `corpus_ai_review_runner` | `*.independent-review.json` | 檔案 |
| 仲裁 | `corpus_ai_adjudication_runner` | `*.adjudication.json` | 檔案 |
| 合併 | `knowledge_consensus_applier` | `*.reviewed-candidate.json` | 檔案 |
| 入庫 | `knowledge_store_runner ingest-package --apply` | `wang_knowledge.change_sets` 一列 | **資料庫**（已經是可信的） |

`knowledge_package_merge_runner` 不在這條線上：它做的是 research batch 的中立合併，單位是批次。合併這一格指的是把仲裁共識套回單篇候選包，產出之後入庫的 `*.reviewed-candidate.json`。

### 文章線（單位：一篇文章／編排計劃）

| 階段 | 誰產生 | 權威 |
| --- | --- | --- |
| 編排 | CompositionPlan + 雙模型審核 | PostgreSQL（今天 44 個計劃） |
| 寫作 | `matthew_exposition_authoring_runner` | runner 產物（manifest 綁 SHA） |
| 編審 | 同 runner 內的 Independent Editorial Review | EditorialReviewPacket |
| 修訂 | 每輪修訂恰好一次 Delta Review | Delta 產物 |
| 審計 | Program Audit | manifest `audit_config.audit_output_path` 指到的那份 |
| 出版 | publication decision（human / automated 分明） | manifest 綁定的 decision |
| 上線 | repository 發布 + production 投影 | `editorial_drafts/<draft_id>/` + 實際部署服務的 HTTP 投影 |

這條線的 read model **已經存在**：`matthew_exposition_progress` API 每次讀取時重算十個階段、編審分數、審計錯誤數、SHA 一致性與 blockers（設計見 `matthew_exposition_progress_design_v1.md`）。文章總表不重做它——讀同一個 read model，加上它沒有的東西：執行記錄、花費、觸發。深查一篇文章的完整性，仍去馬太進度頁。

**記錄表不取代這些產出物。** 它記的是「誰在什麼時候跑了什麼、結果如何、花了多少」，產出物本身仍然是內容的權威。兩者對不上的時候，總表要說出來（見第 11 節）。

## 3. 執行記錄表

新增 migration `backend/api/canonical_repository/migrations/003_pipeline_runs.sql`，沿用既有機制：`wang_knowledge` schema、冪等 SQL、`migrate()` 每次重播全部檔案。

```sql
CREATE TABLE wang_knowledge.pipeline_runs (
    run_id            text PRIMARY KEY,          -- RUN-<26 碼>
    batch_id          text,                      -- 同一次勾選的多篇共用；CLI 為 NULL
    subject_kind      text NOT NULL DEFAULT 'source'
                        CHECK (subject_kind IN ('source','draft','batch')),
    subject_id        text NOT NULL,             -- 來源 id；文章的 draft id；批次 id
    source_ids        text[] NOT NULL DEFAULT '{}',  -- 這次執行涉及的全部來源
    stage             text NOT NULL
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
    quality           jsonb NOT NULL DEFAULT '{}'::jsonb,  -- 該階段的質量摘要，見第 4 節
    input_sha256      jsonb NOT NULL DEFAULT '{}'::jsonb,  -- source/prompt/上游產出的指紋
    output_paths      text[] NOT NULL DEFAULT '{}',
    command           text,                      -- 重跑得出來的那一行
    error_message     text,
    metadata          jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX pipeline_runs_subject_stage_idx
    ON wang_knowledge.pipeline_runs (subject_id, stage, started_at DESC);
CREATE INDEX pipeline_runs_source_ids_idx
    ON wang_knowledge.pipeline_runs USING gin (source_ids);
CREATE INDEX pipeline_runs_live_idx
    ON wang_knowledge.pipeline_runs (status, heartbeat_at)
    WHERE status IN ('queued','running');
```

**寫入的責任在 runner，不在 API。** 每個 runner 開工前寫 `running` 一列，每處理完一個段落更新 `heartbeat_at`，結束時寫 `succeeded` / `failed` 加上 `usage`、`cost_usd`、`quality`、`output_paths`。CLI 跑的一樣寫——目前所有工作都是 CLI 跑的，只記面板觸發的話，總表會是空的而機器其實在忙。

作法是一個共用的 context manager，每個 runner 各加幾行：

```python
with run_record(subject="2016_NYSC_3", stage="extraction", trigger="cli") as run:
    ...
    run.usage(usage_rows)
    run.quality({"sentences": 132, "represented": 121, "excluded": 7, "unprocessed": 4})
    run.outputs(package_path)
```

`KNOWLEDGE_DATABASE_URL` 沒設或資料庫連不上時，`run_record` 印一行警告就放行，不讓記帳擋住幹活；那次執行不會出現在總表，這是**已知的說謊來源之一**，第 11 節列著。

抽取這類單來源階段，`subject_id` 就是來源 id，`source_ids` 是單元素陣列。文章執行一列的 `subject_id` 是 draft id，`source_ids` 列出它引用的全部來源。合併與入庫不叫模型，`cost_usd` 記 0，不留 NULL——「沒花錢」和「沒記到」必須是兩個不同的值。

**花費怎麼算。** 新增 `backend/pipeline/model_prices.py`：一份有日期的價目表，每個 model 三個數字（input / cached input / output，每百萬 token）。`cost_usd` 在寫入時算好並連同 `price_version` 存下來，之後改價不回頭改歷史。今天實測的兩次抽取，是價目表上線後第一批有錢的數字：

| 來源 | 呼叫 | 段落 | prompt | 其中 cached | completion |
| --- | --- | --- | --- | --- | --- |
| `2016_NYSC_3` | 4 | 3 | 81,133 | 9,051 | 90,644 |
| `notes_manuscript_16-72483dc200ad` | 4 | 4 | 29,596 | 23,943 | 59,785 |

## 4. 每一格帶質量，不只帶勾

「跑完了」和「跑出來的東西能用」是兩件事。一格只有 ✓ 的表，回答不了「這 240 個 ✓ 裡哪些其實該重跑」。所以每一格顯示**一個質量數字**，展開看全貌。數字不是新發明的分數——每個階段的產出物裡已經有現成的、可覆核的量：

| 階段 | 格子上顯示 | 數字從哪來 |
| --- | --- | --- |
| 抽取 | 已交代句子比例，如 `✓ 97%`；未交代句數大於 0 時旁註 `4 未答` | 逐句對帳（`sentence_ledger_runner`）：`sentences` / `represented` / `excluded` / `unprocessed`。分母是母本的句子，不是抽取的產出——抽取自己數自己永遠是滿分 |
| 複審 | `14/17 過，3 送仲裁` | independent-review 的 `routing_summary`：`ai_reviewed` / `awaiting_openai_adjudication` / `human_spot_check` |
| 仲裁 | `3 修正，0 人工` | adjudication 的 `summary`：`auto_applied` / `withdrawn` / `human_confirmation_required` / `human_disagreement_required`。人工分歧不是壞事，但必須看得見 |
| 合併 | 套用與拒絕的修正數 | reviewed-candidate 的套用記錄 |
| 入庫 | `59 新增、8 更新`；`already_applied` 顯示「無變化」 | `change_sets.summary` |
| 編審（文章） | 每維度是否達標 + hard gate，如 `10/10 維度過線`。**不顯示 total_score 當主數字**——一個總分讓弱項被強項扛過去，這正是 quality profile 廢掉總分門檻的原因 | EditorialReviewPacket，經 matthew-progress read model |
| 審計（文章） | `0 錯 2 警`，警告點開列明 | Program Audit，經 read model |
| 出版（文章） | `人工` / `自動`，加 SHA 一致性 | publication decision + read model 的 `sha_integrity` |

講道線的質量數字由 runner 在收工時寫進 `pipeline_runs.quality`——是**當時的快照**，跟著那次執行走。文章線的質量不進記錄表：matthew-progress read model 每次讀取時從產出物重算，那是它的設計，文章總表直接引用它，不存第二份。

## 5. 畫面一：講道總表（`/admin/wang` 首頁）

現在的首頁是四張連結卡，導覽列已經叫「總覽」卻沒有總覽的內容。改成表。四張卡縮成頁尾一行連結。

行的全集是 **240**：`sermon_catalog.json` 的 205 篇講道，加上 `notes_to_surmon/` 裡 35 個帶 `unified_source.md` 的筆記母本。其中約 90 篇講道還沒有已發布逐字稿，沒有可抽取的穩定原文——這些列**照樣在表上**，抽取格顯示「無原文」。藏掉它們，表就從「全庫的工作佇列」退化成「已經動過的那部分的進度條」。

```
王教授文庫  [總覽] 文章  馬太進度  論證層  來源覆蓋
─────────────────────────────────────────────────────────────────────
系列 [全部 ▾]  狀態 [全部 ▾]  [ ] 只看有問題的               240 篇
─────────────────────────────────────────────────────────────────────
      來源                      抽取        複審      仲裁     合併  入庫       文章
 [ ]  2016_NYSC_1 …             舊          舊 14/17  舊 3修正  ✗    ✓ 無變化   1 篇
 [x]  2016_NYSC_3 …             ✓ 96%·4未答 ✗         ✗        ✗    ✓ 71物件   –
 [ ]  notes_manuscript_16 …     ✓ 97%       ✗         ✗        ✗    ✓ 132物件  2 篇
 [ ]  2019_3_31 …               失敗        ✗         ✗        ✗    ✗          –
 [ ]  190609 …                  無原文      –         –        –    –          –
                                 ⋮
─────────────────────────────────────────────────────────────────────
 已選 1 篇                                              [ 執行… ]
```

五個階段格，每格四種狀態加一個質量數字：

| 顯示 | 意思 | 怎麼判定 |
| --- | --- | --- |
| ✓ | 有最新的成功結果 | 這一篇這一階段最後一列是 `succeeded`，而且 `input_sha256` 與現在重跑會得到的指紋相同 |
| 舊 | 有成功結果，但輸入或流程已經變了 | 最後一列是 `succeeded`，但 `input_sha256` 對不上：來源改過、prompt 改過，或那次執行根本沒有記指紋 |
| ✗ | 沒跑過 | 這一篇這一階段沒有任何 `succeeded` 的列 |
| 失敗 | 最後一次跑壞了 | 最後一列是 `failed` / `interrupted` / `cancelled`，不論之前有沒有成功過 |
| 無原文 | 還不能跑 | 只出現在抽取格：講道沒有已發布逐字稿，或母本專案沒有 `unified_source.md` |

「舊」不是灰色地帶，是**明確的待辦**：上面草圖裡 26 個舊包會全部落在這一格，因為它們沒有 `sections`，重跑一定得到不同的產出。「舊」的格子照樣顯示當時的質量數字，但變灰——數字是真的，只是對著舊的輸入。

**文章欄不是階段格**，是多對多關係的另一端：顯示「這篇來源被幾篇文章引用」（從文章的 `source_ids` 反查），點過去是文章總表篩到這幾篇。0 篇顯示「–」，是常態不是欠帳——文章按經文段落寫，一輪全庫抽取不會讓 205 篇各得一篇文章。

滑鼠移到一格上，浮出最後一次執行的時間、觸發方式與花費；點一格進單篇詳情並捲到該階段。篩選：系列（讀 `series_title`）、狀態（任一階段失敗 / 有舊 / 講道線全完成 / 完全沒動）。「只看有問題的」＝任一階段失敗，或有 `running` 但 `heartbeat_at` 超過十分鐘。

第一版**只讀**。勾選欄與 `[執行…]` 按鈕在觸發那一步才出現。

API：`GET /admin/wang/operations/overview` → `{schema_version, generated_at, rows[], warnings[]}`。每次請求重算，不存第二份狀態。

## 6. 畫面二：文章總表（`/admin/wang/operations/articles`）

一列一篇文章（或還沒有 draft 的編排計劃）。階段、質量、blockers 讀 matthew-progress read model；執行與花費讀記錄表；「生成文章」按鈕在這一頁。

```
 文章／計劃                    經文        目前階段  編審        審計      出版   上線  花費累計
 看見神蹟，卻仍未明白基督       太16:1–12   線上可見  10/10 過線  0錯2警    自動   ✓     $3.2
 磐石與鑰匙（DRAFT-M16-003）   太16:13–20  線上可見  10/10 過線  0錯2警    自動   ✓     $4.1
 CP-matthew-16-21-23           太16:21–23  編排就緒  –           –         –      –     –
                                 ⋮
```

點一列展開：十個階段的鏈（複用 matthew-progress 的階段模型）、每階段的質量、這一篇的執行記錄、**引用的來源清單**（多對多的另一端，每個來源連回講道總表的那一列，並顯示該來源講道線的狀態——文章引用了一篇「舊」抽取的來源，這裡要看得出來）。深查 SHA 鏈與 artifacts，連去馬太進度頁，不在這裡重做。

### 生成文章

挑一個計劃 → 同一種試算確認框 → worker 以固定的命令模板啟動：

```
matthew_exposition_authoring_runner --plan-id <計劃> \
  --publication-profile <config> --quality-profile <config> \
  --program-audit-manifest … --max-revision-rounds 2 --max-grounding-attempts 4
```

profile 路徑與固定參數放後端 config；面板只能挑計劃，**不能組參數**——能改參數的面板遲早會被用來繞掉 quality profile。

三件面板管不了、也不該管的事：

1. **寫作迴圈是 runner 的，不是面板的。** 一次初審、每輪修訂恰好一次 Delta Review、Program Audit、自動出版決定——這條不變量在 runner 裡（AGENTS.md 是它的權威）。面板只負責啟動和記錄，記錄裡的自動出版決定**永遠不標成人工批准**。寫入文庫也不是部署。
2. **「這個計劃可以寫了嗎」是人的判斷。** 現行流程要求先在計劃上確認 authoring contract。啟動器不計算 readiness、不擋「還沒準備好」的計劃——它算不準，算不準的門檻只會教人繞過它。它顯示計劃有什麼（包括引用來源的講道線狀態），按下去的責任在按的人。
3. **文章執行第一版不能取消。** 迴圈的每一階段都綁著前一階段的 SHA，中途殺掉留下的是半份 draft 加一堆對不上的審核記錄。`cancel_requested` 對 `article` 列不生效，UI 直說。

文章執行寫進同一張記錄表：`subject_kind='draft'`、`source_ids` 列全部引用來源、費用照價目表算。試算在有歷史之前會說「僅有 N 次紀錄，估計不可靠」——文章是最貴的階段，這句話在前幾次會一直在。

## 7. 畫面三：執行記錄（`/admin/wang/operations/runs`）

```
篩選  階段 [全部 ▾]  狀態 [全部 ▾]  觸發 [全部 ▾]  最近 [7 天 ▾]
──────────────────────────────────────────────────────────────────
 開始時間          對象               階段  觸發  用時    花費   狀態
 08-20 03:50      2016_NYSC_3        抽取  cli   4m12s  $0.48  成功
 08-20 01:05      notes_manuscript…   抽取  cli   3m38s  $0.31  成功
 08-19 22:14      2019_3_31          抽取  cli   1m02s  $0.06  失敗
   └ DetailedExtractionValidationError: sentence S0142 …
──────────────────────────────────────────────────────────────────
                                       7 天合計  $12.40 · 26 次
```

一列一次執行（講道與文章同表，對象欄顯示來源 id 或 draft id），最新在上。失敗的列直接把 `error_message` 第一行攤開，不用點；點開才是全文加 `command`（那一行可以複製去重跑）。`running` 的列顯示已經跑了多久，`heartbeat_at` 超過十分鐘的加一個「可能已中斷」標記。

API：`GET /admin/wang/operations/runs?stage=&status=&trigger=&since=&limit=`。

## 8. 畫面四：選擇執行（講道總表上的批次動作，觸發那一步才做）

勾選 → 選「跑到哪一階段為止」→ **先看試算，再決定**。每一篇從它缺的第一個階段接著跑，到指定的終點為止；已經是 ✓ 的階段預設跳過，要重跑得另外勾「連 ✓ 也重跑」。順序固定（抽取→複審→仲裁→合併→入庫），這不是工作流引擎，只是把固定的鏈條一次按完——兩百多篇一輪五個階段，逐階段手按是分批數乘五次，鏈起來是一次。文章不在鏈裡：它在文章總表，見第 6 節。

```
┌──────────────────────────────────────────────┐
│ 執行到「入庫」為止                            │
│                                              │
│ 12 篇 · 缺的階段合計 31 個                    │
│   抽取 3 · 複審 12 · 仲裁 12 · 合併 2 · 入庫 2│
│ 估計花費   $7.4 – $9.0（合併、入庫 $0）       │
│ 估計時間   約 2 小時（同時跑 2 篇）           │
│                                              │
│ 依據：抽取 11 次中位數 $0.42／篇、複審 9 次    │
│       中位數 $0.53／篇，依來源字數調整         │
│                                              │
│ 已是 ✓ 的階段跳過；要重跑得回去另外勾。        │
│                                              │
│              [ 取消 ]  [ 確認開始 ]           │
└──────────────────────────────────────────────┘
```

試算按階段分開：抽取、複審、仲裁取記錄表裡同階段最近 20 次 `succeeded` 的 `cost_usd`，按來源字數線性調整，取中位數與四分位距當區間；合併、入庫不叫模型，直接顯示 $0，不外推。歷史少於 3 次時，直接說「僅有 N 次紀錄，估計不可靠」，區間照給，確認照樣要按。

**上限**（後端擋，不是前端）。上限是防呆，不是產能：全庫 240 篇跑一輪抽取按今天的兩次實測估 $80–120，這是這個面板要支撐的**正常工作**，上限不能把它變成不可能。所以不設「單一 batch 最多 N 篇」——全庫一次是合法的 batch。防呆用確認的力度分級：估計花費超過 $20，確認框要求輸入篇數；超過 $100，要求輸入估計金額本身；當日累計超過 $150 拒絕新 batch 並顯示為什麼。三個門檻都是後端 config，改門檻不用改程式。同時執行上限見第 10 節。勾錯一次不該直接開跑，但按一次該能跑完全庫。

確認後，API 只為每篇寫下**第一個缺的階段**的 `queued` 一列（batch 的 metadata 記著終點階段），就回應。後續階段由 worker 在前一階段成功後接著排——佇列裡不該出現一列「複審還沒跑就排好的仲裁」；前一階段失敗，這一篇的鏈就停在那裡，錯誤顯示在執行記錄。

## 9. 畫面五：詳情

**單講道**（`/admin/wang/operations/sources/<source_id>`）：上半是這一篇是誰（標題、系列、經文、字數、來源檔路徑）＋ 五個階段的當前狀態與質量，跟總表同一份判定；加上**引用它的文章**清單。下半是這一篇的全部執行記錄，按時間倒序，每列可展開成該次的 `usage`、`cost_usd`、`quality`、`output_paths`、`command`、完整錯誤。產出檔路徑只顯示相對於 `DATA_BASE_DIR` 的部分，不把本機絕對路徑吐給瀏覽器。

**單文章**：文章總表的展開列（第 6 節）就是它，第一版不另做獨立路由；深查去馬太進度。

## 10. 執行怎麼跑

**執行必須在 API 行程外。** 後端由 launchd 管（`com.smart_answer.fullarticleservice.plist`），`scripts/deploy.sh` 的 `restart_backend()` 是 `launchctl unload` 再 `load`。一次抽取 5–10 分鐘，跑在 API 行程內會被部署直接殺掉，錢花了還不留紀錄。現有 `series_index_refresh.py` 的記憶體 dict + `BackgroundTasks` 就是這個問題的活樣本：重啟之後連「跑過」都不知道。

作法：

1. API 只寫 `queued` 一列，然後回應。
2. 一個獨立的 worker 行程輪詢 `pipeline_runs` 裡的 `queued`，自己的 launchd plist（`com.smart_answer.wangworker`），**`deploy.sh` 不碰它**。部署時 worker 繼續跑手上那一篇，用的是舊 release 的程式碼——這是刻意的：中途換程式比跑完舊的更糟。同時跑的篇數預設 2、是 config；全庫一輪抽取在併發 2 下約 10–17 小時，worker 要能跨夜、跨多次部署把佇列吃完。真正的天花板是供應商的 rate limit：撞到 429 就退避重試，不記成失敗。
3. worker 每 30 秒更新 `heartbeat_at`。
4. 取消＝把 `cancel_requested` 設 true；worker 在段落之間檢查，收到就停在下一個段落邊界並寫 `cancelled`。不做強殺。文章執行不支援取消（第 6 節）。
5. **中斷偵測**：任何一列 `running` 且 `heartbeat_at` 超過 10 分鐘，由 worker 啟動時與 overview API 讀取時判定為 `interrupted`，寫進 `error_message`。這樣「部署殺掉了一次執行」會變成表上看得見的一列，而不是永遠停在 `running`。

## 11. 它會在什麼情況下說謊

一份不肯講這一節的規格不值得批准。

1. **runner 沒寫記錄**——`KNOWLEDGE_DATABASE_URL` 沒設、資料庫連不上、或有人跑的是還沒接 `run_record` 的舊 runner。那次執行不存在於總表。緩解：overview API 比對 staging 目錄裡的產出檔，發現「有檔案但沒有對應的成功列」時，在頁面頂端掛一條 warning 說明有幾篇對不上；不會自己補一列假的。
2. **歷史補不回來**——今天這 28 個包、3 篇文章，記錄表裡沒有它們的執行歷史，也不打算捏造。第一版上線時，這些會顯示為「舊」（沒有指紋可比），而不是「✓」。講道總表第一天的 ✓ 只會有兩三個，這是正確的。
3. **指紋比對只看得到它記過的東西**——`input_sha256` 記哪幾個 SHA 是寫程式時決定的。漏記一個上游輸入，那個輸入變了也不會讓格子變「舊」。
4. **花費是算出來的，不是帳單**——`cost_usd` 是 token 乘價目表。API 端的 cache 計費規則變了、價目表沒跟上，數字就會偏，而且偏得很安靜。價目表要標日期，總表頁尾要顯示現行 `price_version` 與生效日。
5. **質量數字是收工時的快照**——講道線的 `quality` 跟著那次執行寫死。之後有人手改產出物、或人工批准了幾條 exclusion，格子上的數字不會自己動；要新數字就重跑對帳。單篇詳情提供「重算對帳」讓數字跟上，但那是人按的，不是自動的。
6. **`interrupted` 是猜的**——heartbeat 停了 10 分鐘，可能是行程死了，也可能是一個 30 分鐘不回頭的 API 呼叫。這一列標成中斷可能是錯的；標成中斷的列不會被自動重跑。
7. **掃目錄的部分仍然在第 1 點裡**——warning 靠掃 staging，而 staging 有 7 種佈局。有人把批次寫進第 8 種目錄，warning 就會漏報。這正是總表本身不掃目錄的理由。

## 12. 順序與各步驟的驗收

| 步驟 | 做完的標準 |
| --- | --- |
| 1. 規格 | 這份文件被人看過並同意 |
| 2. 記錄表 | migration 上線；講道線五個 runner 加 authoring runner 都寫；用 CLI 跑一次抽取，不做任何額外動作，它出現在表裡並帶花費與質量 |
| 3. 講道總表（只讀） | 240 行，五格狀態與質量全部來自記錄表，不掃目錄；文章欄反查得出引用數；對不上的掛 warning |
| 4. 文章總表（只讀） | 每篇文章的階段、編審維度達標、審計錯誤警告數、出版決定種類看得到；展開列出引用來源及其講道線狀態 |
| 5. 執行記錄 / 單篇詳情 | 失敗訊息看得到；`command` 複製得出來；質量數字點得開 |
| 6. 觸發執行 | 扣錢前看得到篇數／花費／時間；執行中部署一次，那次執行要嘛跑完要嘛被記成中斷；未登入的請求打不開執行 API；從文章總表挑一個計劃生成一次文章，它帶著花費出現在執行記錄裡 |

只讀的部分先上線，是為了在按鈕出現之前，先確認表上的字是真的。

## 13. 權限（本卡內做，觸發上線之前落地）

後端目前**沒有任何一個 route 要求管理員**。角色檢查只在 `web/src/app/admin/layout.tsx`，是前端的；Next.js 的 proxy route 已經把 cookie 轉給後端，後端只是不看。唯讀頁面這樣還過得去，會花錢的按鈕不行。

不讓 Python 去解 next-auth 的 cookie。session 是 next-auth 的 JWT（JWE 加密），在後端重實作解密等於把 FastAPI 綁死在 next-auth 的版本內部。改用一層短命的簽名 header：

1. `/api/admin/wang/operations/*` 的 Next proxy route 先 `getServerSession()`。沒登入回 401，角色不是 `editor` / `admin` 回 403，**請求根本不轉發**。
2. 通過的請求，proxy 簽一個 `X-Wang-Operator` header：`{email, role, exp}`，HMAC-SHA256，密鑰 `WANG_OPERATIONS_SECRET`（`.env`，前後端共用），`exp` 60 秒。
3. 後端一個 FastAPI dependency 掛在全部 `/admin/wang/operations/*` route 上：驗簽、驗 `exp`，寫入端點另驗 `role`。沒有 header 或驗不過 → 401/403。直接打 8555 而不知道密鑰的請求，讀寫都進不來。
4. `triggered_by` 取自驗過簽的 email，不收 request body 裡自報的身分。

範圍界線：這層只保護 operations API，不回頭給既有的其他後端 route 補權限——那是另一張卡。但 dependency 寫成可以被其他 router 直接掛用的形狀。

## 14. 行的全集

**講道總表：240（已定）。** 卡上寫 131 行，量不出來：`sermon_catalog.json` 有 205 筆、`source_coverage_catalog.py` 說語料 203 篇、`script_published/` 有 115 個逐字稿、`notes_to_surmon/` 有 35 個帶 `unified_source.md` 的母本、PostgreSQL 裡有 25 個 `source_documents`——沒有一個組合等於 131。負責人已定案：全庫都要 ingest。所以全集取最完整的一份——205 篇講道加 35 個母本，讀 `sermon_catalog.json` 與 `notes_to_surmon/*/meta.json`，兩者都是既有的可重跑 read model，不新造名單。90 篇還沒有逐字稿的講道以「無原文」留在表上（第 5 節），因為「要先把原文弄出來」也是這張表要排的工作。

**文章總表：計劃加文章。** PostgreSQL 裡的 CompositionPlan（今天 44 個）加上已有 draft 的文章（今天 3 篇，都有計劃）。計劃是文章的起點，還沒寫的計劃就是文章線的「✗」，跟講道線的 90 篇「無原文」同一個道理：還沒動的工作要在表上，不然沒人去動。

不做的事（照卡）：不做通用工作流引擎；不把編輯審核、改主張、改文章搬進來；不做自動排程，第一版全部由人按下去；不重做「馬太進度」「論證層」「來源覆蓋」。
