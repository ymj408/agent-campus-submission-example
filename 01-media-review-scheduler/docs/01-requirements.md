# 01 · 需求、范围与成功标准

## 1. 用户与目标

**主要用户**

- 内容运营：提交素材与发布时间要求，接收待办和待确认项。
- 审核/排期人员：查看每项素材的审核状态、风险原因、建议时段和草稿状态。
- 内容团队负责人：在人工确认后决定是否进入真实发布流程（本项目不实现）。

**核心目标**

1. 找出待处理素材并读取元数据。
2. 依据规则库判断 `pass / needs_review / blocked`。
3. 检查用户要求时段与已有排期、草稿的冲突。
4. 在冲突时给出可解释的替代建议，但不擅自改变硬性要求。
5. 仅保存排期草稿，不发布；重复请求不产生重复草稿；保存失败明确返回失败。
6. 用可审计的 Trace 证明 Agent 确实根据工具结果调整了下一步。

## 2. 输入 / 输出

### 输入

一次任务只需一个素材和一个硬性发布时间：

```json
{
  "request_id": "REQ-EX-02",
  "material_id": "M01",
  "requested_publish_at": "2026-11-06T18:00:00+08:00",
  "allow_time_change": true,
  "note": "参考实例：18:00 已占用，19:00 可用"
}
```

| 字段 | 说明 |
|---|---|
| `request_id` | 请求标识，用于审计/幂等诊断（不参与草稿幂等键） |
| `material_id` | 必须是 `data/materials.json` 中存在的素材 |
| `requested_publish_at` | 用户硬性要求；ISO 8601；本项目固定 Asia/Shanghai |
| `allow_time_change` | 是否允许“建议”替代时段；默认 true |
| `note` | 人工备注 |

数据侧输入：

- `data/materials.json`：素材标题、平台、类型、时长、版权、简介等。
- `data/rules.json`：必填字段、版权/平台枚举、禁用表述、敏感主题、标题长度。
- `data/schedule.json`：已有确认排期；只读。
- `data/source_notes.json`：模拟制作系统补查；只回填非空字段。

### 输出

每项素材至少返回：

| 输出 | 示例 |
|---|---|
| 审核状态 | `pass` / `needs_review` / `blocked` |
| 风险原因 | `[R004] 命中禁用表述：根治` |
| 建议时段 | `2026-11-06T19:00:00+08:00`（仅草稿建议） |
| 待确认项 | “是否同意草稿改到 19:00？”；`approved=false` |
| 草稿保存状态 | `saved=true/false`、`draft_id`、`deduplicated` |
| 发布状态 | 永远 `published=false` |

## 3. 范围

### In scope

- CLI 运行（`run / list / show / verify`）。
- 6 个业务工具：素材读取、来源补查、规则检查、时段查询、待确认、草稿保存。
- ReAct 循环：模型/策略动作 → 工具 → 观察 → 下一步。
- OpenAI-compatible 真实模型适配；离线 Mock。
- 规则风险、信息缺失、时段冲突、硬性时间边界、重复提交、保存失败、执行上限。
- 47 个可执行测试；8 组输入、实际输出与 Trace。

### Out of scope

- 真实发布、OAuth、内容平台 API。
- 用户审批 UI / 消息推送。
- 多 Agent 协作、生产级 Runtime、数据库。
- 多店铺/多租户、跨时区、跨天排期。
- 自动改写素材文案。
- 真实品牌数据；所有数据均为合成数据。

## 4. 关键语义

### 4.1 硬性发布时间

`requested_publish_at` 是任务绑定字段，不来自模型参数；工具 `save_schedule_draft` 从 `TaskRequest` 读取它。任何偏离都必须：

1. 先生成 `pending_user_confirmation` 的 `request_confirmation`；
2. 草稿同时保存 `requested_publish_at` 和 `proposed_publish_at`；
3. `confirmation.approved=false`，状态为 pending；
4. 摘要明确“等待用户确认”，不称为已发布。

若 `allow_time_change=false`，则不得建议替代时段，直接进入 `blocked_time_change_not_allowed`。

> 草稿中的建议时间不是对硬性要求的修改；原要求被完整保留，最终变更只能由用户在后续流程确认。

### 4.2 只保存草稿

系统没有 publish 工具；`save_schedule_draft` 只写：

- `drafts/DRAFT-*.json`
- `state.json` 的 `draft_index`
- 待确认项索引

不会改写 `data/schedule.json`，也不会调用任何外部平台。

### 4.3 重复提交

幂等键：

```text
sha256(material_id + requested_publish_at + proposed_publish_at)
```

同一个素材、同一原要求、同一建议时段重复保存，返回同一 `draft_id`，`deduplicated=true`，草稿文件数仍为 1。`request_confirmation` 也按问题指纹去重，避免重复确认项。

### 4.4 保存失败

- 故障注入：`--fail-save N`（`99` 表示每次失败）。
- 工具返回 `ok=false, error.code=save_failed, retryable=true`。
- 运行时最终状态为 `save_failed`，`draft.saved=false`，不创建草稿文件，不虚报“已保存”。

## 5. 成功标准与验收矩阵

| 必须验收点 | 对应输入 | 实际输出（见 examples） | 关键证据 |
|---|---|---|---|
| 正常排期 | `01-normal-available`（M03, 12:00） | `draft_saved`，DRAFT 已保存，`published=false` | `examples/outputs/01-*.json` |
| 素材信息缺失 | `03-missing-information`（M02） | 补查回填版权，仍缺时长；`blocked_missing_info`，无草稿 | `examples/outputs/03-*.json` |
| 规则风险 | `04-rule-risk-block`（M04）；`05-warning-needs-review`（M06） | M04 `blocked_rule_risk`；M06 `draft_saved_needs_review` | 对应 output / trace |
| 时段冲突 | `02-conflict-alternative`（M01, 18:00） | 18:00 被 M99 占用，建议 19:00，`draft_saved_pending_confirmation` | `02` output 第 3–5 步 |
| 重复提交 | `02` 连续运行两次 | 同一 `DRAFT-*` / `CONF-*`；第二次 `deduplicated=true`；草稿数 1 | `02` output `store_summary` |
| 保存失败 | `06-save-failure`（M05，注入持久失败） | `save_failed`，无草稿，`draft.saved=false` | `06` output / trace |
| 硬性时间不可改 | `07-hard-time-no-change`（M01 禁改） | `blocked_time_change_not_allowed`，无草稿 | `07` output |
| ReAct 动态决策 | `02`、`03` 两条路径 | 冲突后先确认再保存；缺资料先补查再阻断 | `examples/traces/*.jsonl` |
| 执行上限 | `08-step-limit` | `step_limit_reached`，`completed=false`，Trace 有 `limit` 事件 | `08` output / trace |
| 不越权发布 | 全部 9 次运行 | `published=false`；无 publish 工具 | `examples/outputs/index.json` |
| 真实模型入口 | `--provider openai` | 原生 tool calling 适配器；无 Key 时明确报错 | `tests/test_llm_adapter.py` |

**总体验收结果（实际运行）：**

- 8 个场景 / 9 次运行
- 3 份草稿，0 次发布
- 8/8 场景 `checks_passed=true`
- 47 个测试全部通过

## 6. 非功能要求

| 要求 | 实现 |
|---|---|
| 可复现 | 固定合成数据 + 可重置 store + 一键 examples 脚本 |
| 可解释 | 每条违规含 rule_id、字段、证据；每步 Trace 含决策依据 |
| 安全 | 无真实密钥；草稿只写本地；无发布接口 |
| 可测试 | 纯标准库 unittest；47 个测试；含假 OpenAI 网关协议测试 |
| 可审计 | JSONL Trace；输出含 run_id、usage、limits、finish_claim |
| 失败可见 | 工具错误有 `code` / `retryable` / 摘要；超限有明确状态 |

## 7. 假设

1. 发布时间表示“发布动作发生的时间点”，不与素材时长相乘占位。
2. 冲突按“平台 + 同一时刻”判断。
3. 已有排期为 confirmed；草稿也占位，但同一素材的重复查询忽略自己的草稿。
4. 规则库由运营/法务维护；本项目只负责执行和解释。
5. 用户确认在系统外部完成；本 Agent 只能创建 pending，不能代替用户批准。
6. 发布窗口为 09:00–21:00，时区 Asia/Shanghai。

## 8. 与参考实例的对应

参考：M01 无风险，要求 18:00，已占用，19:00 可用；建议 19:00 并标记待确认；不得称为已发布；重复保存只产生一份草稿。

本仓库：

- M01 在 `data/schedule.json` 中与 M99 的 18:00 冲突；
- `query_slots` 返回 19:00 为最近可用；
- `request_confirmation` 生成 `CONF-0001`，`approved=false`；
- 草稿 `DRAFT-0002` 同时保存 requested=18:00 与 proposed=19:00；
- 第二次运行返回同一草稿，`deduplicated=true`；
- 所有输出 `published=false`，摘要写“未发布 / 等待用户确认”。
