# 系列單元切分與論證抽取兩階段流水線

> **讀者**：Developer、Solution architect
> **類型**：流程
> **狀態**：當前
> **與代碼對齊**：2026-11-26（核對到 `backend/pipeline/series_unit_pipeline.py`）
> **權威範圍**：`unit_argument_series` 從發布逐字稿到 `unit-argument.json`、Fidelity、Round-trip 校驗與 PostgreSQL 入庫的兩階段執行順序與模型分工。

## 1. 為什麼拆成兩階段

系列切分、單元規劃、論證抽取與盲測重構都需要讀取整套連續講道逐字稿，token 消耗較大；而 Fidelity 終審與 Round-trip 黑盒比對／歸因使用 Claude Opus。將流水線拆成 Phase A（純 GPT + Jev）與 Phase B（純 Claude Opus，**不用 Gemini**），可以先用 GPT 跑完所有重計算並產出預篩與盲測重構緩存，再按 Opus 額度節奏完成終審與入庫。

## 2. 階段劃分與模型約定

| 階段 | 命令參數 | 步驟 | 使用模型 | 產出檔案 |
| --- | --- | --- | --- | --- |
| Phase A | `--phase a` | Layer 1 系列切分（`series_unit_segmentation.py`） | `gpt-6.1-sol` | `segmentation.json`、`units/U*/unit-manifest.json` |
| Phase A | `--phase a` | 單元規劃與論證抽取（`claim_unit_plan.py`、`claim_unit_argument.py`） | `gpt-6.1-sol` | `unit-plan.json`、`unit-argument.json` |
| Phase A | `--phase a` | Fidelity 預篩（`argument_fidelity.py`） | Jev（`jev-1.14.0`） | `fidelity/prefilter.json` |
| Phase A | `--phase a` | Round-trip Stage 1 盲測重構（`argument_roundtrip_verify.py`） | `gpt-6.1-sol` | `roundtrip/02_blind_reconstruction.json` |
| Phase B | `--phase b` | Fidelity 終審（僅審 `prefilter.json` 標出條目） | `claude-opus-5-5` | `fidelity/fidelity-final.json`、`fidelity_summary.json` |
| Phase B | `--phase b` | Round-trip Stage 2 黑盒比對 & Stage 3 歸因（複用 Stage 1） | `claude-opus-5-5` | `roundtrip_diff.json`、`roundtrip_attribution.json` |
| 入庫 | `--load` | 寫入 PostgreSQL（`unit_argument_load.py`） | 無模型 | `wang_knowledge.*` |

## 3. 標準執行命令

僅跑 Phase A（不進資料庫，不消耗 Opus）：

```bash
PYTHONPATH=. backend/.venv/bin/python -m backend.pipeline.series_unit_pipeline \
  --series 2016-nysc-matthew-5 \
  --phase a
```

後續補跑 Phase B（僅用 Opus，不用 Gemini；加上 `--wait-on-quota` 可在遇 5 小時配額上限時自動休眠至重置後續跑）並入庫：

```bash
PYTHONPATH=. backend/.venv/bin/python -m backend.pipeline.series_unit_pipeline \
  --series 2016-nysc-matthew-5 \
  --phase b \
  --wait-on-quota \
  --load
```
