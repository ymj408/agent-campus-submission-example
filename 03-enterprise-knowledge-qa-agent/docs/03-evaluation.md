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
tests_run: 40
failures: 0
errors: 0
skipped: 0
success: True
```

| 测试文件 | 数量 | 覆盖 |
|---|---:|---|
| `tests/test_agent.py` | 13 | 明确答案、冲突、资料外、相似问题、上下文、搜索/读取失败、冲突谎报、错误引用、无证据、上限、Trace、内容替换 |
| `tests/test_tools.py` | 12 | 检索冲突、范围过滤、旧版排除、相似问题、资料外、原文/facts、冲突检测、引用校验、读取失败、追问去重、历史改写 |
| `tests/test_cli.py` | 4 | verify、case 输出、冲突 human 输出、无 Key |
| `tests/test_llm_adapter.py` | 4 | 原生 tool calling、JSON 回退、无 Key、HTTP 错误 |
| `tests/test_examples_artifacts.py` | 7 | 产物总览、明确答案、冲突双方、上下文、工具失败、Trace |

关键断言：

- `test_conflict_never_answers_single_value`：冲突状态、双方引用、30/15 值。
- `test_runtime_overrides_conflict_lie`：模型谎报 15 天，运行时仍 `needs_confirmation`。
- `test_runtime_rebuilds_invalid_citation`：模型伪造 quote，最终引用被重建。
- `test_no_evidence_lie_is_rejected`：无证据回答 → `insufficient_evidence`。
- `test_replacing_document_changes_answer`：把 D01 的 30 改为 45，回答相应变为 45。
- `test_context_followup_rewrites_query`：华南→总部，查询改写、回答 30。
- `test_read_failure_preserves_retrieval_only`：读取失败无答案，保留 D01 检索结果。

## 3. 实际运行结果

`examples/outputs/index.json`：

```json
{
  "scenarios": 8,
  "runs": 8,
  "answered": 3,
  "needs_confirmation": 2,
  "insufficient_evidence": 1,
  "tool_failure": 2,
  "all_checks_passed": true
}
```

| 场景 | 实际 status | 引用 | 关键结果 |
|---|---|---|---|
| 01-clear-answer | `answered` | D01 | 30 天 + 原文 |
| 02-document-conflict | `needs_confirmation` | D01/D02 | 30 vs 15，追问适用制度 |
| 03-out-of-scope | `insufficient_evidence` | 0 | answer=null，资料缺口 |
| 04-paraphrase-conflict | `needs_confirmation` | D01/D02 | 相似问题仍命中冲突 |
| 05-context-followup | `answered` | D01 | history_used，改写“总部” |
| 06-search-failure | `tool_failure` | 0 | 无答案 |
| 07-read-failure | `tool_failure` | 0 | 保留 D01 检索结果 |
| 08-clear-product-faq | `answered` | D04 | 产品退款 7 天 |

### 3.1 参考实例（02）

[`examples/outputs/02-document-conflict.json`](../examples/outputs/02-document-conflict.json)

```text
search_documents → D01, D02
read_document D01 → 30 天
read_document D02 → 15 天，conflicts=1
request_clarification → “适用 D01 还是 D02？”
finish needs_confirmation
```

最终：

- `answer_type = conflict`
- `citations = [D01 S2, D02 S2]`
- `conflict.values = [{D01:30}, {D02:15}]`
- `follow_up_question` 存在
- 未把 15 天作为唯一答案

### 3.2 明确答案（01）

[`examples/outputs/01-clear-answer.json`](../examples/outputs/01-clear-answer.json)

- 查询“总部差旅报销期限多久” → scope_hints=总部 → D01 only
- 引用 D01 S2：“员工应在差旅结束后 30 个自然日内提交报销申请，逾期需说明原因。”
- `citation_check.canonical_citations_valid=true`

### 3.3 资料外（03）

- 无候选 active 文档。
- `answer=null`，`missing_information` 明确“知识库没有匹配的 active 文档”。
- 不生成“食堂通常…”之类的常识答案。

### 3.4 相似问题 + 冲突（04）

- “出差回来多久内要报销”没有逐字命中，但通过“出差→差旅”“多久→期限”等扩展命中 D01/D02。
- 仍返回 `needs_confirmation`，证明检索改进没有绕过冲突收口。

### 3.5 上下文（05）

- history: 华南区问题 + 15 天回答。
- 当前“那总部呢？” → 查询改写为“总部 差旅报销期限多久？”。
- 只读 D01，回答 30 天；`history_used=true`。

### 3.6 工具失败（06/07）

- 06：search 连续失败 → `tool_failure`，无候选。
- 07：search 成功（D01），read 连续失败 → `tool_failure`；`retrieved_doc_ids=["D01"]`，`read_doc_ids=[]`，不凭摘要回答。

### 3.7 内容替换

单测 `test_replacing_document_changes_answer`：

- 把 D01 的 fact 30→45，S2 文本 30→45；
- 同样问题“总部差旅报销期限多久”；
- 回答变 45 天，引用变 45 个自然日。

证明答案由文档内容驱动，不是硬编码。

## 4. 失败与边界

### 4.1 已处理

| 情况 | 行为 |
|---|---|
| 文档冲突 | 列双方原文 + 追问，不给单一值 |
| 资料外 | `insufficient_evidence`，answer=null |
| 相似问题 | 同义词扩展检索 |
| 上下文追问 | 改写查询，输出 `rewritten_query` |
| 旧版本 | 检索排除 superseded |
| 搜索失败 | 重试一次后 `tool_failure` |
| 读取失败 | 重试一次后 `tool_failure`，保留检索 |
| 模型错误引用 | 运行时重建 citations，记录 invalid |
| 模型冲突谎报 | 运行时覆盖为 `needs_confirmation` |
| 模型无证据回答 | `insufficient_evidence` |
| 执行上限 | 明确未完成，无答案 |

### 4.2 Mock 边界

Mock 证明：

- 检索、范围过滤、原文读取；
- 冲突检测、追问、引用校验；
- 上下文改写、工具失败、内容替换。

Mock **不证明**：

- 真实模型是否稳定遵守引用/冲突规则；
- 真实大知识库的检索召回；
- 复杂自由文本冲突的语义判断。

真实模型适配器用本地假 OpenAI-compatible 网关测试了 3 次 tool call 全链路，但没有真实 API Key。

### 4.3 已知限制

1. 词法检索 + 手工同义词，不是 embedding 检索。
2. 冲突依赖结构化 `facts`；自由文本中的冲突不会自动检测。
3. 没有权限控制；假设所有员工可读全部合成文档。
4. 没有文档更新/索引同步。
5. 没有多轮会话持久化；history 由输入提供。
6. 真实模型未线上验证。
7. `--fail-tool` 是验收钩子。
8. 单文档事实只覆盖少量主题；answer 以单一 fact 为主。

## 5. 评审建议路径

1. 看 `examples/outputs/index.json`（8/3/2/1/2）。
2. 看 02 输出，核对 30/15、双方引用、追问。
3. 看 01 输出，核对 30 天与 D01 S2 原文。
4. 看 05 输出，核对 `rewritten_query` 与 `history_used`。
5. 看 06/07，核对无答案与保留检索。
6. 看 `examples/test-results.txt`（40 tests）。
