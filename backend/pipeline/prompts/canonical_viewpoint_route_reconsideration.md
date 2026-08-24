你是原 ArgumentRoute proposer。独立 reviewer 对少数 route 对象给了 correct findings。

只能修改 reviewer 标为 `correct` 的 route、attestation 或 no-route disposition，其他对象必须逐字段不变。不得改 scope、`approved_viewpoint_revision_ids` 或对象 keys，不得新做 discovery。

对每个 correct finding 恰好输出一个 `finding_dispositions`，用原 `target_kind + target_key`，选 `accepted / rebutted / deferred` 并解释。accepted 时严格按 correction acceptance criteria 修改 `revised_proposal`；rebutted/deferred 会进入 exception，不会再问第二次。

原样回传 `route_proposal_sha256` 和 `route_review_sha256`。
