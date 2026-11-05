# 01 · 需求、范围与成功标准

## 1. 用户与目标

**主要用户**

- 员工：询问制度、报销、年假、产品退款等问题。
- HR/财务/产品支持：希望回答有文档 ID、版本和原文引用，能追溯到来源。

**核心目标**

1. 根据问题检索 active 文档。
2. 读取原文，而不是只依赖标题/摘要。
3. 用精确原文引用支持回答。
4. 资料冲突时说明冲突并追问，不擅自选一个值。
5. 资料外问题明确说“不知道”，不编造。
6. 工具失败时保留已检索结果，明确未完成。
7. 支持带对话历史的追问。

## 2. 输入

```json
{
  "request_id": "REQ-KQ-05",
  "question": "那总部呢？",
  "history": [
    {"role": "user", "content": "华南区差旅报销期限多久？"},
    {"role": "assistant", "content": "根据 D02，报销期限为 15 天。"}
  ]
}
```

| 字段 | 说明 |
|---|---|
| `request_id` | 任务标识 |
| `question` | 当前问题，必填 |
| `history` | 可选，`user/assistant` 交替的对话历史 |

知识库：

- `data/documents.json`：6 个合成文档，含 doc_id、version、effective_date、status、scope_terms、sections、facts。
- D06 为 superseded，不参与检索与冲突。

## 3. 输出

| 输出 | 说明 |
|---|---|
| `answer` | 回答；资料外/工具失败时为 null；冲突时为冲突说明 |
| `citations` | doc_id、version、section_id、精确 quote |
| `conflict` | 冲突事实、双方值、原文、追问 |
| `missing_information` | 资料缺口/待确认项 |
| `status` | answered / needs_confirmation / insufficient_evidence / tool_failure / 上限 |
| `rewritten_query` | 实际检索查询 |
| `history_used` | 是否用历史改写查询 |
| `citation_check` | 模型引用与规范引用的校验结果 |
| `retrieved_doc_ids` / `read_doc_ids` | 检索与读取轨迹 |

## 4. 关键语义

### 4.1 冲突

D01/D02 都是 active：

| 文档 | 版本 | 适用 | 报销期限 |
|---|---|---|---|
| D01 | 2.0 | 总部/华东 | 30 天 |
| D02 | 3.0 | 华南 | 15 天 |

- 问题“报销期限多久”没有指定范围 → 冲突，`needs_confirmation`。
- 问题“总部差旅报销期限” → 只命中 D01 → `answered` 30 天。
- 问题“华南区差旅报销期限” → 只命中 D02 → `answered` 15 天。
- 问题“总部和华南的报销期限分别多久” → 可命中双方，仍冲突，需用户确认/分别回答。

**不允许**：在没有范围/优先级时直接回答 15 天或 30 天。

### 4.2 引用

- 引用必须来自 `read_document` 的 `sections[].text`。
- `citations[].quote` 必须能在原文中精确找到。
- 检索摘要不能作为引用。
- 运行时用证据重建 answer/citations；模型错误引用会记录在 `citation_check`，不会进入最终 citations。

### 4.3 版本

- 只检索 `status=active`。
- D06 旧版 45 天被排除，避免旧值参与冲突。
- 引用中包含 version 与 effective_date。

### 4.4 资料外

没有 active 文档命中，或命中了但没有可引用事实：

```text
status = insufficient_evidence
answer = null
missing_information = [...]
```

不能凭常识回答。

### 4.5 工具失败

- 搜索失败：重试一次；仍失败 → `tool_failure`。
- 读取失败：重试一次；仍失败 → `tool_failure`，保留检索结果，不能凭摘要回答。
- 已读取的其他证据保留在 `preserved_evidence`。

### 4.6 上下文改写

- 当前问题依赖上一轮时，模型/Mock 可以改写查询。
- Mock 处理范围切换：“华南区…” + “那总部呢？” → “总部 …”。
- 结果输出 `rewritten_query` 与 `history_used`。

## 5. 范围

### In scope

- CLI：`run / list-docs / show-doc / verify`
- 3 个工具：文档检索、原文读取、追问
- 冲突检测、引用校验、工具失败、资料外、上下文改写
- 8 组输入、实际输出、Trace、40 个测试

### Out of scope

- 真实知识库/权限系统/文档同步。
- 向量数据库/embedding 检索。
- 多轮会话持久化。
- 文档自动摘要/修订。
- 真实发布/工单系统。

## 6. 验收矩阵

| 必须验收点 | 输入 | 实际输出 | 证据 |
|---|---|---|---|
| 明确答案 | 01 总部报销期限 | `answered` 30 天，引用 D01 S2 | [01 output](../examples/outputs/01-clear-answer.json) |
| 文档冲突 | 02 通用报销期限 | `needs_confirmation`，引用 D01/D02 | [02 output](../examples/outputs/02-document-conflict.json) |
| 资料外问题 | 03 食堂 | `insufficient_evidence`，answer null | [03 output](../examples/outputs/03-out-of-scope.json) |
| 改写相似问题 | 04 出差回来多久内报销 | 同义词检索命中 D01/D02，冲突 | [04 output](../examples/outputs/04-paraphrase-conflict.json) |
| 上下文追问 | 05 华南→总部 | `history_used`，改写查询，回答 30 天 | [05 output](../examples/outputs/05-context-followup.json) |
| 工具失败 | 06 搜索失败 / 07 读取失败 | `tool_failure`，无答案，保留检索 | 06/07 output |
| 引用支持回答 | 01/08 | 引用 quote 与原文一致 | citations + 测试 |
| 替换文档内容 | 单测改 D01 为 45 天 | 回答变 45 天 | `test_replacing_document_changes_answer` |
| 错误引用 | 单测伪造 quote | 最终引用被重建，模型引用标记无效 | `test_runtime_rebuilds_invalid_citation` |
| 不虚报 | 冲突谎报/无证据谎报 | 运行时覆盖为 needs_confirmation/insufficient | 两个 agent 单测 |

**实际总览：8 个场景 / 8 次运行 / 3 answered / 2 needs_confirmation / 1 insufficient / 2 tool_failure / 40 tests。**

## 7. 成功标准

1. 明确答案必须带精确原文引用。
2. 冲突必须同时引用双方、说明缺范围/优先级、请求确认。
3. 资料外必须无 answer、有 gap。
4. 工具失败必须无 answer，不能凭摘要。
5. 上下文追问必须体现查询改写。
6. 替换文档事实后，答案相应改变。
7. 模型错误引用/冲突谎报不能污染最终回答。
