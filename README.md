# 校招 AI Agent 项目提交

本仓库按**题号目录**组织，每个题目目录保持自包含（README / src / tests / examples / data / docs）。

> 当前只提交了**项目题 1**。其余三题尚未完成，因此不占用空目录；后续完成时按同样结构新增 `02-*`、`03-*`、`04-*` 即可。

## 题目索引

| 题号 | 题目 | 状态 | 入口 |
|---:|---|---|---|
| 1 | 素材审核与发布排期 Agent | ✅ 已完成 | [01-media-review-scheduler/](01-media-review-scheduler/) |
| 2 | 待完成 | — | — |
| 3 | 待完成 | — | — |
| 4 | 待完成 | — | — |

考核要求是“四选一”，因此当前提交以题 1 作为评审项目；其他题号留空不影响题 1 的评审。若后续继续完成，建议每个题一个顶层目录，不要把代码散落在仓库根目录。

## 题 1 快速入口

```bash
cd 01-media-review-scheduler
python run.py verify
python run.py run --case examples/cases/02-conflict-alternative.json --store .runtime/demo --reset
python scripts/run_tests.py
```

- 题 1 说明：[01-media-review-scheduler/README.md](01-media-review-scheduler/README.md)
- 验收矩阵：[docs/01-requirements.md](01-media-review-scheduler/docs/01-requirements.md)
- 实际运行总览：[examples/outputs/index.json](01-media-review-scheduler/examples/outputs/index.json)
- 参考实例（18:00 冲突 → 19:00 pending → 重复提交一份草稿）：
  [examples/outputs/02-conflict-alternative.json](01-media-review-scheduler/examples/outputs/02-conflict-alternative.json)
- 测试结果：[examples/test-results.txt](01-media-review-scheduler/examples/test-results.txt)

## 提交信息

- **仓库**：<https://github.com/ymj408/agent-campus-submission-example>
- **题号**：项目题 1 · 素材审核与发布排期 Agent
- **项目目录**：`01-media-review-scheduler/`
- **最终 commit SHA**：见远程 `main` 分支最新提交，或本仓库提交回复
- **AI 使用记录**：[docs/04-ai-usage.md](01-media-review-scheduler/docs/04-ai-usage.md)

## 目录约定

```text
.
├── README.md                       # 四题索引（本文件）
├── .gitignore
├── .gitattributes
└── 01-media-review-scheduler/      # 题 1，自包含
    ├── README.md
    ├── run.py
    ├── src/
    ├── tests/
    ├── examples/
    ├── data/
    ├── docs/
    ├── requirements.txt
    ├── pyproject.toml
    └── .env.example
```

如果评审只需要看一道题，直接进入 `01-media-review-scheduler/`；根目录 README 只负责导航，不重复项目实现。
