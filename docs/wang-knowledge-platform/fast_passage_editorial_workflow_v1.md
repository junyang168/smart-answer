# 篇章釋經快速路徑 v1

## 目的

快速路徑用於已完成部分知識抽取的連續釋經工作。它避免每一小段經文重新提交整篇筆記、完整逐字稿與大型共享知識包，並把模型使用限制在真實缺口。

## 固定次序

1. 按結構化經文範圍查詢 PostgreSQL。
2. 若資料庫已覆蓋全部經節且存在合格證據，直接進入編排。
3. 若資料庫有缺口，對已審核知識包做確定性的經文切片。
4. 已審核包足以覆蓋時，只把最小切片增量寫入 PostgreSQL。
5. 只有資料庫與已審核包都不足時，才啟動模型補抽取。
6. 編排、正文與審核只接收本段切片。
7. 正文、manifest、精簡知識快照與審核結果發布至 `$DATA_BASE_DIR/wang-knowledge-platform/repository/editorial_drafts/<draft_id>/`。

## 防止主題漂移

切片依经文范围重叠选择记录。覆盖范围明显大于目标篇章的主张只列为 `contextual_claim_leads`，不会自动进入正文或写回本段的最小知识集。

## 模型闸门

文字與媒體分別設閘門。`requires_model_extraction` 只有在以下任一條件成立時才為真：

- 目标范围存在未覆盖经节；
- 没有任何 `eligible`、`eligible_candidate` 或 `eligible_with_label` 证据。

双模型复核与仲裁不再是每篇必跑步骤，只处理冲突、低置信度、来源归属不明或程序审核无法裁定的项目。

若来源地图已列出相关讲道，而切片中没有该讲道的有效时间码证据，系统必须另行返回 `requires_media_projection: true`。文字完整不能代替媒体完整。媒体投影先以已发布逐字稿的唯一 segment index 与逐字摘录进行确定性核验；只有无法唯一定位时才调用模型或转人工。

## 命令

```bash
python -m backend.pipeline.passage_fast_path_runner \
  --book Matt --chapter 16 --start 21 --end 23 \
  --fallback-package <reviewed-package.json> \
  --apply-fallback --output <temporary-slice.json>
```

若 PostgreSQL 已有完整覆盖，命令返回 `postgresql_reuse`，不会重复写入，也不会调用模型。
