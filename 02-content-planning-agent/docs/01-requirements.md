# 01 · 需求、范围与成功标准

## 1. 用户与目标

**主要用户**

- 内容运营：给出活动主题、预算、截止时间，拿到可执行方案。
- 内容负责人：检查方案成本、步骤依赖、失败处理与保存状态。

**核心目标**

1. 在预算和截止时间内选择可行方案。
2. 明确每一步的工具、费用、耗时与依赖。
3. 预算超限时改选替代方案，而不是硬保存。
4. 工具失败时保留已成功结果，重规划或停止并返回缺口。
5. 仅保存本地方案，不接真实发布；重复请求不产生重复方案。
6. 用 Trace 证明 Agent 根据工具观察调整了计划。

## 2. 输入

```json
{
  "request_id": "REQ-CP-01",
  "theme": "秋季通勤穿搭",
  "budget": 10,
  "deadline": "2027-12-31T18:00:00+08:00",
  "audience": "通勤女性",
  "channel": "xiaohongshu"
}
```

| 字段 | 说明 |
|---|---|
| `request_id` | 任务标识 |
| `theme` | 活动主题；必填 |
| `budget` | 模拟预算单位；允许缺失，用于验收“缺预算” |
| `deadline` | ISO 8601；允许缺失，用于验收“缺截止时间” |
| `audience` / `channel` | 受众和渠道，用于检索/脚本/分发 |

**合成资料**

- `data/materials.json`：5 个素材；MAT-04 授权过期，检索时排除。
- `data/tool_costs.json`：5 个工具的费用与耗时。
- `data/plan_templates.json`：4 个方案与显式步骤依赖。

## 3. 输出

| 输出 | 说明 |
|---|---|
| 任务计划 | 选中的方案模板、素材、步骤 |
| 步骤状态 | `pending / succeeded / failed` |
| 工具结果 | 脚本、海报、审核、分发产物路径 |
| 费用合计 | 素材 + 工具费用；预算、剩余、超支 |
| 保存状态 | `saved=true/false`、`plan_id`、`plan_path` |
| 未完成原因 | 缺字段、无素材、无可行方案、工具失败、保存失败、执行上限 |
| 保留结果 | 失败前已成功的步骤及其产物 |

## 4. 成本与依赖语义

### 4.1 成本

```text
plan_total = material.cost + Σ(tool.unit_cost for each step)
```

参考：

| 方案 | 素材 | 工具 | 合计 | 耗时 |
|---|---|---:|---:|---:|
| premium_video | MAT-03 (4) | 2+3+1+2 | **12** | 135 min |
| lean_article | MAT-01 (2) | 2+1+1+2 | **8** | 95 min |
| lean_no_poster | MAT-01 (2) | 2+1+2 | **7** | 75 min |
| organic_text | MAT-05 (1) | 2+1+2 | **6** | 75 min |

预算 10：初始 `premium_video` 超 2；替代 `lean_article` 剩余 2。

### 4.2 截止时间

```text
finish_by = now + Σ(step.minutes)
deadline_ok = finish_by <= deadline
```

超时方案不能保存。

### 4.3 依赖

- `script`：无依赖
- `poster` / `video_edit`：依赖 `script`
- `review`：依赖当前方案中的制作步骤
- `distribution`：依赖 `review`

`execute_step` 会拒绝依赖未成功的步骤；不是提示词层面的“建议”。

## 5. 范围

### In scope

- CLI：`run / list-materials / list-plans / show-plan / verify`
- 7 个工具：任务简报、素材检索、方案列表、成本估算、脚本生成、步骤执行、方案保存
- 预算超限重规划；工具失败保留成功结果；重复执行幂等；执行上限
- 失败步骤续跑（加分项）：checkpoint + `--resume`
- 40 个测试；10 组输入、12 次运行、4 份方案

### Out of scope

- 真实发布/投放平台 API、审批流、预算审批。
- 多用户/多租户、数据库、分布式 Runtime。
- 任意自由组合步骤的通用规划器；方案模板固定。
- 真实素材版权/报价系统。
- 多进程并发写同一 store。

## 6. 验收矩阵

| 必须验收点 | 输入 / 场景 | 实际输出 | 证据 |
|---|---|---|---|
| 正常完成 | 01，预算 10 | 12 超限 → 8 方案 → 保存 | [01 output](../examples/outputs/01-normal-budget-replan.json) |
| 缺预算 | 02，无 budget | `blocked_missing_fields`，不保存 | [02 output](../examples/outputs/02-missing-budget.json) |
| 缺截止时间 | 03，无 deadline | `blocked_missing_fields`，不保存 | [03 output](../examples/outputs/03-missing-deadline.json) |
| 预算超限 | 01 / 04 | 01 改选 8；04 无可行，缺口 3 | 01、04 output |
| 工具失败 | 05 / 06 | 05 海报失败→无海报方案；06 审核失败→停止 | 05、06 output |
| 部分成功保留 | 06 / 07 | script、poster 保留；review failed | 06 output |
| 重复执行 | 08 | 第二次 dedup，方案数 1 | 08 output |
| 失败步骤续跑 | 07 | 第二次复用 script/poster，只补 review/distribution | 07 output |
| 执行上限 | 10 | `step_limit_reached` | 10 output |
| 不虚报 | 09 + false-claim 测试 | save 失败 → `save_failed`；模型 finish 谎报被覆盖 | 09 + test |
| 依赖与成本 | 单测/实际输出 | 依赖拒绝、成本 12/8/7/6 | test-tools |

**实际总览：10 个场景 / 12 次运行 / 4 份已保存方案 / 10/10 场景检查通过 / 40 个测试通过。**

## 7. 成功标准

1. 参考实例：预算 10，初始 12 超限，模型选择 8，重新估算并保存。
2. 没有可行方案时返回缺口，不保存。
3. 工具失败时能指出失败步骤，保留成功结果，且不会把 failed 写成 succeeded。
4. 重复执行只保留一份方案。
5. 所有输出包含成本、步骤状态、保存状态或未完成原因。
6. 执行步数/工具调用超限时返回明确未完成状态。
