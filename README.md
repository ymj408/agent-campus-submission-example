# 素材审核与发布排期 Agent

> 校招 AI Agent 开发考核 · 项目题 1（100 分）  
> 目标：帮助内容团队找出待处理素材，形成**无冲突、可确认**的发布排期草稿。

## 一句话结论

本项目实现了一个可运行的 ReAct 风格 CLI Agent：模型/策略选择动作 → 调用素材、规则、时段或草稿工具 → 观察返回 → 调整下一步。系统**只保存草稿，不接真实发布**；当建议时间偏离用户硬性要求时，必须保留原要求并生成 `pending_user_confirmation` 待确认项，不能静默改时间，也不能虚报保存成功。

## 关键事实

| 事实 | 设计 |
|---|---|
| 产物 | 仅排期草稿（`DRAFT-*.json`），没有 publish 工具 |
| 硬性时间 | `requested_publish_at` 由任务绑定，模型不能通过参数覆盖 |
| 改时段 | 只能建议 + 创建 pending 待确认；`allow_time_change=false` 时直接阻断 |
| 重复提交 | 幂等键 = 素材 + 原要求时间 + 建议时间；重复保存返回同一草稿 |
| 保存失败 | 返回 `save_failed`，不创建草稿，不声称成功 |
| 最终状态 | 由工具事实（草稿/审核错误）推导，模型 `finish` 自报不能覆盖 |
| 执行上限 | 步骤上限 + 工具调用上限，超限返回 `step_limit_reached` / `tool_limit_reached` |
| 离线模式 | `mock`：仅验证流程、边界、幂等，**不证明真实模型决策质量** |
| 真实模型 | `openai`：OpenAI-compatible Chat Completions + 原生 tool calling；需自备 API Key |

## 环境与安装

- Python **3.11+**（开发验证：Python 3.13.5）
- 运行时**零第三方依赖**（标准库）
- 无数据库、无 Docker、无前端依赖

```bash
git clone <this-repo>
cd media-review-scheduler-agent
python --version
python run.py verify
```

Windows PowerShell / macOS / Linux 的命令一致；不需要 `pip install`。  
如需以包形式安装（可选）：

```bash
pip install -e .
media-scheduler-agent verify
```

## 30 秒评审入口

先看四个文件，所有结论都有可复现证据：

1. [docs/01-requirements.md](docs/01-requirements.md)：用户、范围、成功标准
2. [examples/outputs/02-conflict-alternative.json](examples/outputs/02-conflict-alternative.json)：18:00 冲突 → 建议 19:00 → pending；重复提交一份草稿
3. [examples/traces/02-conflict-alternative-run1.jsonl](examples/traces/02-conflict-alternative-run1.jsonl)：逐步骤动作、参数、观察与决策依据
4. [examples/test-results.txt](examples/test-results.txt)：47 个测试全部通过
5. [examples/outputs/index.json](examples/outputs/index.json)：8 个场景、9 次运行、3 份草稿、0 次发布

## 运行方式

### 1. 查看合成素材

```bash
python run.py list
python run.py show M01
```

### 2. 正常排期

```bash
python run.py run \
  --case examples/cases/01-normal-available.json \
  --store .runtime/demo/normal \
  --reset
```

预期：`draft_saved`，草稿 `draft_ready`，`published=false`。

### 3. 时段冲突 + 待确认（参考实例）

```bash
python run.py run \
  --case examples/cases/02-conflict-alternative.json \
  --store .runtime/demo/conflict \
  --reset
```

预期：18:00 被 M99 占用，模型建议 **19:00**，创建 `CONF-*` pending，草稿保存为 `draft_pending_confirmation`；摘要明确“未发布，等待用户确认”。

重复提交（**不要**加 `--reset`，复用同一 store）：

```bash
python run.py run \
  --case examples/cases/02-conflict-alternative.json \
  --store .runtime/demo/conflict
```

预期：返回同一个 `DRAFT-*` 与同一个 `CONF-*`，`deduplicated=true`，`drafts/` 下只有一份 JSON。

### 4. 信息缺失、规则风险、保存失败

```bash
# 补查后仍缺时长：blocked_missing_info，不保存草稿
python run.py run --case examples/cases/03-missing-information.json --store .runtime/demo/missing --reset

# 医疗绝对化表述：blocked_rule_risk，不保存草稿
python run.py run --case examples/cases/04-rule-risk-block.json --store .runtime/demo/risk --reset

# 医美主题 warning：草稿保存但需人工复核
python run.py run --case examples/cases/05-warning-needs-review.json --store .runtime/demo/review --reset

# 持久保存失败：save_failed，不虚报成功
python run.py run --case examples/cases/06-save-failure.json --store .runtime/demo/fail --reset

# 用户明确不允许改时间：18:00 冲突时直接阻断
python run.py run --case examples/cases/07-hard-time-no-change.json --store .runtime/demo/hard --reset

# 执行上限：两步后返回 step_limit_reached
python run.py run --case examples/cases/08-step-limit.json --store .runtime/demo/limit --reset
```

`06-save-failure.json` 内置 `fail_save_times: 99`；也可以手动注入：

```bash
python run.py run --case examples/cases/01-normal-available.json \
  --store .runtime/demo/manual-fail --reset --fail-save 99
```

### 5. 一次生成全部运行记录

```bash
python examples/run_examples.py
```

输出：

- `examples/outputs/*.json`：每组输入的实际输出（含完整步骤）
- `examples/outputs/index.json`：总览与检查结果
- `examples/traces/*.jsonl`：Trace

### 6. 测试

```bash
# 生成 examples/test-results.txt 与 test-summary.json
python scripts/run_tests.py

# 也可以直接运行
python -m unittest discover -s tests -t . -v
```

当前结果：**47 tests, 0 failures, 0 errors, success=True**。

## 真实模型配置

离线 `mock` 不需要配置。真实模型走 OpenAI-compatible Chat Completions：

```bash
cp .env.example .env
# 编辑 .env：
# AGENT_MODEL_BASE_URL=https://api.deepseek.com/v1
# AGENT_MODEL_NAME=deepseek-chat
# AGENT_MODEL_API_KEY=...
python run.py run \
  --case examples/cases/02-conflict-alternative.json \
  --provider openai \
  --store .runtime/openai/conflict \
  --reset
```

说明：

- 只从环境变量读取密钥，不打印、不写入 Trace、不提交 `.env`。
- 使用原生 `tools` / `tool_calls`；同时支持模型返回纯 JSON 动作的兼容网关。
- 若模型返回 multiple tool calls，只执行第一个（提示词要求一次一个）。
- 离线 Mock 的 8 组运行记录**不代表**真实模型效果；真实模型需要另行跑同一批 case 并比较。
- 本仓库没有可用的真实 API Key，因此 `openai` 适配器只做了本地假网关协议测试；详见 [docs/03-evaluation.md](docs/03-evaluation.md)。

## 结果状态

| status | 含义 | 是否草稿 |
|---|---|---|
| `draft_saved` | 原要求时间可用，草稿已保存 | 是 |
| `draft_saved_pending_confirmation` | 存在待确认时段偏离草稿 | 是，pending |
| `draft_saved_needs_review` | 规则 warning，草稿需人工复核 | 是 |
| `blocked_missing_info` | 信息不足，已请求补充 | 否 |
| `blocked_rule_risk` | 规则 blocking | 否 |
| `blocked_no_available_slot` | 冲突且无替代 | 否 |
| `blocked_time_change_not_allowed` | 禁止改时间且冲突 | 否 |
| `save_failed` | 草稿写入失败 | 否 |
| `step_limit_reached` / `tool_limit_reached` | 达到上限，未完成 | 可能已有草稿，但仍未发布 |
| `agent_error` / `tool_failure` | 执行/工具异常 | 否 |

任何状态的 `published` 都是 `false`；代码中不存在发布工具。

## 目录结构

```text
.
├── README.md
├── run.py                         # 零安装入口
├── pyproject.toml
├── requirements.txt
├── .env.example
├── data/
│   ├── materials.json             # 6 个合成素材
│   ├── rules.json                 # 可解释规则
│   ├── schedule.json              # 已有排期（只读）
│   └── source_notes.json          # 模拟制作系统补查
├── src/media_scheduler_agent/
│   ├── cli.py                     # run/list/show/verify
│   ├── agent.py                   # ReAct 循环 + 权威结果收口
│   ├── planner.py                 # 离线观察驱动策略
│   ├── llm.py                     # OpenAI-compatible 适配器
│   ├── tools.py                   # 6 个业务工具 + 安全边界
│   ├── rules.py                   # 规则引擎
│   ├── repository.py              # 种子数据 + 草稿/确认/幂等存储
│   ├── models.py
│   ├── timeutil.py
│   ├── trace.py
│   └── prompts.py
├── tests/                         # 47 个 unittest
├── examples/
│   ├── cases/                     # 8 组输入
│   ├── outputs/                   # 实际输出 + index
│   ├── traces/                    # 实际 Trace
│   ├── run_examples.py
│   ├── test-results.txt
│   └── test-summary.json
├── scripts/run_tests.py
└── docs/01-requirements.md … 05-retrospective.md
```

## 设计取舍摘要

- **原生实现而非框架**：核心是工具边界、确认语义与幂等，不在框架 API。标准库实现更易审计；没有引入 LangGraph / ADK / AgentScope。
- **规则引擎独立于模型**：模型不能自行宣布“审核通过”；保存草稿时重新审核。
- **确认状态机只有 pending**：`request_confirmation` 永远 `approved=false`；没有“模型替用户点同意”的接口。
- **最终状态以工具事实为准**：即使模型 `finish` 谎报 `draft_saved`，运行时也会根据 `save_error` 返回 `save_failed`（有测试覆盖）。
- **Mock 与真实模型分离**：`mock` 只验证流程；`openai` 才是真实决策入口，避免把离线策略包装成模型能力。

## 已知限制

1. 只保存草稿；没有用户审批 UI，也没有真实发布接口。
2. 真实模型未用线上 Key 验证；适配器协议测试不等于模型效果。
3. 单进程文件锁；不支持多进程并发写同一 store。
4. 发布窗口固定 09:00–21:00，时区固定 Asia/Shanghai；不支持跨天/跨时区排期。
5. 规则库为合成规则；生产使用需要法务/运营复核规则内容。
6. `--fail-save` 是显式故障注入，仅用于验收，不是生产容错。
7. 未实现“同一素材的多方案比较”“草稿撤销/覆盖”“审批通过后写入正式排期”。

## 提交信息

- **题号**：项目题 1 · 素材审核与发布排期 Agent
- **GitHub URL**：待填入候选人公开仓库地址（本工作区已初始化本地 Git）
- **最终 commit SHA**：提交回复中给出；可用 `git rev-parse HEAD` 复核
- **AI 使用与个人贡献**：[docs/04-ai-usage.md](docs/04-ai-usage.md)

> 私有仓库请给指定评审账号只读权限；不要把真实 API Key、品牌素材或用户隐私提交到仓库。
