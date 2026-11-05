# 03 · 测试、实际结果、失败与限制

## 1. 复现命令

```bash
# 1) 生成 8 组实际输出与 Trace
python examples/run_examples.py

# 2) 运行全部测试并写入 examples/test-results.txt
python scripts/run_tests.py

# 3) 快速人工检查
python run.py verify
python run.py run --case examples/cases/02-conflict-alternative.json --store .runtime/check --reset
```

实际记录：

- [examples/test-results.txt](../examples/test-results.txt)
- [examples/test-summary.json](../examples/test-summary.json)
- [examples/outputs/index.json](../examples/outputs/index.json)
- [examples/traces/](../examples/traces/)

## 2. 测试结果

```text
tests_run: 47
failures: 0
errors: 0
skipped: 0
success: True
```

测试分布：

| 测试文件 | 数量 | 覆盖 |
|---|---:|---|
| `tests/test_rules.py` | 6 | 正常、缺字段、版权非法、禁用表述、敏感 warning、版本 |
| `tests/test_slots.py` | 5 | 18:00 冲突、19:00 最近、可用时段、自己的草稿忽略、时区、素材不存在 |
| `tests/test_save.py` | 11 | 正常保存、幂等、偏离无确认拒绝、pending 保存、伪造确认拒绝、禁改时间、冲突、阻断、持久/瞬时失败、无 publish |
| `tests/test_agent.py` | 12 | 正常、冲突、重复、缺资料、规则阻断、warning、保存失败、禁改、步数/工具上限、谎报收口、Trace |
| `tests/test_llm_adapter.py` | 4 | 原生 tool calling 全链路、JSON 回退、无 Key、HTTP 错误 |
| `tests/test_cli.py` | 4 | verify、case 运行、human 输出、无 Key 明确报错 |
| `tests/test_examples_artifacts.py` | 5 | 产物完整性、重复草稿、失败无草稿、Trace limit |

重点断言：

- `test_same_request_is_idempotent`：两次保存返回同一 `draft_id`，文件数 1。
- `test_deviation_with_pending_confirmation_is_saved_as_pending`：requested=18:00 保留，proposed=19:00，`approved=false`。
- `test_confirmed_claim_is_not_accepted_from_model`：伪造 `CONF-9999` 被拒绝。
- `test_hard_time_requirement_cannot_be_changed`：`allow_time_change=false` 时拒绝改时段。
- `test_persistent_save_failure_returns_failure_and_no_draft`：失败后无文件。
- `test_runtime_rejects_model_false_success_claim`：模型 finish 谎报 `draft_saved`，运行时仍返回 `save_failed`。
- `test_tool_set_has_no_publish_endpoint`：工具集合锁定，无 publish。
- `test_native_tool_calling_loop`：本地假 OpenAI-compatible 网关，5 次 tool call 完成 M03 草稿。

## 3. 验收结果（实际输出）

`examples/outputs/index.json` 汇总：

```json
{
  "scenarios": 8,
  "runs": 9,
  "drafts": 3,
  "published": 0,
  "all_checks_passed": true
}
```

| 场景 | 运行次数 | 实际 status | 草稿数 | 关键观察 |
|---|---:|---|---:|---|
| 01-normal-available | 1 | `draft_saved` | 1 | 12:00 可用，审核 pass |
| 02-conflict-alternative | 2 | `draft_saved_pending_confirmation` ×2 | 1 | 18:00 被 M99 占；建议 19:00；第二次 dedup |
| 03-missing-information | 1 | `blocked_missing_info` | 0 | 补回版权，仍缺时长；请求用户补充 |
| 04-rule-risk-block | 1 | `blocked_rule_risk` | 0 | R004 医疗绝对化表述 |
| 05-warning-needs-review | 1 | `draft_saved_needs_review` | 1 | R005 医美 warning，草稿需复核 |
| 06-save-failure | 1 | `save_failed` | 0 | 两次可重试失败后明确停止 |
| 07-hard-time-no-change | 1 | `blocked_time_change_not_allowed` | 0 | 19:00 可用也不能改 |
| 08-step-limit | 1 | `step_limit_reached` | 0 | 2 步上限，有 limit 事件 |

### 3.1 参考实例（02）

实际文件：[`examples/outputs/02-conflict-alternative.json`](../examples/outputs/02-conflict-alternative.json)

第一次运行：

```text
step 3 query_slots   → 18:00 occupied, conflict M99, alternatives 19:00/20:00/21:00
step 4 request_conf  → CONF-0001, status=pending_user_confirmation, approved=false
step 5 save_draft    → DRAFT-0002, requested=18:00, proposed=19:00,
                       status=draft_pending_confirmation, published=false
```

第二次运行：

```text
query_slots           → 自己的草稿不占用，19:00 仍可用
request_confirmation  → 返回同一 CONF-0001
save_schedule_draft   → 返回同一 DRAFT-0002, deduplicated=true
store_summary         → draft_count=1
```

对应 Trace：

- [run1](../examples/traces/02-conflict-alternative-run1.jsonl)：冲突 → 确认 → pending 草稿。
- [run2](../examples/traces/02-conflict-alternative-run2.jsonl)：同草稿幂等。

### 3.2 信息不足（03）

- 初次读取 M02：缺 `duration_minutes`、`copyright`。
- `lookup_material_source`：从“制作系统工单 #A-1024”补回 `copyright=original`。
- 再次审核：仍缺 `duration_minutes`，`blocked`。
- `request_confirmation` 创建补充资料请求，`approved=false`。
- 最终 `blocked_missing_info`，无草稿。

Trace：[`03-missing-information-run1.jsonl`](../examples/traces/03-missing-information-run1.jsonl)

### 3.3 规则风险（04 / 05）

- M04：“三天根治失眠”“无需就医” → R004 blocking → `blocked_rule_risk`，无草稿。
- M06：“医美项目”“医疗建议” → R005 warning → 可保存草稿，但 `draft_needs_review`，必须人工复核。

### 3.4 保存失败（06）

- 注入 `fail_save_times=99`（持久失败）。
- 模型看到 `retryable=true`，原参数重试一次。
- 第二次仍失败 → `save_failed`。
- 结果 `draft.saved=false`，`draft.error.code=save_failed`，store 中 0 份草稿。
- 摘要明确“未发布，不能视为已排期”。

Trace：[`06-save-failure-run1.jsonl`](../examples/traces/06-save-failure-run1.jsonl)

### 3.5 执行上限（08）

- `max_steps=2`：执行 get_material、check_rules 后停止。
- `status=step_limit_reached`，`completed=false`，`terminal=false`。
- Trace 有 `{"event":"limit","kind":"step_limit"}`。
- 不会错误地声称完成。

## 4. 失败与边界

### 4.1 已处理的失败

| 失败 | 系统行为 |
|---|---|
| 素材不存在 | `material_not_found` / `blocked_material_not_found`，不保存 |
| 缺字段 | 先补查；仍缺则请求用户；不保存 |
| 规则 blocking | `review_blocked` / `blocked_rule_risk`，不保存 |
| 时段冲突 | 查替代；用户禁改则阻断；不静默替换 |
| 无待确认的改时段 | `confirmation_required`，不保存 |
| 伪造确认 ID | `confirmation_invalid`，不保存 |
| 重复提交 | 返回原草稿，`deduplicated=true` |
| 保存失败 | `save_failed`，无草稿，不虚报 |
| 步数/工具上限 | 明确 `*_limit_reached`，`completed=false` |
| 模型 finish 谎报 | 运行时按工具事实覆盖 |

### 4.2 Mock 的证明边界

离线 Mock 证明：

- ReAct 循环真的执行了“动作 → 工具 → 观察 → 下一步”；
- 工具边界、规则、冲突、确认、幂等、失败与上限逻辑正确；
- 输出可复现、可审计。

离线 Mock **不能**证明：

- 真实大模型能稳定理解这些边界；
- 真实模型在模糊素材上能做出高质量判断；
- 真实 API 的延迟、成本、限流与错误率。

真实模型适配器只做了本地假网关协议测试；本仓库未使用真实 API Key。评审如需验证模型效果，应配置 `.env` 后跑同一批 `examples/cases/*.json`，并单独记录实际模型输出。

### 4.3 已知限制

1. 没有真实发布接口或审批 UI；只到草稿 + pending。
2. `confirmation.approved` 永远为 false，没有“用户确认后转为 approved”的流程。
3. 单进程 RLock；多进程并发写同一 store 未验证。
4. 固定 09:00–21:00、Asia/Shanghai，不支持跨天/跨时区。
5. 冲突只按同一时刻，不按素材时长做区间重叠。
6. 规则库为合成示例，不构成法务意见。
7. 草稿不支持撤销/覆盖/多方案比较；同素材已有其他草稿会被拒绝。
8. `--fail-save` 是显式测试钩子，不是生产级故障恢复。
9. 真实模型未做线上质量评估。
10. 远程仓库：<https://github.com/ymj408/agent-campus-submission-example>；最终提交 SHA 以远程 `main` 为准。

## 5. 评审建议路径

1. 打开 `examples/outputs/index.json` 看总览（8/9/3/0）。
2. 打开 `02` 输出，核对 requested/proposed/confirmation/published。
3. 打开 `02-run1` 与 `02-run2` Trace，核对步骤与幂等。
4. 打开 `03`、`06`，核对缺资料与保存失败边界。
5. 打开 `test-results.txt`，核对 47 个测试。
6. 运行 README 的 30 秒命令，自行复现。
