# 04 · AI 辅助范围、个人贡献与验证

> 本文件如实记录题 3 的 AI 使用范围。**不把 AI 生成当成候选人个人贡献，也不把 Mock 结果当成真实模型效果。**

## 1. AI 工具

| 项目 | 记录 |
|---|---|
| 工具 | DeepSeek Harness 编码 Agent（Web GUI 会话） |
| 模型标识 | `deepseek-flash` |
| 日期 | 2026-11-05（Asia/Shanghai） |
| 使用方式 | 阅读题目、生成代码/测试/数据/文档、执行命令验证 |
| 其他代码生成工具 | 本次会话未使用 |

## 2. 候选人提供的内容

- 题目：项目题 3「企业知识问答 Agent」；
- 验收点：明确答案、文档冲突、资料外问题、改写后的相似问题、上下文追问、工具失败；
- 关键要求：引用原文、冲突时追问而不是选一个值、替换文档内容后答案要变；
- 仓库：`https://github.com/ymj408/agent-campus-submission-example`，题 3 放在 `03-enterprise-knowledge-qa-agent/`。

候选人**尚未在本次会话中进行人工逐行改写**；提交前应复核代码、补充个人贡献说明。

## 3. AI 生成/修改范围

| 范围 | 内容 | 人工改写 |
|---|---|---|
| 设计 | 检索、范围过滤、冲突、引用校验、上下文改写 | 否 |
| 代码 | `src/knowledge_qa/` 全部模块 | 否 |
| 数据 | 6 个合成文档、D01/D02 冲突、D06 旧版 | 否 |
| 测试 | 40 个测试 | 否 |
| 运行记录 | 8 组输入、8 次运行、Trace | 脚本生成 |
| 文档 | README、docs/01–05、examples/README | 否 |

## 4. 实际发生的修正

1. **范围过滤误带无关全国制度**：早期“总部报销”把退款/密码制度也当候选，读取了 4 份文档。改为“范围文档 + 全国制度”，再用 `best_score - 1` 过滤低分项；最终只读 D01。
2. **冲突发现太晚**：最初读完全部候选才检查冲突，导致 02 场景多读了 D03–D05。改为每读一份文档就检查 `conflicts`，D01/D02 之后立即追问。
3. **冲突说明重复/缺少 gap**：统一把 conflict reason、追问、待办合并进 `missing_information`，避免 CLI 只显示空资料缺口。
4. **引用被模型伪造的风险**：最终 citations 不直接采用模型 payload，而是从 `read_document` 证据重建；模型引用写入 `citation_check` 审计。
5. **上下文 CLI 转义问题**：PowerShell 直接传 JSON 容易丢引号，增加 `--history-file`，并保留 `--history`。
6. **删除旧版干扰**：D06 superseded 在检索时排除，避免 45 天旧值参与冲突。

## 5. 验证

```bash
python -m compileall -q src run.py
python run.py verify
python examples/run_examples.py
python scripts/run_tests.py
```

| 验证 | 结果 |
|---|---|
| 知识库 | 6 文档：5 active + 1 superseded |
| 场景 | 8 / 8 检查通过：3 answered、2 conflict、1 insufficient、2 tool failure |
| 测试 | 40 tests, 0 failures, 0 errors |
| 明确答案 | D01 30 天，引用 S2 |
| 冲突 | D01/D02 双方引用、追问、无单一值 |
| 资料外 | answer=null，gap |
| 相似问题 | 检索命中 D01/D02 |
| 上下文 | 华南→总部，查询改写，30 天 |
| 读取失败 | 无答案，保留 D01 检索 |
| 内容替换 | D01 30→45，回答变 45 |
| 错误引用 | 最终引用重建，模型引用标 invalid |

产物：

- [examples/test-results.txt](../examples/test-results.txt)
- [examples/test-summary.json](../examples/test-summary.json)
- [examples/outputs/index.json](../examples/outputs/index.json)
- [examples/traces/](../examples/traces/)

## 6. 未完成/未验证

1. **真实模型**：没有 API Key，未对线上模型发起请求；仅测本地假网关协议。
2. **人工复核**：候选人尚未逐文件复核；自动测试不等于人工验收。
3. **真实知识库**：没有接 Confluence/飞书/SharePoint，也没有权限控制。
4. **语义检索**：没有 embedding/向量库；同义词为手工配置。
5. **自由文本冲突**：只对结构化 facts 检测。

## 7. 提交前候选人应补充

- 实际修改/重写了哪些文件，为什么；
- 如何验证“冲突不选 15 天”“错误引用不能进入最终回答”“替换文档后答案改变”；
- 是否用真实模型跑过 01–08，差异是什么；
- 实际投入时间与未完成项（同步 `docs/05-retrospective.md`）。

## 8. 隐私与密钥

- 所有文档、员工、产品、制度均为合成数据；
- `.env` 被 `.gitignore` 忽略；
- Trace/结果不含 API Key；
- 未提交真实企业文档或用户隐私。
