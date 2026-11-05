# 04 · AI 辅助范围、个人贡献与验证

> 本文件如实记录题 2 的 AI 辅助范围。**不把 AI 生成当成候选人个人贡献，也不把 Mock 结果当成真实模型效果。**

## 1. AI 工具

| 项目 | 记录 |
|---|---|
| 工具 | DeepSeek Harness 编码 Agent（Web GUI 会话） |
| 模型标识 | `deepseek-flash` |
| 日期 | 2026-11-05（Asia/Shanghai） |
| 使用方式 | 阅读题目、生成代码/测试/数据/文档、执行命令验证 |
| 其他代码生成工具 | 本次会话未使用 |

## 2. 候选人提供的内容

- 题目：项目题 2「内容任务规划 Agent」；
- 验收点：正常完成、缺预算/截止、工具失败、预算超限、重复执行、失败续跑；
- 关键业务要求：预算计算、步骤依赖、实际状态、保留成功结果、不能把失败步骤写成成功；
- 仓库：`https://github.com/ymj408/agent-campus-submission-example`，题 2 放在 `02-content-planning-agent/`。

候选人**尚未在本次会话中进行人工逐行改写**；提交前应复核代码、补充个人贡献说明。本文不虚构候选人未执行的修改。

## 3. AI 生成/修改范围

| 范围 | 内容 | 人工改写 |
|---|---|---|
| 设计 | 预算/截止/依赖/重规划/续跑状态机 | 否（本次会话） |
| 代码 | `src/content_planner/` 全部模块 | 否 |
| 数据 | 5 素材、5 工具费用、4 方案模板 | 否 |
| 测试 | 40 个测试 | 否 |
| 运行记录 | 10 组输入、12 次运行、4 份方案、Trace | 脚本生成 |
| 文档 | README、docs/01–05、examples/README | 否 |

## 4. 实际发生的修正

1. **失败信息循环引用**：最初把完整 plan 放进 error.details，plan 内的 failed step 又引用 error，导致 JSON 循环。改为只传 `step_id / plan_id / preserved_steps`。
2. **重规划后仍被旧失败阻塞**：Planner 重新估算新方案时清除 failed_step，避免把上一个方案的海报失败带到无海报方案。
3. **续跑后待办仍显示旧失败**：步骤重新成功后写入 `resolved_at`；已保存方案不再显示“去续跑”。
4. **重复执行第二次仍执行全部步骤**：`get_brief` 返回已有方案，Planner 直接 `plan_saved_deduplicated`，只调用 1 次工具。
5. **`material_id` 在保存方案中缺失**：方案文件补上顶层的 `material_id`，人类可读输出可正确显示素材。

这些都是本次会话中可复核的调整，不是“一次生成即完美”。

## 5. 验证记录

```bash
python -m compileall -q src run.py
python run.py verify
python examples/run_examples.py
python scripts/run_tests.py
```

| 验证 | 实际结果 |
|---|---|
| 数据 | 5 素材 / 5 工具 / 4 方案 |
| 场景 | 10 组 / 12 次运行 / 4 份方案 / 10 个检查通过 |
| 测试 | 40 tests, 0 failures, 0 errors |
| 参考实例 | 12 超预算 → 8 方案 → 保存 |
| 预算不足 | 最低 6、预算 3、缺口 3，不保存 |
| 海报失败 | 改选 7 的无海报方案，脚本保留 |
| 审核失败 | 停止，脚本/海报保留，方案未保存 |
| 续跑 | 第二次不重新执行 script/poster |
| 重复 | 方案数 1，第二次只 get_brief |
| 保存失败 | `save_failed`，0 方案 |

产物：

- [examples/test-results.txt](../examples/test-results.txt)
- [examples/test-summary.json](../examples/test-summary.json)
- [examples/outputs/index.json](../examples/outputs/index.json)
- [examples/traces/](../examples/traces/)

## 6. 未完成/未验证

1. **真实模型**：没有 API Key，未对真实 DeepSeek/OpenAI 服务发起请求；只测了本地假网关的协议链路。
2. **人工复核**：候选人尚未逐文件复核；自动测试通过不等于人工验收。
3. **生产级**：没有真实发布、审批、数据库、多进程锁、监控。
4. **真实报价**：工具费用/素材成本为合成数据。

## 7. 提交前候选人应补充

- 实际修改/重写了哪些文件，为什么；
- 实际跑过的命令与观察；
- 如何核对“预算 12→8”“失败步骤保留”“重复只保存一份”；
- 是否用真实模型跑过 case，差异是什么；
- 实际投入时间与未完成项（同步 `docs/05-retrospective.md`）。

## 8. 隐私与密钥

- 所有素材、工具费用、方案均为合成数据；
- `.env` 被 `.gitignore` 忽略；
- Trace/结果不含 API Key；
- 未提交真实品牌素材、账号或用户隐私。
