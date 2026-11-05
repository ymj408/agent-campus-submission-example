# 短视频脚本 Agent

> 校招 AI Agent 开发考核 · 项目题 4  
> 目标：生成有事实依据、满足目标时长、可编辑的短视频脚本。

## 一句话结论

本项目实现了一个带工具反馈循环的脚本 Agent：检索素材 → 用 **estimate_duration** 估算时长 → 超时压缩后重新估算 → 用 **check_rules** 检查 claims、禁用词、素材授权和语速。参考实例中，首稿 **38 秒**，压缩后 **28 秒**；P01 只支持“支持字幕”，因此不会出现“效率提升 80%”等无依据结论。

## 关键事实

| 事实 | 设计 |
|---|---|
| 素材 | P01 仅包含“支持字幕”；P02 有三种配色；P03 无事实；P04 授权过期，检索排除 |
| 首稿 | 4 分镜，合计 38 秒 |
| 修正稿 | 压缩到 28 秒，满足 30 秒目标 |
| 最短稿 | 14 秒；目标 10 秒时仍超时，明确 `duration_unmet` |
| 事实追溯 | `claims[].text` 必须能在同一 `material_id` 的 facts 中找到 |
| 无效果数据 | “效率提升 80%”命中禁用词且无事实支持，必须删除后重检 |
| 工具 | 素材检索、时长估算、规则检查；另有 brief 读取 |
| 最终时长 | 由工具从分镜 seconds 汇总，模型自报不进入最终结果 |
| 工具失败 | 连续失败 → `tool_failure`，不输出 ready 脚本 |
| 执行上限 | 步数/工具调用上限，超限明确未完成 |

## 环境与安装

- Python **3.11+**（开发验证：Python 3.13.5）
- 运行时**零第三方依赖**（仅标准库）

```bash
git clone https://github.com/ymj408/agent-campus-submission-example.git
cd agent-campus-submission-example/04-short-video-script-agent
python run.py verify
```

无需 `pip install`。可选：

```bash
pip install -e .
short-video-script-agent verify
```

## 30 秒评审入口

1. [docs/01-requirements.md](docs/01-requirements.md)：需求、验收矩阵、38→28
2. [正常脚本](examples/outputs/01-normal-30s.json)：38 → 28 秒，分镜/旁白/字幕/P01
3. [超时长失败](examples/outputs/04-duration-unmet.json)：14 秒仍超 10 秒，明确失败
4. [规则风险修正](examples/outputs/07-rule-risk-clean.json)：删除“效率提升 80%”后重检
5. [工具失败](examples/outputs/06-check-failure.json)：不把脚本标为 ready
6. [测试结果](examples/test-results.txt)：39 个测试通过

## 运行方式

### 1. 正常 30 秒脚本

```bash
python run.py run \
  --theme "通勤降噪耳机" \
  --audience "通勤上班族" \
  --platform douyin \
  --target-duration 30 \
  --style "简洁可信"
```

预期：

```text
首稿 38 秒 → 时长超 8 秒
压缩稿 28 秒 → 通过
规则检查通过
输出 title / 4 个分镜 / 旁白 / 字幕 / P01 / 28 秒
```

### 2. 缺关键信息

```bash
python run.py run \
  --theme "通勤降噪耳机" \
  --platform douyin \
  --target-duration 30
```

缺 `audience` 时返回 `blocked_missing_fields`，不生成脚本。

### 3. 无素材

```bash
python run.py run \
  --theme "宠物猫粮" \
  --audience "养猫人群" \
  --platform douyin \
  --target-duration 30
```

预期：`insufficient_material`，不编造产品脚本。

### 4. 超时长无法修正

```bash
python run.py run \
  --theme "通勤降噪耳机" \
  --audience "通勤上班族" \
  --platform douyin \
  --target-duration 10
```

预期：38 → 28 → 14 秒，仍超 10 秒 4 秒；`duration_unmet`，脚本 `ready=false`。

### 5. 规则风险修正

```bash
python run.py run \
  --theme "通勤降噪耳机" \
  --audience "通勤上班族" \
  --platform douyin \
  --target-duration 30 \
  --style "夸张营销"
```

预期：首稿/压缩稿加入无依据“效率提升 80%” → 规则检查阻断 → 删除后重新估算和检查 → `script_ready`，最终脚本不含该结论。

### 6. 工具失败

```bash
python run.py run --case examples/cases/05-search-failure.json
python run.py run --case examples/cases/06-check-failure.json
```

预期：`tool_failure`，不输出 ready 脚本。

### 7. 全部运行记录与测试

```bash
python examples/run_examples.py
python scripts/run_tests.py
```

当前实际结果：**8 个场景 / 8 次运行 / 3 次 script_ready / 1 次缺信息 / 1 次无素材 / 1 次超时失败 / 2 次工具失败 / 39 个测试通过**。

## 真实模型配置

```bash
cp .env.example .env
# 填写 AGENT_MODEL_BASE_URL / AGENT_MODEL_NAME / AGENT_MODEL_API_KEY
python run.py run \
  --case examples/cases/01-normal-30s.json \
  --provider openai
```

- 原生 `tools` / `tool_calls`；纯 JSON 动作作为兼容回退。
- 只从环境变量读取 Key；不打印、不写入 Trace。
- 没有 Key 时明确报错。
- 真实模型输出仍会被 `estimate_duration` / `check_rules` 校验；Mock 记录不代表模型创作效果。

## 结果状态

| status | 含义 | ready 脚本 |
|---|---|---|
| `script_ready` | 时长满足、规则通过、事实可追溯 | 是 |
| `blocked_missing_fields` | 缺受众/平台/目标时长 | 否 |
| `insufficient_material` | 无可用素材 | 否 |
| `duration_unmet` | 多次压缩后仍超时 | 否 |
| `rule_blocked` | 规则阻断且无法修正 | 否 |
| `tool_failure` | 检索/估算/检查失败 | 否 |
| `step_limit_reached` / `tool_limit_reached` | 达到上限，未完成 | 否 |

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
│   ├── brand_rules.json
│   └── README.md
├── src/script_agent/
│   ├── cli.py
│   ├── agent.py
│   ├── planner.py
│   ├── llm.py
│   ├── tools.py
│   ├── knowledge.py
│   ├── models.py
│   ├── timeutil.py
│   ├── trace.py
│   └── prompts.py
├── tests/                         # 39 个 unittest
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

## 设计取舍

- **时长由工具汇总**：分镜 seconds 是输入，总时长由 `estimate_duration` 计算；模型自报 28 秒无效。
- **事实由 claims 追溯**：每个 claim 必须能在同素材 facts 中找到；P01 只能支持“支持字幕”。
- **压缩是重复决策**：38 秒超时 → 28 秒；28 秒仍超时 → 14 秒；仍超时则明确失败。
- **规则失败可修正**：无依据效果 claim 删除后重新估算/检查；不能把 blocked 当 ready。
- **最终收口优先工具事实**：即使模型 finish 说 `script_ready`，规则/时长失败仍覆盖。

## 已知限制

1. 素材与品牌规则为合成数据；未接真实产品库/法务规则。
2. 时长以分镜秒数汇总 + 语速阈值校验，不是真实 TTS/剪辑耗时。
3. 检索是关键词匹配，不做语义 embedding。
4. 分镜模板固定；不是通用视频创作模型。
5. 真实模型未用线上 Key 验证。
6. `--fail-tool` 是验收钩子，不是生产容错。
7. 没有视频渲染、字幕文件导出、素材下载。
8. 没有多人协作/版本管理。

## 提交信息

- **仓库**：<https://github.com/ymj408/agent-campus-submission-example>
- **项目目录**：`04-short-video-script-agent/`
- **题号**：项目题 4 · 短视频脚本 Agent
- **最终 commit SHA**：见远程 `main` 最新提交或提交回复
- **AI 使用记录**：[docs/04-ai-usage.md](docs/04-ai-usage.md)
