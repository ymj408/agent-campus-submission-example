# examples/ 运行记录

本目录是**实际运行产物**，不是手写结果。生成方式：

```bash
python examples/run_examples.py
```

- 输入：`examples/cases/*.json`
- 实际输出：`examples/outputs/*.json`
- Trace：`examples/traces/*.jsonl`
- 总览：`examples/outputs/index.json`
- 测试结果：`examples/test-results.txt`、`examples/test-summary.json`

## 场景总览

| # | 输入 | 关键验收点 | 实际 status | 草稿 | 输出 / Trace |
|---:|---|---|---|---:|---|
| 01 | [normal-available](cases/01-normal-available.json) | 正常排期、草稿、不发布 | `draft_saved` | 1 | [output](outputs/01-normal-available.json) · [trace](traces/01-normal-available-run1.jsonl) |
| 02 | [conflict-alternative](cases/02-conflict-alternative.json) | 时段冲突、替代 19:00、pending、重复提交 | `draft_saved_pending_confirmation` ×2 | 1 | [output](outputs/02-conflict-alternative.json) · [trace1](traces/02-conflict-alternative-run1.jsonl) · [trace2](traces/02-conflict-alternative-run2.jsonl) |
| 03 | [missing-information](cases/03-missing-information.json) | 信息缺失、补查、阻断 | `blocked_missing_info` | 0 | [output](outputs/03-missing-information.json) · [trace](traces/03-missing-information-run1.jsonl) |
| 04 | [rule-risk-block](cases/04-rule-risk-block.json) | 规则 blocking | `blocked_rule_risk` | 0 | [output](outputs/04-rule-risk-block.json) · [trace](traces/04-rule-risk-block-run1.jsonl) |
| 05 | [warning-needs-review](cases/05-warning-needs-review.json) | 规则 warning、人工复核 | `draft_saved_needs_review` | 1 | [output](outputs/05-warning-needs-review.json) · [trace](traces/05-warning-needs-review-run1.jsonl) |
| 06 | [save-failure](cases/06-save-failure.json) | 保存失败、不虚报 | `save_failed` | 0 | [output](outputs/06-save-failure.json) · [trace](traces/06-save-failure-run1.jsonl) |
| 07 | [hard-time-no-change](cases/07-hard-time-no-change.json) | 禁止改硬性时间 | `blocked_time_change_not_allowed` | 0 | [output](outputs/07-hard-time-no-change.json) · [trace](traces/07-hard-time-no-change-run1.jsonl) |
| 08 | [step-limit](cases/08-step-limit.json) | 步数上限、未完成 | `step_limit_reached` | 0 | [output](outputs/08-step-limit.json) · [trace](traces/08-step-limit-run1.jsonl) |

汇总：**8 个场景 / 9 次运行 / 3 份草稿 / 0 次发布 / 8 个场景检查通过**。

## 参考实例怎么读

打开 `outputs/02-conflict-alternative.json`：

1. 第一次运行的 `slot_query.requested.status = occupied`，冲突对象是 M99；
2. `alternatives[0] = 19:00`；
3. `confirmation` 为 `pending_user_confirmation`，`approved=false`；
4. `draft.requested_publish_at = 18:00`，`proposed_publish_at = 19:00`；
5. `published=false`；
6. 第二次运行 `deduplicated=true`，`store_summary.draft_count=1`。

Trace 的每一步包含：

```json
{
  "event": "step",
  "step": 4,
  "action": "tool",
  "tool": "request_confirmation",
  "arguments": {"material_id": "M01", "reason": "slot_deviation", "...": "..."},
  "decision_basis": "请求时段冲突，先生成待确认项...",
  "observation": {
    "ok": true,
    "summary": "创建待确认项 CONF-0001...",
    "data": {"status": "pending_user_confirmation", "approved": false}
  }
}
```

最后一行 `run_end` 会汇总 `status / published / draft_saved / confirmation_id`。

## Mock 边界

`provider=mock` 使用确定性的观察驱动策略，证明：

- 动作 → 工具 → 观察 → 下一步的循环确实在跑；
- 规则、冲突、确认、幂等、失败、上限逻辑正确；
- 输出可复现。

它**不**证明真实大模型决策质量。真实模型入口见 README 的 `--provider openai`；没有 Key 时不会偷偷回退到 Mock。

## 重新生成

```bash
python examples/run_examples.py
```

该命令会重置 `.runtime/examples/<case>`，不会修改 `data/`。生成的输出含运行时间戳与 run_id，因此内容会变化，但 state 断言应保持：
`published=false`、02 只有一份草稿、06 无草稿、08 为 limit。
