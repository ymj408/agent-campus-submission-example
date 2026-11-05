# 02 · 方案、调用流程与动态决策

## 1. 总体架构

```text
┌──────────────────────────── CLI (run.py / cli.py) ────────────────────────────┐
│ 读取 case/参数 → 初始化 Store → 选择 Planner → 输出 result.json + trace.jsonl  │
└───────────────────────────────────┬───────────────────────────────────────────┘
                                    │
                    ┌───────────────▼────────────────┐
                    │ SchedulerAgent (agent.py)       │
                    │  step/tool 上限、循环、收口      │
                    └───────┬─────────────────┬──────┘
                            │                 │
              ┌─────────────▼──────┐   ┌──────▼────────────────────────────┐
              │ Planner            │   │ ToolRuntime (tools.py)             │
              │ MockPlanner        │   │ 6 个业务工具 + 参数/任务绑定校验   │
              │ OpenAICompatible   │   │ 每次保存重新审核/查冲突            │
              └─────────────┬──────┘   └──────┬─────────────────────────────┘
                            │                 │
                            │        ┌────────▼─────────┐
                            │        │ RuleEngine       │
                            │        │ rules.py         │
                            │        └────────┬─────────┘
                            │                 │
                            │        ┌────────▼─────────┐
                            │        │ RuntimeStore     │
                            │        │ state/drafts     │
                            │        └──────────────────┘
                            │
                  原生 tool_calls / JSON 动作
```

没有 Web 服务、数据库或消息队列；CLI 一次运行是一个独立进程，状态落在 `--store` 指定目录。

## 2. 文件与职责

| 文件 | 职责 |
|---|---|
| `models.py` | `TaskRequest`、`Action`、`StepRecord`、上限 |
| `repository.py` | 只读种子数据；运行期 state/drafts；原子写；锁 |
| `rules.py` | 规则类型执行、缺失字段、违规解释、审核结论 |
| `tools.py` | 6 个工具、参数校验、任务绑定、幂等保存、故障注入 |
| `planner.py` | Planner 协议 + 离线观察驱动策略 |
| `llm.py` | OpenAI-compatible Chat Completions + tool calling |
| `agent.py` | ReAct 循环、Trace、最终状态推导、摘要/待办 |
| `cli.py` | 参数解析、案例加载、人类可读/JSON 输出 |
| `trace.py` | JSONL 记录器 |

## 3. 工具契约

| 工具 | 关键参数 | 作用 | 是否改写 |
|---|---|---|---|
| `get_material` | `material_id` | 读取绑定素材、返回缺失字段 | 否 |
| `lookup_material_source` | `material_id` | 补查模拟工单，只回填非空字段 | 是（material_overrides） |
| `check_rules` | `material_id` | 执行规则，返回 pass/needs_review/blocked | 否 |
| `query_slots` | `material_id`, `requested_publish_at?`, `limit` | 请求时段状态 + 最近的后续可用时段 | 否 |
| `request_confirmation` | `material_id`, `reason`, `proposed_publish_at?`, `questions` | 创建 pending 待确认项，**不批准** | 是（confirmations） |
| `save_schedule_draft` | `material_id`, `proposed_publish_at`, `confirmation_id?` | 重新审核/查冲突/校验确认后保存草稿 | 是（drafts） |

**不存在** `publish`、`publish_schedule`、平台 API 工具。`test_tool_set_has_no_publish_endpoint` 会锁定该集合。

每个工具还有可选的 `rationale` 参数，仅用于 Trace 的一句话决策依据；运行时在进入业务逻辑前移除，不参与判断。

### 3.1 时段查询

- 以 60 分钟为刻度，范围 09:00–21:00（含端点）。
- 冲突条件：同平台 + 同一时刻的 confirmed schedule 或**他人**草稿。
- 同一素材自己的草稿会被忽略，保证重复提交时仍能选中同一时段并触发幂等。
- 替代时段排序：优先“之后”的最近时段（18:00 → 19:00），没有更晚可用才回退到更早。
- 返回 `requested.status = available | occupied | outside_window`，并带冲突对象。

### 3.2 规则检查

规则类型：

| 类型 | 例子 | 结果 |
|---|---|---|
| `required_fields` | 时长、版权缺失 | blocking |
| `allowed_values` | 版权状态不在 original/licensed/authorized | blocking |
| `forbidden_terms` | “根治”“无需就医” | blocking |
| `sensitive_terms` | “医美”“医疗建议”“疗法” | warning |
| `max_length` | 标题超长 | warning |

审核结论：

```text
任一 blocking → blocked
否则任一 warning → needs_review
否则 → pass
```

保存工具会再次运行同一个引擎，因此模型跳过 `check_rules` 也不能绕过审核。

### 3.3 待确认

`request_confirmation` 生成：

```json
{
  "confirmation_id": "CONF-0001",
  "reason": "slot_deviation",
  "requested_publish_at": "2026-11-06T18:00:00+08:00",
  "proposed_publish_at": "2026-11-06T19:00:00+08:00",
  "questions": ["18:00 已被占用，是否同意草稿改到 19:00？"],
  "status": "pending_user_confirmation",
  "approved": false
}
```

- 指纹去重：同一问题重复请求返回同一个 `CONF-*`。
- 工具没有“批准”入口；真实用户审批不在本期范围。
- `allow_time_change=false` 时，`slot_deviation` 请求直接返回 `time_change_not_allowed`。

### 3.4 草稿保存

保存前依次检查：

1. `material_id` 是否绑定任务；
2. 建议时间是否在发布窗口；
3. 重新审核（blocking 拒绝）；
4. 偏离原要求时：是否允许改时间、是否提供 pending 且匹配的 confirmation；
5. 幂等键是否已存在（存在则直接返回原草稿）；
6. 故障注入；
7. 同素材是否已有其他草稿；
8. 正式排期/他人草稿是否占用；
9. 写草稿文件 + 更新 state 索引（失败则清理文件并返回 `save_failed`）。

## 4. ReAct 循环

```python
for step in range(1, max_steps + 1):
    action = planner.decide(task, history)      # 模型选择动作
    if action.kind == "finish":
        finish_claim = action
        break

    if tool_calls >= max_tool_calls:
        return limit("tool_limit")

    observation = tools.call(action.tool, action.arguments)  # 执行工具
    history.append(...)                         # 观察结果
    trace.step(...)                             # 记录参数/摘要/依据

return finalize(finish_claim, history)          # 工具事实优先
```

- **步骤上限**：默认 10。
- **工具调用上限**：默认 8。
- 达到上限立即停止，返回 `step_limit_reached` / `tool_limit_reached`，`completed=false`，并写 `limit` 事件。
- 模型只能看到工具 Observation，不能直接改 store；所有写入必须经过工具。

### 4.1 观察 → 下一步（离线策略）

| 观察 | 下一步 | 依据 |
|---|---|---|
| 素材缺字段 | `lookup_material_source` | 先补查，不猜测 |
| 补查后仍缺字段 | `check_rules` → `request_confirmation` → finish blocked | 不能保存不完整草稿 |
| 规则 blocked 且有风险 | finish `blocked_rule_risk` | 阻断，不排期 |
| 规则 warning | 继续查时段/保存，草稿标记 needs_review | 草稿可人工复核 |
| 请求时段 occupied + 有替代 | `request_confirmation` → `save_schedule_draft` | 不静默改时间 |
| occupied + 用户禁改 | finish `blocked_time_change_not_allowed` | 硬性要求优先 |
| occupied + 无替代 | finish `blocked_no_available_slot` | 无可用时段 |
| save 可重试失败 | 原参数重试一次；仍失败 finish `save_failed` | 明确失败，不虚报 |
| 已有草稿 | finish 对应 draft 状态 | 重复提交幂等 |

离线策略是**观察驱动**的，但仍是条件策略；它不是大模型，不用于证明模型决策质量。真实模型通过 `OpenAICompatiblePlanner` 接入，使用同一套工具与循环。

## 5. 最终状态由工具事实推导

`agent._finalize` 的优先级：

```text
step_limit / tool_limit / agent_error
  > 已落盘草稿（draft_saved*）
  > save_error（save_failed）
  > audit blocked（blocked_missing_info / blocked_rule_risk）
  > 时段约束（blocked_time_change_not_allowed / blocked_no_available_slot / awaiting）
  > 合理的 finish claim
  > incomplete
```

模型 `finish` 的 status 只作为 `finish_claim` 记录，**不能覆盖**工具事实。测试 `SaveThenLiePlanner` 在两次持久保存失败后谎报 `draft_saved`，运行时仍返回 `save_failed`。

同理：

- 摘要由运行时生成，不直接采用模型 summary；
- `published` 硬编码为 `false`；
- `pending_items` 由审核/确认/保存错误推导，模型不能删除必需待办。

## 6. 幂等与存储

### 6.1 幂等键

```text
draft_key = sha256({material_id, requested_publish_at, proposed_publish_at})
```

- 不包含 `request_id`：同一素材同一时间即使请求 ID 不同也不重复建草稿。
- 不包含 `confirmation_id`：确认项重建不影响草稿幂等。
- 重复保存先查 `state.draft_index`；命中直接读取草稿文件，标记 `deduplicated=true`。

### 6.2 文件布局

```text
<store>/
├── state.json
│   ├── schedule                # 种子排期副本（只读语义）
│   ├── material_overrides      # 补查回填
│   ├── draft_index             # key -> DRAFT-id
│   ├── confirmations
│   └── confirmation_index
└── drafts/
    └── DRAFT-0001.json
```

- 所有 JSON 写入使用临时文件 + `os.replace`。
- 同一 store 使用进程内 `RLock`；单进程测试通过。
- 草稿文件先写，再写索引；索引失败会删除刚写的草稿，避免“文件存在但索引没有”的半个成功。

### 6.3 全局序列

`DRAFT-*` 与 `CONF-*` 共用一个递增 sequence，因此可能出现 `CONF-0001` + `DRAFT-0002`；这是有意保留的审计单调序列，类型前缀区分对象。

## 7. 真实模型接入

`llm.py`：

- `POST {base_url}/chat/completions`
- `tools = 6 个业务工具 + finish`
- 原生 `tool_calls` 优先；纯 JSON 内容作为兼容回退。
- Trace 只记录 `rationale` 一句话，不记录隐藏推理。
- API Key 只从环境变量读取；HTTP 错误只保留状态码和截断响应，不打印请求头。
- 每条历史工具调用在消息中重建为 `assistant(tool_calls)` + `tool(result)`，支持多轮。

**没有真实 Key 的边界**：本地假网关测试证明“请求格式、响应解析、动作执行、下一步观察”协议打通；不证明真实模型能稳定做出正确的业务判断。

## 8. 技术取舍

| 选择 | 理由 | 代价 |
|---|---|---|
| Python 标准库 | 零依赖、评审一键运行、逻辑可审计 | 没有框架的追踪/可视化 |
| 原生 ReAct 循环 | 考核重点是决策与边界，不是框架调用 | 需要自己实现循环、上限、Trace |
| 规则引擎独立 | 避免模型自审自签；保存时二次校验 | 规则变更需维护规则文件 |
| 草稿文件 + JSON 索引 | 可读、可测试、易验证幂等 | 不是事务数据库；极端崩溃需恢复脚本 |
| 离线策略与真实模型分离 | 不把 Mock 当模型效果 | 真实模型效果需额外验证 |
| 全局 sequence | 审计 ID 单调 | 两类对象 ID 不连续 |

## 9. 安全与越权防护

- `TaskRequest` 由 CLI/case 构造；模型参数不能改 `requested_publish_at`。
- `_require_task_material` 拒绝操作任务外素材。
- 保存时重新审核、重新查冲突，防止“查询时可用、保存时已被占”或模型伪造结果。
- 没有发布工具；`published` 在 draft、result、Trace 中均为 false。
- 确认工具没有 `approved=true` 的入参；模型不能代替用户同意。
- `.env` 被 `.gitignore`；Trace 不写密钥。

## 10. 关键文件

- 实现：[`src/media_scheduler_agent/`](../src/media_scheduler_agent/)
- 规则：[`data/rules.json`](../data/rules.json)
- 参考冲突样例：[`examples/outputs/02-conflict-alternative.json`](../examples/outputs/02-conflict-alternative.json)
- Trace：[`examples/traces/02-conflict-alternative-run1.jsonl`](../examples/traces/02-conflict-alternative-run1.jsonl)
