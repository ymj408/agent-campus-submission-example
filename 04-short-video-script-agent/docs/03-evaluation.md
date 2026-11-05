# 03 · 测试、实际结果、失败与限制

## 1. 复现

```bash
python examples/run_examples.py
python scripts/run_tests.py
python run.py verify
```

证据：

- [examples/test-results.txt](../examples/test-results.txt)
- [examples/test-summary.json](../examples/test-summary.json)
- [examples/outputs/index.json](../examples/outputs/index.json)
- [examples/traces/](../examples/traces/)

## 2. 测试结果

```text
tests_run: 39
failures: 0
errors: 0
skipped: 0
success: True
```

| 测试文件 | 数量 | 覆盖 |
|---|---:|---|
| `tests/test_agent.py` | 13 | 38→28、缺信息、无素材、超时失败、规则修正、工具失败、谎报、目标变化、上限、Trace |
| `tests/test_tools.py` | 11 | 时长 38/28/14、规则 pass/blocked、禁用表述、无依据 claim、语速、过期素材、失败 |
| `tests/test_cli.py` | 4 | verify、case 输出、超时 human 输出、无 Key |
| `tests/test_llm_adapter.py` | 4 | 原生 tool calling、JSON 回退、无 Key、HTTP 错误 |
| `tests/test_examples_artifacts.py` | 7 | 产物总览、38→28、超时失败、规则清理、目标变化、Trace |

关键断言：

- `test_long_script_is_38_seconds` / `test_short_script_is_28_seconds`。
- `test_unsupported_effect_claim_is_blocked`：unsupported_claim + forbidden_phrase。
- `test_runtime_rejects_false_ready_on_rule_violation`：模型谎报 ready，最终 rule_blocked。
- `test_runtime_rejects_false_duration_claim`：模型说 28，工具 38，最终 duration_unmet。
- `test_rule_risk_is_cleaned`：38→28→28，删除效果 claim 后通过。
- `test_target_change_changes_revision_path`：20 秒目标触发 38→28→14。
- `test_no_material` / `test_missing_critical_info` / 工具失败。

## 3. 实际运行结果

`examples/outputs/index.json`：

```json
{
  "scenarios": 8,
  "runs": 8,
  "script_ready": 3,
  "blocked_missing_fields": 1,
  "insufficient_material": 1,
  "duration_unmet": 1,
  "tool_failure": 2,
  "all_checks_passed": true
}
```

| 场景 | status | 总时长 | 关键结果 |
|---|---|---:|---|
| 01-normal-30s | `script_ready` | 28 | 38→28，P01，规则 pass |
| 02-missing-info | `blocked_missing_fields` | — | 缺 audience/target |
| 03-no-material | `insufficient_material` | — | 宠物猫粮无素材 |
| 04-duration-unmet | `duration_unmet` | 14 | 目标 10，仍超 4 秒 |
| 05-search-failure | `tool_failure` | — | 不输出脚本 |
| 06-check-failure | `tool_failure` | 28 | 有候选但未确认规则，ready=false |
| 07-rule-risk-clean | `script_ready` | 28 | 删除“效率提升 80%”后 pass |
| 08-target-20s | `script_ready` | 14 | 38→28→14 |

### 3.1 参考实例（01）

[`examples/outputs/01-normal-30s.json`](../examples/outputs/01-normal-30s.json)

```text
get_brief
search_materials → P01
estimate_duration(long)  → 38 > 30
estimate_duration(short) → 28 <= 30
check_rules              → pass
finish script_ready
```

最终：

- `total_duration_seconds = 28`
- `revisions = [38, 28]`
- 4 个分镜，均有旁白/字幕
- `sources = [P01 facts=支持字幕]`
- `script.ready = true`
- 不含“效率提升 80%”

### 3.2 超时长失败（04）

- 目标 10 秒。
- 38 → 28 → 14。
- 14 > 10，`duration_unmet`。
- `duration_check.gap_seconds = 4`。
- `script.ready = false`，不虚报满足时长。

### 3.3 无依据效果 claim（07）

- 风格“夸张营销”使压缩稿加入“效率提升 80%”。
- `check_rules` 同时报：
  - `unsupported_claim`：P01 facts 中没有该结论；
  - `forbidden_phrase`：命中禁用表述。
- 删除后重新估算 28 秒，重新检查 pass。
- 最终脚本不含该结论。

### 3.4 目标变化（08）

- 目标 20 秒。
- 38 → 28 → 14，14 <= 20，`script_ready`。
- 证明时长目标变化会改变修正路径，不是固定 28 秒输出。

### 3.5 工具失败（05/06）

- 05：search 连续失败 → `tool_failure`，无脚本。
- 06：estimate 成功 28，check 连续失败 → `tool_failure`，`script.ready=false`。

## 4. 失败与边界

### 4.1 已处理

| 情况 | 行为 |
|---|---|
| 缺关键信息 | `blocked_missing_fields` |
| 无素材 | `insufficient_material` |
| 首稿 38 秒 | 压缩到 28 后重估 |
| 28 秒仍超时 | 压缩到 14 后重估 |
| 最短仍超时 | `duration_unmet` + gap |
| 无依据 claim | `unsupported_claim` + 禁用词 |
| 规则阻断 | 删除/修正后重检，或 `rule_blocked` |
| 工具失败 | `tool_failure`，不 ready |
| 模型谎报 ready | 运行时覆盖 |
| 模型谎报 28 秒 | 工具 38 秒覆盖 |
| 执行上限 | 明确未完成 |

### 4.2 Mock 边界

Mock 证明：

- 38/28/14 时长轨迹；
- P01 facts 支持与无依据 claim 阻断；
- 规则修正、超时失败、工具失败；
- 最终状态/时长/来源由工具收口。

Mock **不证明**：

- 真实模型创作质量；
- 真实 TTS/剪辑耗时；
- 真实品牌/法务规则的完整性。

真实模型适配器只做了本地假 OpenAI-compatible 网关测试，没有真实 API Key。

### 4.3 已知限制

1. 分镜秒数是显式输入，不是 TTS 实测。
2. 素材/规则为合成数据。
3. 检索是关键词匹配，不是语义检索。
4. 分镜模板固定，创作自由度有限。
5. 没有视频渲染/字幕文件导出。
6. 真实模型未线上验证。
7. `--fail-tool` 是验收钩子。
8. 没有多人协作/版本管理。

## 5. 评审建议路径

1. 看 `examples/outputs/index.json`（8/3/1/1/1/2）。
2. 看 01 输出，核对 38→28、P01、分镜/字幕。
3. 看 04 输出，核对 14 秒仍超 10 秒、ready=false。
4. 看 07 输出，核对无依据 claim 已删除。
5. 看 08 输出，核对目标变化触发 14 秒稿。
6. 看 `examples/test-results.txt`（39 tests）。
