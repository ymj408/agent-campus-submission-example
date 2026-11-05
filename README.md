# 校招 AI Agent 项目提交

本仓库按**题号目录**组织，每个题目保持自包含（README / src / tests / examples / data / docs）。

> 当前已完成并提交**项目题 1、2、3、4**，每题独立自包含。

## 题目索引

| 题号 | 题目 | 状态 | 入口 | 验证 |
|---:|---|---|---|---|
| 1 | 素材审核与发布排期 Agent | ✅ 已完成 | [01-media-review-scheduler/](01-media-review-scheduler/) | 47 tests |
| 2 | 内容任务规划 Agent | ✅ 已完成 | [02-content-planning-agent/](02-content-planning-agent/) | 40 tests |
| 3 | 企业知识问答 Agent | ✅ 已完成 | [03-enterprise-knowledge-qa-agent/](03-enterprise-knowledge-qa-agent/) | 40 tests |
| 4 | 短视频脚本 Agent | ✅ 已完成 | [04-short-video-script-agent/](04-short-video-script-agent/) | 39 tests |

考核要求是“四选一”，但当前仓库已提供四个完整项目，可任选其一评审；每个题目目录独立，互不影响。

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

## 题 3 快速入口

```bash
cd 03-enterprise-knowledge-qa-agent
python run.py verify
python run.py run --question "总部差旅报销期限多久？"
python run.py run --question "报销期限多久？"
python scripts/run_tests.py
```

- 题 3 说明：[03-enterprise-knowledge-qa-agent/README.md](03-enterprise-knowledge-qa-agent/README.md)
- 明确答案：[D01 30 天](03-enterprise-knowledge-qa-agent/examples/outputs/01-clear-answer.json)
- 文档冲突：[D01/D02 冲突待确认](03-enterprise-knowledge-qa-agent/examples/outputs/02-document-conflict.json)
- 上下文追问：[华南 → 总部](03-enterprise-knowledge-qa-agent/examples/outputs/05-context-followup.json)
- 资料外：[无资料不回答](03-enterprise-knowledge-qa-agent/examples/outputs/03-out-of-scope.json)
- 运行总览：[examples/outputs/index.json](03-enterprise-knowledge-qa-agent/examples/outputs/index.json)

## 题 4 快速入口

```bash
cd 04-short-video-script-agent
python run.py verify
python run.py run --theme "通勤降噪耳机" --audience "通勤上班族" --platform douyin --target-duration 30
python scripts/run_tests.py
```

- 题 4 说明：[04-short-video-script-agent/README.md](04-short-video-script-agent/README.md)
- 正常脚本：[38 → 28 秒，P01 引用](04-short-video-script-agent/examples/outputs/01-normal-30s.json)
- 超时长失败：[14 秒仍超 10 秒](04-short-video-script-agent/examples/outputs/04-duration-unmet.json)
- 规则风险修正：[删除“效率提升 80%”](04-short-video-script-agent/examples/outputs/07-rule-risk-clean.json)
- 运行总览：[examples/outputs/index.json](04-short-video-script-agent/examples/outputs/index.json)

## 提交信息

- **仓库**：<https://github.com/ymj408/agent-campus-submission-example>
- **项目目录**：`01-media-review-scheduler/`、`02-content-planning-agent/`、`03-enterprise-knowledge-qa-agent/`、`04-short-video-script-agent/`
- **最终 commit SHA**：见远程 `main` 分支最新提交，或本仓库提交回复
- **AI 使用记录**：题 1 [docs/04-ai-usage.md](01-media-review-scheduler/docs/04-ai-usage.md)；题 2 [docs/04-ai-usage.md](02-content-planning-agent/docs/04-ai-usage.md)；题 3 [docs/04-ai-usage.md](03-enterprise-knowledge-qa-agent/docs/04-ai-usage.md)；题 4 [docs/04-ai-usage.md](04-short-video-script-agent/docs/04-ai-usage.md)

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
├── 02-content-planning-agent/      # 题 2，自包含
│   ├── README.md
│   ├── src/ tests/ examples/ data/ docs/
│   ├── run.py
│   └── pyproject.toml / requirements.txt / .env.example
├── 03-enterprise-knowledge-qa-agent/ # 题 3，自包含
│   ├── README.md
│   ├── src/ tests/ examples/ data/ docs/
│   ├── run.py
│   └── pyproject.toml / requirements.txt / .env.example
└── 04-short-video-script-agent/    # 题 4，自包含
    ├── README.md
    ├── src/ tests/ examples/ data/ docs/
    ├── run.py
    └── pyproject.toml / requirements.txt / .env.example
```

如果评审只需要看一道题，直接进入对应目录；根 README 只负责导航，不重复项目实现。
