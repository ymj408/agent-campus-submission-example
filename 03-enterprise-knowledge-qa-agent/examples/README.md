# examples/ 实际运行记录

由 `python examples/run_examples.py` 生成，不是手写结果。

- 输入：`examples/cases/*.json`
- 实际输出：`examples/outputs/*.json`
- Trace：`examples/traces/*.jsonl`
- 总览：`examples/outputs/index.json`
- 测试：`examples/test-results.txt`、`examples/test-summary.json`

## 总览

| # | 场景 | 验收点 | status | 引用 |
|---:|---|---|---|---:|
| 01 | [clear-answer](cases/01-clear-answer.json) | 明确答案、原文引用 | `answered` | 1 |
| 02 | [document-conflict](cases/02-document-conflict.json) | 冲突、追问、不选一个值 | `needs_confirmation` | 2 |
| 03 | [out-of-scope](cases/03-out-of-scope.json) | 资料外、不编造 | `insufficient_evidence` | 0 |
| 04 | [paraphrase-conflict](cases/04-paraphrase-conflict.json) | 相似问题、检索改进、冲突 | `needs_confirmation` | 2 |
| 05 | [context-followup](cases/05-context-followup.json) | 上下文追问、查询改写 | `answered` | 1 |
| 06 | [search-failure](cases/06-search-failure.json) | 工具失败 | `tool_failure` | 0 |
| 07 | [read-failure](cases/07-read-failure.json) | 读取失败、保留检索 | `tool_failure` | 0 |
| 08 | [clear-product-faq](cases/08-clear-product-faq.json) | 产品 FAQ、原文引用 | `answered` | 1 |

合计：**8 个场景 / 8 次运行 / 3 answered / 2 needs_confirmation / 1 insufficient / 2 tool_failure**。

## 参考实例怎么读

打开 [02 输出](outputs/02-document-conflict.json)：

1. `read_doc_ids` 为 D01、D02；
2. `conflict.values` 为 D01 30 天、D02 15 天；
3. `citations` 同时包含 D01 S2、D02 S2；
4. `status=needs_confirmation`，`answer_type=conflict`；
5. `follow_up_question` 请求确认适用制度；
6. 最终没有把 15 天作为唯一答案。

打开 [01 输出](outputs/01-clear-answer.json)：

- 查询带“总部”范围，检索仅 D01；
- `citations[0].quote` 是 D01 S2 原文；
- `citation_check.canonical_citations_valid=true`。

打开 [05 输出](outputs/05-context-followup.json)：

- `history_used=true`；
- `rewritten_query` 含“总部”；
- 回答 D01 30 天。

打开 [07 输出](outputs/07-read-failure.json)：

- `status=tool_failure`，`answer=null`；
- `retrieved_doc_ids=["D01"]`，`read_doc_ids=[]`；
- 不凭检索摘要回答。

## Trace 结构

```json
{
  "event": "step",
  "step": 4,
  "action": "tool",
  "tool": "read_document",
  "arguments": {"doc_id": "D02"},
  "decision_basis": "读取 D02 原文，检查事实和引用。",
  "observation": {
    "ok": true,
    "summary": "读取 D02 v3.0 章节「报销期限」...",
    "data": {"quote": "...", "conflicts": [{"fact_key": "reimbursement_deadline_days"}]}
  }
}
```

最后一行 `run_end` 包含 status、completed、answer_type、citation_count。

## 重新生成

```bash
python examples/run_examples.py
python scripts/run_tests.py
```

## Mock 边界

Mock 证明检索、范围过滤、冲突、引用校验、上下文改写、工具失败逻辑可运行；不证明真实模型回答质量。真实模型用 `--provider openai`，需自备 Key。
