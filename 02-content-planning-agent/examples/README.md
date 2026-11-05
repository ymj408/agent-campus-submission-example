# examples/ 实际运行记录

全部由 `python examples/run_examples.py` 生成，不是手写结果。

- 输入：`examples/cases/*.json`
- 实际输出：`examples/outputs/*.json`
- Trace：`examples/traces/*.jsonl`
- 总览：`examples/outputs/index.json`
- 测试结果：`examples/test-results.txt`、`examples/test-summary.json`

## 总览

| # | 场景 | 关键验收点 | 实际 status | 方案数 |
|---:|---|---|---|---:|
| 01 | [normal-budget-replan](cases/01-normal-budget-replan.json) | 预算 12→8、成本、保存 | `plan_saved` | 1 |
| 02 | [missing-budget](cases/02-missing-budget.json) | 缺预算 | `blocked_missing_fields` | 0 |
| 03 | [missing-deadline](cases/03-missing-deadline.json) | 缺截止时间 | `blocked_missing_fields` | 0 |
| 04 | [no-feasible-plan](cases/04-no-feasible-plan.json) | 全部超预算、缺口 | `incomplete_no_feasible_plan` | 0 |
| 05 | [poster-failure-replan](cases/05-poster-failure-replan.json) | 工具失败、改选、保留脚本 | `plan_saved` | 1 |
| 06 | [review-failure-preserve](cases/06-review-failure-preserve.json) | 工具失败、保留成功、不保存 | `incomplete_tool_failure` | 0 |
| 07 | [resume-failed-step](cases/07-resume-failed-step.json) | 失败续跑 | incomplete → `plan_saved` | 1 |
| 08 | [duplicate-execution](cases/08-duplicate-execution.json) | 重复执行幂等 | saved → dedup | 1 |
| 09 | [save-failure](cases/09-save-failure.json) | 保存失败、不虚报 | `save_failed` | 0 |
| 10 | [step-limit](cases/10-step-limit.json) | 执行上限 | `step_limit_reached` | 0 |

合计：**10 个场景 / 12 次运行 / 4 份已保存方案 / 10 个检查通过**。

## 参考实例怎么读

打开 [01 输出](outputs/01-normal-budget-replan.json)：

1. `step_records` 中两次 `estimate_plan`：premium 超 2，lean 剩 2；
2. `plan.template_id = lean_article`；
3. `budget.total = 8`，`remaining = 2`；
4. `steps` 四个全是 `succeeded`；
5. `saved=true`，`plan_path=plans/PLAN-0001.json`。

打开 [05 输出](outputs/05-poster-failure-replan.json)：

```text
script succeeded
poster failed
estimate_plan lean_no_poster → 7
script preserved=true
review/distribution succeeded
save_plan → PLAN-0001
```

打开 [06 输出](outputs/06-review-failure-preserve.json)：

```text
script succeeded
poster succeeded
review failed
→ incomplete_tool_failure
→ 没有已保存方案
```

打开 [07 输出](outputs/07-resume-failed-step.json)：

```text
run1: review failed，checkpoint 保留 script/poster
run2: --resume；estimate 显示 script/poster preserved=true
     只执行 review/distribution，然后保存
```

## Trace 结构

```json
{
  "event": "step",
  "step": 5,
  "action": "tool",
  "tool": "estimate_plan",
  "arguments": {"plan_id": "lean_article"},
  "decision_basis": "当前方案超预算，尝试替代方案。",
  "observation": {
    "ok": true,
    "summary": "方案 lean_article 估算 8，预算 10，剩余 2",
    "data": {"within_budget": true, "total": 8}
  }
}
```

最后一行 `run_end` 汇总 `status / saved / total / plan_id`。

## 重新生成

```bash
python examples/run_examples.py
```

会重置 `.runtime/examples/<case>`，不修改 `data/`。输出中的时间戳和 run_id 会变化，但检查项应保持通过。

## Mock 边界

`provider=mock` 使用确定性的观察驱动策略，证明：

- 预算计算、依赖、状态、失败保留、续跑、幂等确实被执行；
- 输出可复现、可审计。

它**不**证明真实大模型决策质量；真实模型入口见 README 的 `--provider openai`。
