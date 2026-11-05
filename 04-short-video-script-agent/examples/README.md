# examples/ 实际脚本与检查记录

由 `python examples/run_examples.py` 生成，不是手写结果。

- 输入：`examples/cases/*.json`
- 实际输出：`examples/outputs/*.json`
- Trace：`examples/traces/*.jsonl`
- 总览：`examples/outputs/index.json`
- 测试：`examples/test-results.txt`、`examples/test-summary.json`

## 总览

| # | 场景 | 验收点 | status | 总时长 |
|---:|---|---|---|---:|
| 01 | [normal-30s](cases/01-normal-30s.json) | 正常生成、38→28、P01 | `script_ready` | 28 |
| 02 | [missing-info](cases/02-missing-info.json) | 缺关键信息 | `blocked_missing_fields` | — |
| 03 | [no-material](cases/03-no-material.json) | 无素材 | `insufficient_material` | — |
| 04 | [duration-unmet](cases/04-duration-unmet.json) | 超时长失败 | `duration_unmet` | 14 |
| 05 | [search-failure](cases/05-search-failure.json) | 工具失败 | `tool_failure` | — |
| 06 | [check-failure](cases/06-check-failure.json) | 规则检查失败 | `tool_failure` | 28 |
| 07 | [rule-risk-clean](cases/07-rule-risk-clean.json) | 无依据 claim 修正 | `script_ready` | 28 |
| 08 | [target-20s](cases/08-target-20s.json) | 目标变化触发 14 秒稿 | `script_ready` | 14 |

合计：**8 个场景 / 8 次运行 / 3 script_ready / 1 缺信息 / 1 无素材 / 1 超时失败 / 2 工具失败**。

## 参考实例怎么读

打开 [01 输出](outputs/01-normal-30s.json)：

1. `revisions` = [38, 28]；
2. `total_duration_seconds` = 28，`target` = 30；
3. `script.scenes` 有 4 个分镜，含 visual/voiceover/subtitle/seconds；
4. `sources[0]` = P01，`facts=["支持字幕"]`；
5. `rule_check.decision=pass`；
6. `script.ready=true`；
7. 脚本不含“效率提升 80%”。

打开 [04 输出](outputs/04-duration-unmet.json)：

- `revisions` = [38, 28, 14]；
- 目标 10，最短 14，gap 4；
- `script.ready=false`；
- `duration_unmet`，不虚报。

打开 [07 输出](outputs/07-rule-risk-clean.json)：

- 首稿/压缩稿带“效率提升 80%”；
- `rule_check` 报 `unsupported_claim` + `forbidden_phrase`；
- 清理后重新估算 28、重新检查 pass；
- 最终脚本不含该结论。

打开 [08 输出](outputs/08-target-20s.json)：

- 目标 20；
- 38 → 28 → 14；
- 14 秒版本通过；
- 证明目标变化会改变修正路径。

## Trace 结构

```json
{
  "event": "step",
  "step": 3,
  "action": "tool",
  "tool": "estimate_duration",
  "arguments": {"script": {"draft_version": "long", "...": "..."}},
  "decision_basis": "首稿超出目标时长，压缩到 28 秒后重新估算。",
  "observation": {
    "ok": true,
    "summary": "估算 38.0 秒，超过目标 8.0 秒",
    "data": {"total_seconds": 38.0, "within_target": false}
  }
}
```

最后一行 `run_end` 汇总 status、total_seconds、target_seconds。

## 重新生成

```bash
python examples/run_examples.py
python scripts/run_tests.py
```

## Mock 边界

Mock 证明时长估算、38→28→14、claims 事实检查、规则修正和失败处理可运行；不证明真实模型创作质量。真实模型用 `--provider openai`，需自备 Key。
