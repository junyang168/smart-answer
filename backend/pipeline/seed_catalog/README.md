# Seed catalog（審閱階段）

這個工具從指定 notes-to-manuscript 系列的已發布 `final.md` 與既有
`sermon_search/topic_index.json` 建立候選種子目錄。它不修改講稿、
`series_db.json`、正式 topic index 或前台資料。

## 輸出

- `catalog_manifest.json`：輸入檔案、雜湊、統計與安全狀態
- `canonical_units.json`：候選 canonical units、四類欄目、來源與主題建議
- `bible_index.json`：卷、章、經文範圍與單元連結
- `topic_taxonomy.json`：兩層候選主題樹；一個單元可以出現在多個主題
- `topic_aliases.json`：同義詞與共同檢索入口候選
- `duplicate_candidates.json`：經文重疊、來源重疊、標題相似與同義主題群組
- `review_needed.json`：低置信度、未分類及需要人工決定的項目
- `review.md`：給內容審閱者閱讀的整合稿

所有分類都是建議值，狀態為 `candidate_requires_review`。人工確認前，
这些结果不应进入正式前台索引。

Seed catalog 只建立候選 canonical units、聖經／主題分類與現有 manuscript
段落連結。它不建立原始講道或 notes 的片段級 citation，因此不能單獨滿足正式
repository 的來源追溯要求。正式資料庫還必須把 Evidence 行號映射回 transcript
段落、媒體時間或 notes 頁碼，經人工確認後才能發布。

完整的產品與技術要求見：

- [`docs/wang-knowledge-platform/20-knowledge/exegesis_topic_repository_functional_spec.md`](../../../docs/wang-knowledge-platform/20-knowledge/exegesis_topic_repository_functional_spec.md)
- [`docs/wang-knowledge-platform/20-knowledge/repository-tech-spec/README.md`](../../../docs/wang-knowledge-platform/20-knowledge/repository-tech-spec/README.md)

## 生成《馬太福音釋經》審閱稿

```bash
.venv/bin/python -m backend.pipeline.seed_catalog.generator \
  --data-root /opt/homebrew/var/www/church/web/data \
  --series-id d5c55bdf-6375-49e9-a08d-22eda1eaf21d \
  --output "$DATA_BASE_DIR/wang-knowledge-platform/catalog/seed-catalog/matthew-review-v1"
```

主题树与匹配信号保存在 `taxonomy_seed.json`。修改后重新生成即可；生成器
会在结束前再次核对所有来源 `final.md` 的 SHA-256，确保盘点期间正文未被改变。

已经由用户确认的决定记录在 `review_decisions.json`。生成器会优先采用这些
决定，并将相应单元从一般待确认清单中移除；不会靠标题硬编码例外。
