# 01 · 需求、范围与成功标准

## 1. 用户与目标

**主要用户**

- 短视频创作者：给出主题、受众、平台、目标时长和风格，拿到可编辑脚本。
- 品牌/运营审核：检查分镜时长、素材来源、产品事实与风险。

**核心目标**

1. 生成标题、分镜、旁白/字幕。
2. 用工具估算总时长，超时后压缩并重新估算。
3. 用工具检查品牌规则、事实支持、素材授权和语速。
4. 输出素材来源 ID、风险、任务状态。
5. 没有效果数据时，不生成效果结论。
6. 无法满足时长/规则时明确失败，不把 draft 标成 ready。

## 2. 输入

```json
{
  "request_id": "REQ-SV-01",
  "theme": "通勤降噪耳机",
  "audience": "通勤上班族",
  "platform": "douyin",
  "target_duration_seconds": 30,
  "style": "简洁可信",
  "product": "耳机"
}
```

| 字段 | 说明 |
|---|---|
| `theme` | 主题，必填 |
| `audience` / `platform` | 关键信息；缺失则阻断 |
| `target_duration_seconds` | 目标时长；缺失则阻断 |
| `style` | 风格；可影响文案，但不能突破事实/规则 |
| `product` | 产品线索，用于检索 |

**素材**

- P01：仅包含“支持字幕”的产品事实。
- P02：“有三种配色”。
- P03：无产品事实，仅场景空镜。
- P04：授权过期，检索排除。
- P05：“包含三种耳塞尺寸”。

## 3. 输出

| 输出 | 说明 |
|---|---|
| `title` | 脚本标题 |
| `storyboard` / `script.scenes` | 分镜、画面、旁白、字幕、秒数 |
| `total_duration_seconds` | 工具估算总时长 |
| `sources` | 素材 ID、授权、facts |
| `duration_check` | 目标/估算/是否满足/缺口 |
| `rule_check` | pass/blocked + violations |
| `revisions` | 38 → 28 → 14 的时长轨迹 |
| `risks` | 规则/时长风险 |
| `status` | script_ready / duration_unmet / rule_blocked / … |

## 4. 关键语义

### 4.1 时长

- `estimate_duration` 汇总每个分镜的 `seconds`。
- 同时计算每个分镜的字数/秒，语速阈值 2–6 字/秒。
- 总时长 > 目标 → `duration_exceeded`，不能 ready。

参考：

```text
30 秒目标
首稿：10+10+9+9 = 38 秒
压缩稿：7+7+7+7 = 28 秒
28 <= 30 → 时长通过
```

### 4.2 事实

- `claims[].text` 必须能在同一 `material_id` 的 `facts` 中找到。
- P01 只能支持“支持字幕”；不能支持“效率提升 80%”。
- 分镜 `material_ids` 必须存在且 `usable=true`。

### 4.3 规则

`check_rules` 检查：

1. 必填字段；
2. 平台目标时长范围（douyin 10–60）；
3. 总时长是否超目标；
4. 分镜语速；
5. 禁用表述（“效率提升 80%”“保证”“第一”等）；
6. 字幕；
7. 素材存在/可用；
8. claims 事实支持。

任一 blocking → `blocked`，不能输出 ready 脚本。

### 4.4 修正

- 38 秒超时 → 28 秒压缩稿 → 重新估算。
- 28 秒仍超时 → 14 秒最短稿 → 重新估算。
- 14 秒仍超时 → `duration_unmet`，明确差距。
- 规则检查发现无依据效果 claim → 删除后重新估算/检查。
- 不能把 blocked/超时脚本标成 ready。

## 5. 范围

### In scope

- CLI：`run / list-materials / show-material / show-rules / verify`
- 4 个工具：任务简报、素材检索、时长估算、规则检查
- 38→28 修正、规则风险修正、超时长失败、工具失败
- 8 组输入、实际脚本、Trace、39 个测试

### Out of scope

- 真实视频渲染、TTS、字幕文件导出。
- 真实产品库/法务规则/素材授权系统。
- 语义检索/embedding。
- 通用自由视频创作；分镜模板有限。
- 多轮协作/版本管理。

## 6. 验收矩阵

| 必须验收点 | 输入 | 实际输出 | 证据 |
|---|---|---|---|
| 正常生成 | 01，30 秒 | 38→28，分镜/旁白/字幕/P01 | [01 output](../examples/outputs/01-normal-30s.json) |
| 缺关键信息 | 02，缺受众/目标 | `blocked_missing_fields` | [02 output](../examples/outputs/02-missing-info.json) |
| 无素材 | 03，宠物猫粮 | `insufficient_material` | [03 output](../examples/outputs/03-no-material.json) |
| 工具失败 | 05/06 | `tool_failure`，不 ready | 05/06 output |
| 超时长修正 | 01 | 38→28，ready | 01 revisions |
| 超时长失败 | 04，目标 10 | 14 秒仍超，`duration_unmet` | [04 output](../examples/outputs/04-duration-unmet.json) |
| 无效果数据不编造 | 07，夸张风格 | 删除“效率提升 80%”后通过 | [07 output](../examples/outputs/07-rule-risk-clean.json) |
| 事实追溯 | 01/07 | P01 facts=支持字幕 | sources + claims |
| 时长变化 | 08，目标 20 | 38→28→14，ready | [08 output](../examples/outputs/08-target-20s.json) |
| 模型谎报 | 单测 | 运行时覆盖为 duration/rule | agent tests |

**实际总览：8 个场景 / 8 次运行 / 3 script_ready / 1 缺信息 / 1 无素材 / 1 超时失败 / 2 工具失败 / 39 tests。**

## 7. 成功标准

1. 参考实例 38→28，最终 28 ≤ 30。
2. 最终脚本无“效率提升 80%”等无依据结论。
3. 每个 claim 有 P01 事实支持。
4. 超时长无法修正时明确失败，不 ready。
5. 工具失败无 ready 脚本。
6. 改变目标时长，修正动作相应改变。
