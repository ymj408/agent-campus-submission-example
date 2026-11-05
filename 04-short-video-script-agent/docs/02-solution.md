# 02 · 方案、时长修正与事实追溯

## 1. 总体架构

```text
CLI (run.py / cli.py)
  │ 主题/受众/平台/目标时长/风格
  ▼
ScriptAgent (agent.py)
  │ ReAct 循环 + 最终时长/规则收口
  ├── Planner
  │    ├── MockPlanner：38→28→14 + 规则修正
  │    └── OpenAICompatiblePlanner：原生 tool calling
  └── ToolRuntime (tools.py)
       ├── KnowledgeBase：materials + brand_rules
       ├── search_materials
       ├── estimate_duration
       └── check_rules
```

没有视频渲染或数据库；脚本以 JSON 输出，可直接编辑。

## 2. 脚本结构

```json
{
  "title": "通勤降噪耳机｜30 秒字幕功能演示",
  "platform": "douyin",
  "audience": "通勤上班族",
  "target_duration_seconds": 30,
  "scenes": [
    {
      "scene_id": 1,
      "visual": "通勤地铁内，字幕浮层出现。",
      "voiceover": "通勤路上打开字幕……",
      "subtitle": "通勤打开字幕……",
      "seconds": 7,
      "material_ids": ["P01"],
      "claims": [{"text": "支持字幕", "material_id": "P01"}]
    }
  ]
}
```

## 3. 工具

| 工具 | 作用 |
|---|---|
| `get_brief` | 检查 audience/platform/target 是否缺失 |
| `search_materials` | 检索 usable 素材，返回 facts/授权/匹配 |
| `estimate_duration` | 汇总分镜秒数，计算语速和总时长 |
| `check_rules` | 必填、平台范围、语速、禁用词、字幕、素材、claims |

没有 `publish` 或渲染工具；脚本是最终 JSON。

## 4. 时长估算

```text
total = Σ scene.seconds
chars_per_second = speech_chars(scene.voiceover) / scene.seconds
pacing_ok = 2.0 <= chars_per_second <= 6.0
within_target = total <= target
```

- 总时长由工具算，模型自报无效。
- 参考：长稿 10+10+9+9 = 38；短稿 7×4 = 28；迷你稿 4+4+3+3 = 14。

## 5. 规则检查

`check_rules` 的 violations：

| code | 说明 |
|---|---|
| `missing_field` | 必填字段 |
| `target_out_of_range` | 平台目标时长不在范围 |
| `duration_exceeded` | 总时长 > 目标 |
| `pacing_invalid` | 分镜语速不在 2–6 字/秒 |
| `forbidden_phrase` | 命中禁用表述 |
| `missing_subtitle` | 分镜无字幕 |
| `unusable_material` | 素材不存在/过期 |
| `unsupported_claim` | claim 不在该素材 facts |

P01 facts 只有“支持字幕”：

- `claims` 写“支持字幕” → 通过；
- 写“效率提升 80%” → `unsupported_claim` + `forbidden_phrase`；
- 因此夸张风格也必须删除效果结论。

## 6. 修正循环

### 6.1 正常 30 秒

```text
estimate 长稿 → 38，超时 8
estimate 短稿 → 28，通过
check_rules 短稿 → pass
finish script_ready
```

### 6.2 规则风险

```text
estimate 短稿（含“效率提升 80%”）→ 28
check_rules → blocked（unsupported_claim + forbidden_phrase）
estimate 清理稿 → 28
check_rules → pass
finish script_ready
```

清理稿的 `draft_version=short_clean`，避免重复清理。

### 6.3 超时长无法修正

```text
38 → 28 → 14
14 > 10
check_rules → duration_exceeded
finish duration_unmet，gap=4，script.ready=false
```

## 7. 最终收口

`agent._finalize`：

```text
上限/异常
  > 缺关键信息
  > 检索失败
  > 无素材
  > 工具失败
  > 规则 blocked
  > 时长超限
  > script_ready
```

- 最终 script 来自最后一次 `estimate_duration`/`check_rules` 的参数，不采用模型 finish 的 script。
- 最终 total 来自工具 duration，不采用模型自报。
- 规则 blocked 时即使模型 finish `script_ready`，最终仍 `rule_blocked`。
- 时长 38 时即使模型说 28，最终仍 `duration_unmet`。
- `sources` 从 claims/material_ids 回溯到素材 facts。

## 8. 事实追溯

`check_rules` 对每个 claim：

```python
material = kb.get(claim.material_id)
supported = claim.text in material.facts
```

最终 `sources`：

```json
{
  "material_id": "P01",
  "title": "P01 通勤耳机字幕演示片段",
  "license": "original",
  "facts": ["支持字幕"]
}
```

没有效果数据时，脚本不能出现效果结论。

## 9. 技术取舍

| 选择 | 理由 | 代价 |
|---|---|---|
| 分镜秒数显式输入 + 工具汇总 | 可精确复现 38/28/14 | 不是真实 TTS 耗时 |
| claims 结构化追溯 | 可机器验证事实 | 需要模型/作者标注 |
| 规则/时长双检查 | 防止超时和编造 | 多一次工具调用 |
| 模板化修正 | 参考实例可复现 | 创作自由度有限 |
| 本地 JSON | 可编辑、可评审 | 无渲染/导出 |

## 10. 关键文件

- 工具：[`src/script_agent/tools.py`](../src/script_agent/tools.py)
- 修正策略：[`src/script_agent/planner.py`](../src/script_agent/planner.py)
- 最终收口：[`src/script_agent/agent.py`](../src/script_agent/agent.py)
- 规则：[`data/brand_rules.json`](../data/brand_rules.json)
- 参考输出：[`examples/outputs/01-normal-30s.json`](../examples/outputs/01-normal-30s.json)
