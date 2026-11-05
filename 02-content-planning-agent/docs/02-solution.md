# 02 · 方案、调用流程与动态决策

## 1. 总体架构

```text
CLI (run.py / cli.py)
  │ 读取任务、初始化 store、选择 planner
  ▼
ContentPlanningAgent (agent.py)
  │ ReAct 循环、步数/工具上限、最终状态收口
  ├── Planner
  │    ├── MockPlanner：离线观察驱动策略
  │    └── OpenAICompatiblePlanner：原生 tool calling
  └── ToolRuntime (tools.py)
       ├── SeedData：materials / tool_costs / plan_templates
       └── RuntimeStore：state / plans / artifacts / runs(checkpoint)
```

没有数据库、Web 服务、消息队列；一次 CLI 运行是一个进程。

## 2. 工具契约

| 工具 | 参数 | 作用 | 状态 |
|---|---|---|---|
| `get_brief` | — | 读主题/预算/截止、缺失字段、已有方案 | 只读 |
| `search_materials` | `query?`, `limit?` | 过滤授权过期素材，按标签匹配 | 只读 |
| `list_plan_options` | — | 列出模板、总成本、总耗时 | 只读 |
| `estimate_plan` | `plan_id` | 计算成本/预算/截止时间，建立当前计划 | 内存状态 |
| `generate_script` | `plan_id` | 生成脚本 artifact；校验预算/截止/依赖 | 写 artifact |
| `execute_step` | `plan_id`, `step_id` | 执行海报/剪辑/审核/分发；校验依赖 | 写 artifact |
| `save_plan` | `plan_id`, `note?` | 全部成功后保存；幂等 | 写 plans/ |

没有 publish 工具。`save_plan` 只保存本地方案。

## 3. ReAct 循环

```python
for step in range(1, max_steps + 1):
    action = planner.decide(task, history)      # 模型选择动作
    if action.kind == "finish":
        finish_claim = action
        break
    if tool_calls >= max_tool_calls:
        return limit("tool_limit")
    observation = tools.call(action.tool, action.arguments)
    history.append(...)                         # 观察结果
    trace.step(...)
return finalize(finish_claim, history)          # 工具事实优先
```

- 默认 16 步 / 14 次工具调用。
- 达到上限立即停止，返回 `step_limit_reached` / `tool_limit_reached`，`completed=false`。
- 每次 Trace 包含动作、参数、工具摘要、一句 `decision_basis`，不记录隐藏思维链。

## 4. 动态决策

### 4.1 预算超限

`estimate_plan` 对超预算方案返回 `ok=true, within_budget=false, over_by=2`，不是工具错误。

离线策略观察后：

```text
estimate premium_video → 12 > 10
  → estimate lean_article → 8 <= 10
  → 执行步骤
  → save_plan
```

若所有方案都超预算：

```text
finish(incomplete_no_feasible_plan)
gap = cheapest_total - budget
```

不保存任何方案。

### 4.2 工具失败

`generate_script` / `execute_step` 失败时：

1. 将当前步骤标记 `failed`；
2. 保留 `step_results` 中之前成功的步骤；
3. 写入 `failure_history`（包括失败时已经成功的步骤）；
4. 返回 `ok=false, code=tool_failed`。

策略观察后：

- 找不含失败步骤、预算内、同素材优先的替代方案；
- 找到则重新 `estimate_plan`（成功步骤会带 `preserved=true` 复用）；
- 找不到则 `finish(incomplete_tool_failure)`，不保存；
- 失败步骤永远不会被写成 `succeeded`。

示例：

- 海报失败 → `lean_no_poster`（7），脚本保留；
- 审核失败 → 所有方案都依赖审核，停止，保留脚本/海报。

### 4.3 重复执行

- 任务指纹 = theme + budget + deadline + audience + channel 的 SHA-256。
- `save_plan` 按指纹 + 方案 ID 建幂等键。
- `get_brief` 发现同一指纹已有方案，直接 `plan_saved_deduplicated`，不再执行步骤。
- 第二次运行只调用一次 `get_brief`；方案数仍为 1。

### 4.4 失败步骤续跑（加分项）

每次工具成功/失败后写 checkpoint：

```text
runs/RUN-<fingerprint>.json
  ├── step_results   # 成功步骤及产物
  ├── estimates
  ├── current_plan   # 步骤状态
  ├── failure_history
  └── saved_plan
```

`--resume` 加载 checkpoint：

- `estimate_plan` 重建当前计划时，成功且同素材的步骤直接标为 `succeeded (preserved=true)`；
- `generate_script` / `execute_step` 命中已成功步骤时直接返回缓存；
- 失败步骤重新执行；
- 全部成功后再 `save_plan`。

实际示例 07：第一次审核失败；第二次只补 review/distribution，没有再次执行 script/poster。

### 4.5 保存

`save_plan` 依次检查：

1. 当前 plan 与 estimate 匹配；
2. 预算内；
3. 截止时间可行；
4. 全部步骤 `succeeded`；
5. 同指纹已有方案 → 幂等返回；
6. 写 `plans/PLAN-*.json`；
7. 更新 `state.json` 的 `plans_index` / `request_index`；
8. 失败则清理文件并返回 `save_failed`。

## 5. 最终状态由工具事实推导

`agent._finalize` 优先级：

```text
step_limit / tool_limit / agent_error
  > 已保存方案（plan_saved / plan_saved_deduplicated）
  > save_error（save_failed）
  > 缺字段（blocked_missing_fields）
  > 无素材 / 无可行方案 / 工具失败 / 超时
  > 合理的 finish claim
  > incomplete
```

- 模型 `finish(status=plan_saved)` 不能覆盖 `save_error`；测试 `SaveThenLiePlanner` 验证返回 `save_failed`。
- 摘要与待办由运行时生成；模型 summary 只记录在 `finish_claim`。
- 方案文件的 `saved=true` 只在真正写入成功后出现。

## 6. 存储

```text
<store>/
├── state.json          # sequence / plans_index / request_index
├── plans/
│   └── PLAN-0001.json
├── artifacts/
│   └── <fingerprint>/
│       ├── script.md
│       ├── poster.svg
│       ├── review.json
│       └── distribution.json
└── runs/
    └── RUN-<fingerprint>.json   # checkpoint
```

- JSON 写入采用临时文件 + `os.replace`。
- 单进程 `RLock`；不支持多进程。
- 方案文件先写，再写索引；索引失败会清理文件，避免半成功。

## 7. 技术取舍

| 选择 | 理由 | 代价 |
|---|---|---|
| 原生 Python + 标准库 | 零依赖、可审计、评审可一键运行 | 无框架可视化 |
| 成本/依赖放工具层 | 模型不能口算或跳过依赖 | 规则变更要维护数据 |
| 模板化方案 | 让参考实例可复现、成本可验证 | 不是任意自由规划器 |
| 离线策略与真实模型分离 | 不把 Mock 当模型效果 | 真实模型需另测 |
| checkpoint | 失败续跑、避免重复计费/调用 | 不是事务数据库 |
| 保存与发布分离 | 防止误发布 | 没有真实投放能力 |

## 8. 安全与边界

- 预算、截止时间来自 `PlanningTask`，模型参数不能修改。
- `save_plan` 重新核验预算、截止时间、依赖与步骤状态。
- 没有发布工具；方案只写本地。
- `.env` 被 `.gitignore`；Trace 不写密钥。
- 失败注入 `--fail-step` 仅用于验收，不是生产容错。

## 9. 关键文件

- 实现：[`src/content_planner/`](../src/content_planner/)
- 成本/方案数据：[`data/`](../data/)
- 参考实例：[`examples/outputs/01-normal-budget-replan.json`](../examples/outputs/01-normal-budget-replan.json)
- 工具失败：[`examples/outputs/05-poster-failure-replan.json`](../examples/outputs/05-poster-failure-replan.json)
- 续跑：[`examples/outputs/07-resume-failed-step.json`](../examples/outputs/07-resume-failed-step.json)
