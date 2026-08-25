# base contract 對母本覆蓋率

報告本身**不進版本控制**：它逐句列出母本內容，屬於教授材料，依專案規則不得進入 Git。

產出位置：`$DATA_BASE_DIR/wang-knowledge-platform/staging/reports/base-contract-coverage/`

重跑方式：

```bash
PYTHONPATH=. backend/.venv/bin/python -m backend.pipeline.base_contract_coverage \
  --output-dir "$DATA_BASE_DIR/wang-knowledge-platform/staging/reports/base-contract-coverage"
```

工具是確定性的，不呼叫 model，相同輸入產出相同結果。

## 2026-08-17 的量測結果（僅數字，不含內容）

| 文章 | 經文相關句 | 已成為 required step | 範圍內未成 step | 完全在契約範圍外 |
|---|---:|---:|---:|---:|
| DRAFT-M16-001-V1 | 43 | 12 | 31 | 0 |
| DRAFT-M16-002-V1 | 97 | 18 | 26 | **53** |
| DRAFT-M16-003-V1 | 26 | 5 | 10 | 11 |

承重但未被涵蓋者：001 為 10／0，002 為 10／**25**，003 為 6／3。

002 落在契約範圍外的 53 句中，25 句位於母本 `## 三、天国钥匙`——即該篇太16:19 的釋經，落在文章經文範圍內，卻在 `base_source.section_anchor` 之外。成因是契約按**章節標題**選取母本範圍，而非按**經文引用**。001 為 0 只是因為該篇材料剛好都在同一標題下。
