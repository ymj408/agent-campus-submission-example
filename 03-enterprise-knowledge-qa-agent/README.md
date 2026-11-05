# 企业知识问答 Agent

> 校招 AI Agent 开发考核 · 项目题 3  
> 目标：帮助员工从制度/FAQ 中找到可靠答案，并给出可核验的原文引用。

## 一句话结论

本项目实现了一个带检索、原文读取、引用校验和冲突收口的问答 Agent。**没有读到原文就不回答；多个现行文档冲突时不擅自选一个值；回答引用从工具读到的原文重建，而不是采信模型自报。**

## 关键事实

| 事实 | 设计 |
|---|---|
| 检索 | 只检索 `status=active` 文档；D06 旧版被排除 |
| 范围 | 总部/华东 → D01；华南 → D02；全国性制度也可命中 |
| 冲突 | D01 说 30 天，D02 说 15 天；通用问题无法确定，必须追问 |
| 引用 | 必须是 `read_document` 返回的 `sections[].text` 精确子串 |
| 回答校验 | 模型 finish 只是 claim；运行时用已读取证据重建 answer/citations |
| 资料外 | 无匹配文档 → `insufficient_evidence`，不编造 |
| 工具失败 | search/read 失败 → `tool_failure`，保留已检索结果 |
| 上下文 | 支持 history；Mock 会把“那总部呢”改写为总部检索，真实模型也看到历史 |
| 相似问题 | 关键词/同义词扩展；“出差回来多久内要报销”仍能检索到 D01/D02 |
| 执行上限 | 步数/工具调用上限，超限明确未完成 |

## 环境与安装

- Python **3.11+**（开发验证：Python 3.13.5）
- 运行时**零第三方依赖**（仅标准库）

```bash
git clone https://github.com/ymj408/agent-campus-submission-example.git
cd agent-campus-submission-example/03-enterprise-knowledge-qa-agent
python run.py verify
```

无需 `pip install`。可选：

```bash
pip install -e .
enterprise-knowledge-qa-agent verify
```

## 30 秒评审入口

1. [docs/01-requirements.md](docs/01-requirements.md)：需求、范围、验收矩阵
2. [明确答案](examples/outputs/01-clear-answer.json)：D01 30 天 + 原文引用
3. [文档冲突](examples/outputs/02-document-conflict.json)：D01 30 天 vs D02 15 天，`needs_confirmation`
4. [资料外问题](examples/outputs/03-out-of-scope.json)：无资料，不回答
5. [上下文追问](examples/outputs/05-context-followup.json)：“那总部呢”改写为总部查询
6. [工具失败](examples/outputs/07-read-failure.json)：检索成功、读取失败，保留检索结果
7. [测试结果](examples/test-results.txt)：40 个测试通过

## 运行方式

### 1. 明确答案

```bash
python run.py run --question "总部差旅报销期限多久？"
```

预期：`answered`，引用 D01 S2，回答 30 天。

### 2. 文档冲突

```bash
python run.py run --question "报销期限多久？"
```

预期：`needs_confirmation`；同时引用 D01/D02，说明 30/15 冲突并要求确认适用制度。**不能只回答 15 天。**

### 3. 资料外问题

```bash
python run.py run --question "公司食堂今天吃什么？"
```

预期：`insufficient_evidence`，`answer=null`，资料缺口为“知识库没有匹配的 active 文档”。

### 4. 改写后的相似问题

```bash
python run.py run --question "出差回来多久内要报销？"
```

预期：同义词扩展后仍命中 D01/D02，返回冲突。

### 5. 上下文追问

```bash
python run.py run --case examples/cases/05-context-followup.json
```

预期：`history_used=true`；检索查询改写为“总部 …”，回答 D01 的 30 天。

### 6. 工具失败

```bash
# 检索连续失败
python run.py run --question "报销期限多久？" --fail-tool search_documents=99

# 检索成功但读取失败
python run.py run --question "总部差旅报销期限多久？" --fail-tool read_document=99
```

预期：`tool_failure`，`answer=null`；读取失败时保留 `retrieved_doc_ids`。

### 7. 全部运行记录与测试

```bash
python examples/run_examples.py
python scripts/run_tests.py
```

当前实际结果：**8 个场景 / 8 次运行 / 3 次 answered / 2 次冲突待确认 / 1 次资料外 / 2 次工具失败 / 40 个测试通过**。

## 真实模型配置

```bash
cp .env.example .env
# 填写 AGENT_MODEL_BASE_URL / AGENT_MODEL_NAME / AGENT_MODEL_API_KEY
python run.py run \
  --case examples/cases/02-document-conflict.json \
  --provider openai
```

- 使用原生 `tools` / `tool_calls`；纯 JSON 动作作为兼容回退。
- 只从环境变量读取 Key；不打印、不写入 Trace。
- 没有 Key 时明确报错，不会静默回退 Mock。
- 模型输出仍会被运行时按原文校验；Mock 与实际模型效果需分别记录。

## 结果状态

| status | 含义 | 有直接回答 |
|---|---|---|
| `answered` | 有 active 文档事实 + 精确引用 | 是 |
| `needs_confirmation` | 冲突，不能确定唯一值，已追问 | 否（只有冲突说明） |
| `insufficient_evidence` | 无匹配文档/无可引用事实 | 否 |
| `tool_failure` | 检索或读取失败，无法核验 | 否 |
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
│   ├── documents.json
│   └── README.md
├── src/knowledge_qa/
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
├── tests/                         # 40 个 unittest
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

- **检索先于回答，原文先于摘要**：摘要不用于引用；必须 `read_document`。
- **冲突检测看结构化事实**：同一 `fact_key` 的不同值 → 冲突；范围可通过问题显式缩小。
- **运行时收口冲突**：即使模型擅自答“15 天”，最终也是 `needs_confirmation`。
- **引用可验证**：模型 citation 与已读 sections 比对；错误引用会被重建/标记。
- **上下文改写**：离线策略处理范围切换；真实模型可从 messages 中看到历史。
- **不引入向量库**：合成知识库小，关键词+同义词+范围过滤足够；真实规模应换检索层。

## 已知限制

1. 只覆盖合成制度/FAQ；没有接入真实 Confluence/飞书/SharePoint。
2. 检索是词法匹配 + 手工同义词，不是 embedding/向量检索；同义词覆盖有限。
3. 冲突检测依赖结构化 `facts`；纯自由文本语义冲突需额外抽取。
4. 没有用户权限控制；假设所有员工可读全部合成文档。
5. 真实模型未用线上 Key 验证；本地假网关只验证协议。
6. 未实现文档更新/增量索引/版本迁移流程。
7. `--fail-tool` 是验收钩子，不是生产容错。
8. 单轮 CLI；多轮历史由输入提供，不维护会话数据库。

## 提交信息

- **仓库**：<https://github.com/ymj408/agent-campus-submission-example>
- **项目目录**：`03-enterprise-knowledge-qa-agent/`
- **题号**：项目题 3 · 企业知识问答 Agent
- **最终 commit SHA**：见远程 `main` 最新提交或提交回复
- **AI 使用记录**：[docs/04-ai-usage.md](docs/04-ai-usage.md)
