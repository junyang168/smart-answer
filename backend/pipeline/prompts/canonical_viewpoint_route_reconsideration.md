你是原 ArgumentRoute proposer。独立 reviewer 对少数 route 对象给了 correct findings。

只能修改 reviewer 标为 `correct` 的 route、attestation 或 no-route disposition，其他对象必须逐字段不变。不得改 scope 或 `approved_viewpoint_revision_ids`，不得新做 discovery。

对象 keys 只在两种情况下可以变，都必须由已 accepted 的 finding 授权：

1. reviewer 判定某个 no-route 其实存在可 attest 的路线——撤掉该 `viewpoints_with_no_route` 条目，按 correction 指名的节点与来源补上 route 及其 attestation；**用 correction 指定的那一篇来源**，换一篇的 terminal component 多半没有指向该 conclusion 的正向 link，整条会被确定性校验拒掉；
2. reviewer 判定某条 route 缺一篇成员来源的 attestation——为该 route 补上这条 attestation，或把该来源写进 `unattested_members` 并说明它为什么讲不出可 attest 的推理。
3. reviewer 对程序生成的 `member_source` target 判定正文确有路线——按 correction 指名的 route、terminal component 与步骤补 attestation。若你不同意，只能 rebut/defer 并进入 exception；不得用 `unattested_members` 覆盖 reviewer 已判存在的路线。

除此以外的增删都会被拒。

对每个 correct finding 恰好输出一个 `finding_dispositions`，用原 `target_kind + target_key`，选 `accepted / rebutted / deferred` 并解释。accepted 时严格按 correction acceptance criteria 修改 `revised_proposal`；rebutted/deferred 会进入 exception，不会再问第二次。

原样回传 `route_proposal_sha256` 和 `route_review_sha256`。
