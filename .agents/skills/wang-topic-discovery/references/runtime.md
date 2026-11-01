# 現有入口與恢復

以下為 #531 建立技能時核對的接點；執行時讀所用 checkout 的實作，不把本頁當成獨立 policy。

## 入口與材料

- `backend/pipeline/topic_discovery.py`：`Corpus.load`、`candidates`、`Discovery.run`、CLI。
- `backend/pipeline/topic_cvp_pilot.py`：共享 `PROPOSER`、`call_model`、request-byte 上限。
- `backend/api/config.py`：`WANG_STAGING_DIR`，會載入環境設定；不要輸出 `.env` 或連線秘密。
- #517 初始產物：`WANG_STAGING_DIR/topic-survey/matthew-01/README.md` 及 `readers/`、`readers-exegesis-r2/`、`readers-theology-r3/`、`readers-r4/`。README 有多輪與同題合併說明，seed IDs 在讀者詳稿。
- discovery 產物：`WANG_STAGING_DIR/topic-discovery/<topic>-<run-id>/`。每個 campaign／topic 使用自己可識別的 run-id，不覆寫別的 session。

在授權 worktree 的 repo root 使用 `backend/.venv/bin/python`：

```bash
backend/.venv/bin/python -m backend.pipeline.topic_discovery --help
backend/.venv/bin/python -m backend.pipeline.topic_discovery '問題名稱' \
  --seed '已核實的Claim-ID' --seed '另一來源的Claim-ID' \
  --term '具體別名' --dry-run
```

`--dry-run` 只讀資料庫並列第一輪候選**數量**，沒有執行語義判定，也不是完整成員預覽。正式執行去掉 `--dry-run`，加 `--run-id '本次唯一編號'`。`--term` 可不給。`--other-topic` 只供確有區別的鄰接問題；它會讓主要回答該問題的候選進 borderline，不能拿來強制全庫互斥。

## 模型與粒度

當前 `PROPOSER = ("codex", "gpt-6.1-sol")`，走 Codex 訂閱 CLI；#517 的 Claude Code 會話不是這個判定模型。使用實際現行配置，不因本技能硬改模型／effort，不自動切 API 或新增 reviewer。

候選來自五類連接：同篇 Claim 關係、同／相鄰原文段落、同 CVP 連接、相關 CVP 的 Claims、重疊經文；第一輪可加主題詞／別名。後三種圖路徑可跨講道。圖路徑與詞只是候選入口，判定讀 statement、證據及相關原文塊。

目前分片目標 200,000 bytes；共享原文塊去重，允許一次追加閱讀，最多 6 塊。候選不截斷；超大單項／最終序列化超限要定位 packet 問題，不能刪材料湊大小。90 條只提示，不是 group 硬上限；10 輪保護停下來標為未收斂，不報完成，也不靠另開同樣 run 無限重試。

源檔變動時現有程式可能只用已存 evidence 判定，結果的 `sources_changed` 必須保留。`on_hold` 也不等於可交 CVP 寫入；內容分組與上游資格分開。

## 原生產物與接續限制

- `result.json`：`seeds`、`accepted`、`borderline`、按 Claim ID 的 `verdicts`、`rounds`、`stop`、`sources_changed`。seeds 直接收入，沒有候選 verdict。
- `claims.json`：收入的 Claim ID JSON array，可供後續 CVP 使用；不含上游合格證明。
- `report.md`、`borderline.md`：人工可讀報告，邊緣表供相鄰問題起步。
- `round-N/shard-K/`：request、system、raw、response；追加閱讀另有 reread 目錄。原始回答不手改。

現有 CLI 沒有全庫 scheduler、append-seed 或真正 checkpoint resume。重用同一 run-id 是從頭重建遍歷，再由 `call_model` 對完全相同 payload 使用已有 response。現有快取比對**未包含 system prompt／schema／model**：重用前核實程式版本、prompt、schema、模型與來源／Claim 依據未變；變更後另建有前驅記錄的 run，不以同 payload 誤用舊語義判定。

已有完整有效結果直接復用，不重跑。程序慢或無新日誌時先查活躍 CLI 與保存回答，不發第二份。當前 429 處理會等待 600 秒後重試一次；持久後台運行時監看，不在交互工具中阻塞等待十分鐘。再次拒絕留失敗與接續點，按實際供應商恢復情況續跑；不換帳號、換模型或重設目錄繞限制。

多主題可共用只讀資料但使用不同輸出目錄；只有本次授權並行才開多 worker，避免與其他 session 爭用訂閱容量。建立 skill 不構成啟動並行或全庫運行的指令。
