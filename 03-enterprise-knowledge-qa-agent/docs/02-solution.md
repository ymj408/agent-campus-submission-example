# 02 · 方案、检索与冲突收口

## 1. 总体架构

```text
CLI (run.py / cli.py)
  │ 问题 + 可选 history
  ▼
KnowledgeQAAgent (agent.py)
  │ ReAct 循环、引用/冲突最终收口
  ├── Planner
  │    ├── MockPlanner：检索 → 读取 → 冲突/回答
  │    └── OpenAICompatiblePlanner：原生 tool calling
  └── ToolRuntime (tools.py)
       ├── KnowledgeBase：documents.json
       ├── search_documents
       ├── read_document
       └── request_clarification
```

没有向量库、数据库或 Web 服务；知识库为本地 JSON。

## 2. 工具

| 工具 | 参数 | 作用 | 是否写 |
|---|---|---|---|
| `search_documents` | `query`, `top_k?` | 只检索 active 文档；返回潜在冲突 | 否 |
| `read_document` | `doc_id`, `section_id?` | 返回精确原文、facts、当前冲突 | 否 |
| `request_clarification` | `question`, `options` | 记录冲突追问 | 内存 |

没有发布/写入知识库的工具。

## 3. 检索

### 3.1 文档过滤

1. `status=active`；D06 `superseded` 直接排除。
2. 如果 query 含范围词（总部/华东/华南/华北/全国）：
   - 保留匹配范围的文档 + 全国性文档；
   - 低分文档由阈值过滤。
3. 如果 query 没有范围词：所有 active 文档参与评分。

### 3.2 词法评分

查询扩展：

- 关键词：报销、差旅、出差、期限、多久、年假、退款、密码等。
- 同义词：出差→差旅、期限→天/截止、多久→期限、年假→休假、退款→退货。
- 中文 2–6 字 token。

评分：

```text
title 命中 +3
faq_questions 命中 +3
summary 命中 +2
section 正文/fact label 命中 +1
```

取最高分附近（`best - 1`）的候选，避免“期限/多久”把无关制度带进来。

### 3.3 相似问题

“出差回来多久内要报销”没有逐字出现“报销期限”，但通过：

- “出差” → “差旅”
- “多久” → “期限”
- “报销” ← FAQ / 正文

仍命中 D01/D02，随后触发冲突。

## 4. 原文读取

`read_document` 返回：

```json
{
  "doc_id": "D01",
  "version": "2.0",
  "scope_terms": ["总部", "华东"],
  "facts": {
    "reimbursement_deadline_days": {
      "label": "报销期限",
      "value": 30,
      "unit": "天",
      "section_id": "S2"
    }
  },
  "sections": [
    {"id": "S2", "title": "报销期限", "text": "员工应在差旅结束后 30 个自然日内..."}
  ],
  "quote": "..."
}
```

摘要不能作为引用；引用只能来自 `sections[].text`。

## 5. 冲突检测

`ToolRuntime.conflicts()`：

1. 只看已读取且 `is_active` 的文档。
2. 按 fact_key 分组。
3. 同一 fact_key 出现不同 value → 潜在冲突。
4. 如果问题有单一范围 hint（如总部），过滤掉不适用范围的文档；若过滤后仍有不同值 → 冲突。
5. 输出：

```json
{
  "fact_key": "reimbursement_deadline_days",
  "label": "报销期限",
  "values": [
    {"doc_id": "D01", "value": 30, "quote": "..."},
    {"doc_id": "D02", "value": 15, "quote": "..."}
  ],
  "reason": "多份 active 文档对同一事实给出不同值，且未提供明确优先级",
  "question": "本次问题应适用哪份制度？..."
}
```

策略读取到冲突后：

1. `request_clarification` 记录待确认问题；
2. `finish(needs_confirmation)`；
3. 不输出单一 15/30 作为答案。

## 6. 引用校验与最终收口

`agent._finalize` 不由模型 finish 直接决定：

```text
上限/异常
  > 冲突（needs_confirmation）
  > search/read 失败（tool_failure）
  > 无候选/无证据（insufficient_evidence）
  > 有证据（answered）
```

`_canonical_answer` 从 `evidence` 重建：

- 冲突：双方 quote + 值 + 追问，`answer_type=conflict`；
- 明确：fact label/value + quote，`answer_type=direct`；
- 其他：answer=null。

`citation_check`：

- 模型 citations 逐个用 `validate_citation(doc_id, quote, section_id)` 校验；
- 规范 citations 也校验；
- 错误模型引用不会进入最终 `citations`，但会记录在 `citation_check.invalid_model_citations`。

因此：

- 模型谎报 15 天 → `needs_confirmation`；
- 模型伪造 quote → 最终引用被重建；
- 模型无证据回答 → `insufficient_evidence`。

## 7. 上下文

`QATask.history` 会：

- 作为真实模型 messages 的历史；
- 给 Mock 的 `rewrite_query` 使用。

Mock 规则：

- 当前问题出现新范围词，上一轮有旧范围词：替换范围，如“华南…” + “那总部呢” → “总部 差旅…”；
- 当前问题含“那/呢/这个/上述/它”等短追问：拼接上一轮用户问题；
- 否则使用原问题。

输出 `rewritten_query` 与 `history_used`，Trace 中的 search 参数可见。

## 8. 工具失败

- `search_documents` 连续失败 → `tool_failure`，无候选。
- `read_document` 连续失败 → `tool_failure`，保留 `retrieved_doc_ids`，不凭摘要回答。
- 失败一次后重试成功：失败被清除，继续回答。
- `failed_reads` 只记录未解决失败；已读取证据保留在 `preserved_evidence`。

## 9. 技术取舍

| 选择 | 理由 | 代价 |
|---|---|---|
| 词法检索 + 同义词 | 小知识库可解释、零依赖、可测试 | 不是语义检索 |
| 结构化 facts 冲突检测 | 能稳定拦截 30/15 | 自由文本冲突需抽取 |
| 运行时重建回答/引用 | 防止模型无依据/错误引用 | 模型自由表达受限 |
| 只读知识库 | 避免问答 Agent 越权改文档 | 无文档维护闭环 |
| 本地 JSON | 评审可读、可替换内容验证 | 不适合大规模 |

## 10. 关键文件

- 检索/冲突：[`src/knowledge_qa/tools.py`](../src/knowledge_qa/tools.py)
- 最终收口：[`src/knowledge_qa/agent.py`](../src/knowledge_qa/agent.py)
- 知识库：[`data/documents.json`](../data/documents.json)
- 参考冲突：[`examples/outputs/02-document-conflict.json`](../examples/outputs/02-document-conflict.json)
- 上下文：[`examples/outputs/05-context-followup.json`](../examples/outputs/05-context-followup.json)
