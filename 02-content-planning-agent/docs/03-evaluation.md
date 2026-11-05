# 03 · 测试、实际结果、失败与限制

## 1. 复现

```bash
# 生成 10 组实际输出与 Trace
python examples/run_examples.py

# 运行 40 个测试并写入结果
python scripts/run_tests.py

# 数据自检
python run.py verify
```

证据：

- [examples/test-results.txt](../examples/test-results.txt)
- [examples/test-summary.json](../examples/test-summary.json)
- [examples/outputs/index.json](../examples/outputs/index.json)
- [examples/traces/](../examples/traces/)

## 2. 测试结果

```text
tests_run: 40
failures: 0
errors: 0
skipped: 0
success: True
```

| 测试文件 | 数量 | 覆盖 |
|---|---:|---|
| `tests/test_agent.py` | 13 | 正常重规划、缺字段、无可行、海报失败、审核失败、续跑、重复、保存失败、上限、谎报、Trace |
| `tests/test_tools.py` | 13 | 检索过滤、成本 12/8/7/6、依赖、脚本 artifact、部分成功、回退、保存幂等/失败、续跑 |
| `tests/test_cli.py` | 4 | verify、case 运行、缺预算 human 输出、无 Key 报错 |
| `tests/test_llm_adapter.py` | 4 | 原生 tool calling 全链路、JSON 回退、无 Key、HTTP 错误 |
| `tests/test_examples_artifacts.py` | 6 | 产物总览、参考实例、失败保留、续跑、重复、Trace |

关键断言：

- `test_plan_costs_match_reference`：12 / 8 / 7 / 6。
- `test_dependency_is_enforced`：脚本未成功时执行 poster → `dependency_not_met`。
- `test_poster_failure_preserves_script_and_fallback`：脚本 `succeeded`，海报 `failed`，改选 7 的方案。
- `test_save_requires_all_steps_succeeded`：步骤未成功 → 拒绝保存。
- `test_save_is_idempotent`：两次保存同一方案，文件数 1。
- `test_resume_skips_succeeded_steps`：第二次不重新生成脚本/海报。
- `test_runtime_rejects_false_save_claim`：模型 finish 谎报 `plan_saved`，运行时仍 `save_failed`。

## 3. 实际运行结果

`examples/outputs/index.json`：

```json
{
  "scenarios": 10,
  "runs": 12,
  "saved_plans": 4,
  "all_checks_passed": true
}
```

| 场景 | 运行数 | 实际 status | 方案数 | 关键结果 |
|---|---:|---|---:|---|
| 01-normal-budget-replan | 1 | `plan_saved` | 1 | 12 超限 → 8 保存，剩余 2 |
| 02-missing-budget | 1 | `blocked_missing_fields` | 0 | 缺 budget |
| 03-missing-deadline | 1 | `blocked_missing_fields` | 0 | 缺 deadline |
| 04-no-feasible-plan | 1 | `incomplete_no_feasible_plan` | 0 | 最低 6，预算 3，缺口 3 |
| 05-poster-failure-replan | 1 | `plan_saved` | 1 | 海报失败 → 无海报 7；脚本保留 |
| 06-review-failure-preserve | 1 | `incomplete_tool_failure` | 0 | review failed；script/poster 保留 |
| 07-resume-failed-step | 2 | incomplete → plan_saved | 1 | 续跑只补 review/distribution |
| 08-duplicate-execution | 2 | plan_saved → plan_saved_deduplicated | 1 | 第二次只 get_brief |
| 09-save-failure | 1 | `save_failed` | 0 | 步骤成功，方案没保存 |
| 10-step-limit | 1 | `step_limit_reached` | 0 | 3 步上限，明确未完成 |

### 3.1 参考实例（01）

[`examples/outputs/01-normal-budget-replan.json`](../examples/outputs/01-normal-budget-replan.json)

```text
get_brief
search_materials
list_plan_options
estimate_plan premium_video  → 12, 超预算 2
estimate_plan lean_article   → 8, 剩余 2
generate_script              → script succeeded
execute_step poster          → succeeded
execute_step review          → succeeded
execute_step distribution    → succeeded
save_plan                    → PLAN-0001 saved
```

Trace：[`01-normal-budget-replan-1-normal.jsonl`](../examples/traces/01-normal-budget-replan-1-normal.jsonl)

### 3.2 工具失败与部分成功（05 / 06）

05：[`05-poster-failure-replan.json`](../examples/outputs/05-poster-failure-replan.json)

```text
script succeeded
poster failed（注入故障）
estimate_plan lean_no_poster → 7，预算内；script preserved=true
review succeeded
distribution succeeded
save_plan → PLAN-0001
```

06：[`06-review-failure-preserve.json`](../examples/outputs/06-review-failure-preserve.json)

```text
script succeeded
poster succeeded
review failed
所有替代方案都依赖 review → 停止
最终 incomplete_tool_failure，方案未保存
```

### 3.3 失败步骤续跑（07）

第一次：

[`07-resume-failed-step-run1`](../examples/traces/07-resume-failed-step-1-first-fail.jsonl)

```text
script succeeded
poster succeeded
review failed
→ incomplete_tool_failure；checkpoint 保留 script/poster
```

第二次加 `--resume`：

[`07-resume-failed-step-run2`](../examples/traces/07-resume-failed-step-2-resume.jsonl)

```text
estimate_plan → script/poster 显示 succeeded (preserved=true)
execute_step review → succeeded
execute_step distribution → succeeded
save_plan → PLAN-0001
```

第二次 step_records 中没有 `generate_script`，证明没有重复执行已成功步骤。

### 3.4 重复执行（08）

[`08-duplicate-execution.json`](../examples/outputs/08-duplicate-execution.json)

- 第一次：10 个工具调用，保存 PLAN-0001。
- 第二次：只调用 `get_brief`，`plan_saved_deduplicated`，同一 PLAN-0001。
- `store_summary.plan_count = 1`。

### 3.5 保存失败（09）

- 脚本、海报、审核、分发全部 `succeeded`。
- `save_plan` 两次失败（故障注入）。
- 最终 `save_failed`，`saved=false`，`plan_count=0`，成功步骤仍保留。
- 摘要明确“没有已保存方案”。

### 3.6 执行上限（10）

- `max_steps=3`：get_brief / search / list_plan_options 后停止。
- `step_limit_reached`，`completed=false`，Trace 含 limit 事件。

## 4. 失败与边界

### 4.1 已处理的失败

| 情况 | 行为 |
|---|---|
| 缺预算/截止 | 阻断并返回缺失字段 |
| 无素材 | 停止，不估算 |
| 预算超限 | 改选；无可行则返回缺口 |
| 截止超时 | 不保存，返回超时 |
| 依赖未满足 | `dependency_not_met`，不执行 |
| 工具失败 | 步骤 failed；成功结果保留；重规划/停止 |
| 保存失败 | `save_failed`；无方案文件 |
| 重复任务 | 直接 dedup，不重复执行 |
| 执行上限 | 明确 `*_limit_reached` |
| 模型谎报 | 运行时按工具事实覆盖 |

### 4.2 Mock 边界

Mock 证明：

- 成本计算与预算比较；
- 步骤依赖与 artifact 状态；
- 失败重规划、部分成功保留、checkpoint 续跑；
- 幂等保存与最终状态收口。

Mock **不证明**：

- 真实模型是否能稳定选对方案；
- 真实模型的提示注入/越权风险；
- 真实素材报价与截止时间估算准确度。

真实模型适配器用本地假 OpenAI-compatible 网关做了 11 次 tool call 全链路测试，但没有使用真实 API Key。

### 4.3 已知限制

1. 只保存方案，不接真实发布/投放。
2. 真实模型未用线上 Key 验证。
3. 方案模板固定，不是任意自由规划器。
4. 成本表/素材库为合成数据。
5. 单进程锁；不支持多进程写同一 store。
6. 截止时间按串行耗时估算，不支持并行排期。
7. checkpoint 不是事务；极端崩溃可能留下未索引 artifact（不影响已保存方案的正确性）。
8. `--fail-step` 是验收钩子，不是生产容错。
9. 没有审批/预算审批/版权人工复核。
10. 方案文件写入与 state 索引两次写；索引失败已清理文件，但未实现跨进程恢复。

## 5. 评审建议路径

1. 看 `examples/outputs/index.json`（10/12/4）。
2. 看 01 输出，核对 12→8、总成本、4 个步骤。
3. 看 05 输出，核对 poster failed 与 script preserved。
4. 看 06/07，核对失败保留与续跑。
5. 看 08，核对方案数 1。
6. 看 `examples/test-results.txt`（40 tests）。
