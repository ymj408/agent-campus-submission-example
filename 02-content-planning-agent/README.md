# 内容任务规划 Agent

> 校招 AI Agent 开发考核 · 项目题 2  
> 目标：在预算与截止时间内，为运营人员产出一份可执行、可核验的内容活动方案。

## 一句话结论

本项目实现了一个带工具调用循环的内容规划 Agent：先读任务与素材，再估算方案成本，超预算时改选替代方案，按依赖执行脚本/海报/审核/分发，最后只在全部步骤成功且预算内保存方案。工具失败时保留成功步骤、重规划或续跑，不把 failed/pending 写成 succeeded，也不虚报方案保存成功。

## 关键事实

| 事实 | 设计 |
|---|---|
| 产物 | 本地 `PLAN-*.json` 方案 + `artifacts/`；没有发布工具 |
| 预算口径 | 素材成本 + 工具费用；保存前重新核验 |
| 截止时间 | 按步骤总耗时计算完成时间，超时不能保存 |
| 初始方案 | `premium_video` = 12 |
| 替代方案 | `lean_article` = 8；预算 10 时自动改选 |
| 步骤依赖 | `script → poster/video_edit → review → distribution`，工具强制执行 |
| 工具失败 | 失败步骤标记 `failed`；成功步骤保留；可改选无失败步骤的方案 |
| 重复执行 | 任务指纹索引；第二次直接返回同一方案，不重复执行 |
| 失败步骤续跑 | `--resume` 从 checkpoint 复用成功步骤，只补失败/未执行步骤（加分项） |
| 执行上限 | 步数 + 工具调用上限，超限返回明确未完成 |
| Mock | 验证流程、预算、依赖、失败和幂等；不证明真实模型决策质量 |
| 真实模型 | OpenAI-compatible Chat Completions + 原生 tool calling；需自备 Key |

## 环境与安装

- Python **3.11+**（开发验证：Python 3.13.5）
- 运行时**零第三方依赖**（仅标准库）

```bash
git clone https://github.com/ymj408/agent-campus-submission-example.git
cd agent-campus-submission-example/02-content-planning-agent
python run.py verify
```

不需要 `pip install`。如希望安装为命令（可选）：

```bash
pip install -e .
content-planning-agent verify
```

## 30 秒评审入口

1. [docs/01-requirements.md](docs/01-requirements.md)：需求、范围、验收矩阵
2. [examples/outputs/01-normal-budget-replan.json](examples/outputs/01-normal-budget-replan.json)：预算 10，初始 12 超限，改选 8 并保存
3. [examples/traces/01-normal-budget-replan-1-normal.jsonl](examples/traces/01-normal-budget-replan-1-normal.jsonl)：两步估算与改选过程
4. [examples/outputs/05-poster-failure-replan.json](examples/outputs/05-poster-failure-replan.json)：海报失败 → 无海报方案，保留脚本
5. [examples/outputs/07-resume-failed-step.json](examples/outputs/07-resume-failed-step.json)：审核失败后从检查点续跑
6. [examples/test-results.txt](examples/test-results.txt)：40 个测试全部通过

## 运行方式

### 1. 正常完成（预算 10）

```bash
python run.py run \
  --case examples/cases/01-normal-budget-replan.json \
  --store .runtime/demo/normal \
  --reset
```

预期：

```text
premium_video 估算 12，超预算 2
lean_article 估算 8，预算内
script → poster → review → distribution 全部成功
保存 PLAN-*，总成本 8，剩余 2
```

### 2. 缺预算 / 缺截止时间

```bash
python run.py run --case examples/cases/02-missing-budget.json --store .runtime/demo/no-budget --reset
python run.py run --case examples/cases/03-missing-deadline.json --store .runtime/demo/no-deadline --reset
```

预期：`blocked_missing_fields`，不估算、不保存。

### 3. 预算超限且没有可行方案

```bash
python run.py run --case examples/cases/04-no-feasible-plan.json --store .runtime/demo/low --reset
```

预算 3，最低方案 6，预期 `incomplete_no_feasible_plan`，缺口 3，不保存。

### 4. 工具失败与重规划

```bash
# 海报失败，改选无海报方案，脚本保留，保存成功
python run.py run --case examples/cases/05-poster-failure-replan.json --store .runtime/demo/poster --reset

# 审核失败，所有方案都需要审核，停止并保留脚本/海报
python run.py run --case examples/cases/06-review-failure-preserve.json --store .runtime/demo/review --reset
```

### 5. 失败步骤续跑（加分项）

```bash
# 第一次：审核注入失败，返回 incomplete_tool_failure
python run.py run --case examples/cases/07-resume-failed-step.json --run-index 0 --store .runtime/demo/resume --reset

# 第二次：同一 store + --resume；脚本/海报从检查点复用，只补审核与分发
python run.py run --case examples/cases/07-resume-failed-step.json --run-index 1 --store .runtime/demo/resume --resume
```

### 6. 重复执行

```bash
python run.py run --case examples/cases/08-duplicate-execution.json --run-index 0 --store .runtime/demo/dup --reset
python run.py run --case examples/cases/08-duplicate-execution.json --run-index 1 --store .runtime/demo/dup
```

预期第二次 `plan_saved_deduplicated`，只执行 `get_brief`，方案数仍为 1。

### 7. 保存失败 / 执行上限

```bash
python run.py run --case examples/cases/09-save-failure.json --store .runtime/demo/savefail --reset
python run.py run --case examples/cases/10-step-limit.json --store .runtime/demo/limit --reset
```

### 8. 生成全部运行记录与测试

```bash
python examples/run_examples.py
python scripts/run_tests.py
```

当前实际结果：**10 个场景 / 12 次运行 / 4 份已保存方案 / 40 个测试通过**。

## 真实模型配置

```bash
cp .env.example .env
# 填写 AGENT_MODEL_BASE_URL / AGENT_MODEL_NAME / AGENT_MODEL_API_KEY
python run.py run \
  --case examples/cases/01-normal-budget-replan.json \
  --provider openai \
  --store .runtime/openai/normal \
  --reset
```

- 使用原生 `tools` / `tool_calls`；纯 JSON 动作作为兼容回退。
- 只从环境变量读取 Key；不打印、不写入 Trace。
- 没有真实 Key 时明确报错，不会静默回退 Mock。
- 本仓库的 `mock` 运行记录不代表真实模型效果；真实模型需另跑同一批 case。

## 结果状态

| status | 含义 | saved |
|---|---|---|
| `plan_saved` | 预算内、截止可行、所有步骤成功，方案已保存 | true |
| `plan_saved_deduplicated` | 重复任务命中已有方案，未重复执行 | true |
| `blocked_missing_fields` | 缺预算或截止时间 | false |
| `incomplete_no_material` | 没有可用素材 | false |
| `incomplete_no_feasible_plan` | 所有候选方案都超预算/超时 | false |
| `incomplete_tool_failure` | 工具失败且没有可行替代；保留成功结果 | false |
| `incomplete_deadline` | 所有方案都无法在截止前完成 | false |
| `save_failed` | 步骤成功但方案写入失败 | false |
| `step_limit_reached` / `tool_limit_reached` | 达到执行上限，未完成 | false |

## 目录结构

```text
.
├── README.md
├── run.py
├── pyproject.toml
├── requirements.txt
├── .env.example
├── data/
│   ├── materials.json
│   ├── tool_costs.json
│   ├── plan_templates.json
│   └── README.md
├── src/content_planner/
│   ├── cli.py
│   ├── agent.py
│   ├── planner.py
│   ├── llm.py
│   ├── tools.py
│   ├── repository.py
│   ├── models.py
│   ├── timeutil.py
│   ├── trace.py
│   └── prompts.py
├── tests/                         # 40 个 unittest
├── examples/
│   ├── cases/                     # 10 组输入
│   ├── outputs/                   # 实际输出 + index
│   ├── traces/                    # 实际 Trace
│   ├── run_examples.py
│   ├── test-results.txt
│   └── test-summary.json
├── scripts/run_tests.py
└── docs/01-requirements.md … 05-retrospective.md
```

## 设计取舍

- **成本是预算数据，不是模型口头数字**：`estimate_plan` 从素材和工具费用表计算，保存时再核验。
- **步骤依赖工具化**：`execute_step` 检查 `depends_on`，模型不能跳过脚本直接执行审核。
- **失败不是异常终止**：失败步骤进入 `failure_history`，成功结果留在 `step_results`；模型用这些观察选择替代方案。
- **续跑是状态能力**：checkpoint 记录成功步骤，`--resume` 复用，避免重复调用和重复计费。
- **保存与发布分离**：只写 `plans/` 和 `artifacts/`，没有 publish 工具。

## 已知限制

1. 只保存方案，不接真实发布/投放平台。
2. 真实模型没有线上 Key 验证；Mock 不等于模型效果。
3. 成本表、素材库均为合成数据，生产需接入真实报价/授权系统。
4. 单进程文件锁；不支持多进程并发写同一 store。
5. 截止时间按步骤总耗时串行估算，不支持并行排期。
6. 方案模板固定；没有任意自由组合步骤的规划器。
7. `--fail-step` 是验收用故障注入，不是生产容错。
8. 未实现审批工作流、预算审批、素材版权人工复核。

## 提交信息

- **仓库**：<https://github.com/ymj408/agent-campus-submission-example>
- **项目目录**：`02-content-planning-agent/`
- **题号**：项目题 2 · 内容任务规划 Agent
- **最终 commit SHA**：见远程 `main` 最新提交或提交回复
- **AI 使用记录**：[docs/04-ai-usage.md](docs/04-ai-usage.md)
