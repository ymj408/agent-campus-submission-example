# 04 · AI 辅助范围、个人贡献与验证

> 本文件如实记录题 4 的 AI 使用范围。**不把 AI 生成当成候选人个人贡献，也不把 Mock 结果当成真实模型创作效果。**

## 1. AI 工具

| 项目 | 记录 |
|---|---|
| 工具 | DeepSeek Harness 编码 Agent（Web GUI 会话） |
| 模型标识 | `deepseek-flash` |
| 日期 | 2026-11-05（Asia/Shanghai） |
| 使用方式 | 阅读题目、生成代码/测试/数据/文档、执行命令验证 |
| 其他代码生成工具 | 本次会话未使用 |

## 2. 候选人提供的内容

- 题目：项目题 4「短视频脚本 Agent」；
- 验收点：正常生成、缺关键信息、无素材、工具失败、超时长修正或明确失败；
- 关键要求：P01 只有“支持字幕”事实；30 秒目标首稿 38 秒要压缩到 28 秒；无效果数据不得加“效率提升 80%”；
- 仓库：`https://github.com/ymj408/agent-campus-submission-example`，题 4 放在 `04-short-video-script-agent/`。

候选人**尚未在本次会话中进行人工逐行改写**；提交前应复核代码并补充个人贡献说明。

## 3. AI 生成/修改范围

| 范围 | 内容 | 人工改写 |
|---|---|---|
| 设计 | 脚本结构、时长修正、claims 追溯、规则检查 | 否 |
| 代码 | `src/script_agent/` 全部模块 | 否 |
| 数据 | 5 个素材、品牌规则 | 否 |
| 测试 | 39 个测试 | 否 |
| 运行记录 | 8 组输入、8 次运行、Trace | 脚本生成 |
| 文档 | README、docs/01–05、examples/README | 否 |

## 4. 实际发生的修正

1. **28 秒稿第 4 分镜语速 1.71 字/秒**：低于 2.0 阈值，规则检查阻断。把旁白加长到 14 字，语速 2.0，最终 pass。
2. **无素材场景误命中**：搜索 query 中固定带“产品 素材”，导致“宠物猫粮”也命中耳机素材。改为只使用 theme/product，无素材场景恢复 `insufficient_material`。
3. **时长风险重复**：规则 violation 已包含 duration_exceeded，最终 risks 又追加“总时长…”，改为去重。
4. **规则修正循环**：清理稿使用 `short_clean` 版本标识，避免再次进入“删除 claim”分支。
5. **模型自报时长不采信**：最终 total 一律取工具 duration；测试用“模型说 28、工具 38”覆盖。
6. **工具失败重试**：estimate/check 连续失败后由 Planner 明确 `tool_failure`，不输出 ready。

## 5. 验证

```bash
python -m compileall -q src run.py
python run.py verify
python examples/run_examples.py
python scripts/run_tests.py
```

| 验证 | 结果 |
|---|---|
| 素材 | 5 个，4 个可用；P04 授权过期 |
| 场景 | 8 / 8 检查通过 |
| 测试 | 39 tests, 0 failures, 0 errors |
| 正常 | 38 → 28，P01，规则 pass |
| 超时失败 | 38 → 28 → 14，14 > 10，duration_unmet |
| 规则风险 | 删除“效率提升 80%”后 pass |
| 目标变化 | 20 秒目标触发 14 秒稿 |
| 工具失败 | 无 ready 脚本 |
| 模型谎报 | 时长/规则覆盖 |

产物：

- [examples/test-results.txt](../examples/test-results.txt)
- [examples/test-summary.json](../examples/test-summary.json)
- [examples/outputs/index.json](../examples/outputs/index.json)
- [examples/traces/](../examples/traces/)

## 6. 未完成/未验证

1. **真实模型**：没有 API Key，未对线上模型发起请求；仅测本地假网关协议。
2. **真实 TTS/剪辑**：时长是分镜秒数汇总，不是实际合成耗时。
3. **真实素材/规则**：全部为合成数据。
4. **人工复核**：候选人尚未逐文件复核。
5. **渲染/导出**：没有视频、字幕文件生成。

## 7. 提交前候选人应补充

- 实际修改/重写了哪些文件，为什么；
- 如何核对 38→28、P01 facts、无效果数据；
- 是否用真实模型跑过 01–08，创作质量差异是什么；
- 实际投入时间与未完成项（同步 `docs/05-retrospective.md`）。

## 8. 隐私与密钥

- 所有素材、产品、规则均为合成数据；
- `.env` 被 `.gitignore` 忽略；
- Trace/结果不含 API Key；
- 未提交真实品牌素材、产品参数或用户隐私。
