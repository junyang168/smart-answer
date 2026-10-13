# CVP 语境语义规则全局修正（2026-10-05）

> **读者**：Developer、Solution architect
> **类型**：记录
> **状态**：当次实现与离线验证记录；不构成生产审核通过
> **与代码对齐**：2026-10-05，基于 `67d03eb` 后的本次工作树变更
> **权威范围**：只记录这次代码修正及检查结果；规则以架构、Canonical Viewpoint 规范及版本化配置为准

## 结果与授权

用户要求从全局修改等价与支持规则，并明确须防过度宽松，随后两次“好，你改吧”。本次授权用于代码、prompt、测试与规范，不当作任何 Claim、连接或 CVP 的审核批准，不新增旧语义请求的调用预算。

已完成全局代码修正和离线回归，未调用真实模型，未写 Registry，未重启 campaign。真实进度仍为 36/156 组、192/973 Claims；g037 未 completion，g038 未启动。g035/g037 的临时非独立判读及待复审标记保持。

## 根因

1. 身份整合要求按讲员用法理解，提议与复核却把双向反事实当硬门槛；短摘要或 locator 容易被当成脱离来源的完整公式。
2. supports 的不同入口定义不一致，可能把单条局部依据误作必须证明完整 CVP。
3. 原提议模板还有无条件句式同义示例；身份归并没有结构化未决出口，可能逼模型选择新建或合并。
4. 修订后的新审计会重新请求仲裁，未先识别实际目标未变、仅 finding 理由变化的既有裁决。g037 同一负向目标已有相反裁决；代码不能挑一个有利结果放行。

## 实现

- 新增 `config/wang_cvp_contextual_semantics_v2.md`，按自身语境的实际意思判断等价；保留对象、范围、必要／充分条件、否定、因果、模态及归属。支持要求具体论证作用，不要求独立证明全部观点；同主题或相容仍不足。
- 五个 CVP 工厂、final reviewer、独立审计、条件 content-blocked 补充、原文仲裁及新 bounded supports review 使用版本化规则。独立审计自己读取规则文本，不导入生产语义判定实现。
- 旧 v1/v2/v3 policy、旧角色 prompt 和特定历史修订 adapter 保持。标准新 profile 为 v4，#357 原 GPT6.1Sol/Opus5.5 high 订阅模型 profile 为 v5；模型、容量、顺序、来源及写入门槛未变。
- 身份归并新增 v2 response 的 `unresolved_items`；精确一次覆盖、禁止与 verdict 重叠，遇未决保存原 raw 并停止，不能猜测合并。
- 仲裁前只读查找、验证实际目标未变的历史 packet/raw/handoff、原 audit 与物理原文 SHA、模型/backend/prompt/config/请求/回答。已裁决目标阻止新自动调用；精确拒绝仍须经过既有 reuse proof 门槛，理由变化或冲突不授权放行。
- 新规则入口复用现有无损 shared-record 编码，旧 codec/cache 身份不变。真实 g037 旧大包加入新规则后 shared-text 请求为 502093 bytes；shared-record 后为 466060 bytes，低于冻结 500000。原语义 SHA 与还原 SHA 同为 `d6f8b0bea200e6668094a749f04bb4acad914990c2baddc0bd42ff590b95b111`，零模型、零 Registry 写入。
- prefix-only 旧缓存核验显式保留 v3 profile，不跟随新默认规则；原预算、失败、拒绝、reservation 未重置。

## 验证

- `backend/.venv/bin/python -m pytest -q backend/tests/test_viewpoint*.py backend/tests/test_audit_library*.py`：1518 passed。
- 13 个明确标记为虚构、非教授原话的双边校准案例及离线评分器；覆盖误合、误拒、局部依据、纯相关、必要／充分条件、模态、范围、归属、指代及上下文补结论。期望标签不进入待测输入，评分永不构成生产批准。
- `python3 scripts/check-docs.py docs/wang-knowledge-platform`：71 files，OK。
- `python3 scripts/check-docs.py docs`：76 files；仅 CONVENTIONS 已记录的另项目旧锚点失败 `docs/notes-to-sermon-agent/functional_spec.md:120`，未修改该项目。
- `git diff --check` 通过；primary 最终 `git status --short` 为空；原政策与角色 prompt 无 diff。

## 尚未完成

这次测试证明规则接线、版本隔离、编码和防重试行为，不证明真实 GPT/Opus/Gemini 的语义误判率降低。生产切换前仍需一次独立、来源与规则 SHA 绑定的双边真实模型校准及实际请求预检；保留原指定模型，不让新规则借旧批准或重跑已完成组。

g037 已应用的 `KCS-e858cf921200b1952706`（19 ops）与 `KCS-8b2be154d709aa8af3c1`（3 ops）保持，不重放。已有相反裁决不能因代码升级被清除，需单独明确解决；本记录及用户的规则授权不构成该内容的人工批准。
