# 校招 AI Agent 项目提交

本仓库按**题号目录**组织，每个题目保持自包含（README / src / tests / examples / data / docs）。

> 当前已完成并提交**项目题 1、项目题 2**。剩余两题尚未完成，因此不占空目录；后续按同样结构新增 `03-*`、`04-*`。

## 题目索引

| 题号 | 题目 | 状态 | 入口 | 验证 |
|---:|---|---|---|---|
| 1 | 素材审核与发布排期 Agent | ✅ 已完成 | [01-media-review-scheduler/](01-media-review-scheduler/) | 47 tests |
| 2 | 内容任务规划 Agent | ✅ 已完成 | [02-content-planning-agent/](02-content-planning-agent/) | 40 tests |
| 3 | 待完成 | — | — | — |
| 4 | 待完成 | — | — | — |

考核要求是“四选一”。当前提交包含两个完整项目，可任选其一评审；题 2 是后加的独立子项目，不影响题 1。

## 题 1 快速入口

```bash
cd 01-media-review-scheduler
python run.py verify
python run.py run --case examples/cases/02-conflict-alternative.json --store .runtime/demo --reset
python scripts/run_tests.py
```

- 题 1 说明：[01-media-review-scheduler/README.md](01-media-review-scheduler/README.md)
- 参考实例：[18:00 冲突 → 19:00 pending](01-media-review-scheduler/examples/outputs/02-conflict-alternative.json)
- 运行总览：[examples/outputs/index.json](01-media-review-scheduler/examples/outputs/index.json)

## 题 2 快速入口

```bash
cd 02-content-planning-agent
python run.py verify
python run.py run --case examples/cases/01-normal-budget-replan.json --store .runtime/demo --reset
python scripts/run_tests.py
```

- 题 2 说明：[02-content-planning-agent/README.md](02-content-planning-agent/README.md)
- 参考实例：[12 超预算 → 8 方案保存](02-content-planning-agent/examples/outputs/01-normal-budget-replan.json)
- 工具失败重规划：[海报失败 → 无海报方案](02-content-planning-agent/examples/outputs/05-poster-failure-replan.json)
- 失败续跑：[审核失败 → resume 保存](02-content-planning-agent/examples/outputs/07-resume-failed-step.json)
- 运行总览：[examples/outputs/index.json](02-content-planning-agent/examples/outputs/index.json)

## 提交信息

- **仓库**：<https://github.com/ymj408/agent-campus-submission-example>
- **项目目录**：`01-media-review-scheduler/`、`02-content-planning-agent/`
- **最终 commit SHA**：见远程 `main` 分支最新提交，或本仓库提交回复
- **AI 使用记录**：题 1 [docs/04-ai-usage.md](01-media-review-scheduler/docs/04-ai-usage.md)；题 2 [docs/04-ai-usage.md](02-content-planning-agent/docs/04-ai-usage.md)

## 目录约定

```text
.
├── README.md                       # 四题索引（本文件）
├── .gitignore
├── .gitattributes
├── 01-media-review-scheduler/      # 题 1，自包含
│   ├── README.md
│   ├── src/ tests/ examples/ data/ docs/
│   ├── run.py
│   └── pyproject.toml / requirements.txt / .env.example
└── 02-content-planning-agent/      # 题 2，自包含
    ├── README.md
    ├── src/ tests/ examples/ data/ docs/
    ├── run.py
    └── pyproject.toml / requirements.txt / .env.example
```

如果评审只需要看一道题，直接进入对应目录；根 README 只负责导航，不重复项目实现。
