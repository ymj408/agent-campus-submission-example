# 04 · AI 辅助范围、个人贡献与验证

> 本文件按考核要求如实记录 AI 使用范围。**不把 AI 生成当成候选人个人贡献，也不把 Mock 结果当成真实模型效果。**

## 1. 使用的 AI 工具

| 项目 | 记录 |
|---|---|
| 工具 | DeepSeek Harness 的编码 Agent（Web GUI 会话） |
| 模型标识 | `deepseek-flash` |
| 会话日期 | 2026-11-05（Asia/Shanghai） |
| 使用方式 | 读取题目与工作区；生成项目代码、测试、数据、文档；执行命令验证 |
| 其他代码生成工具 | 本次会话未使用其他 AI 编程工具 |

## 2. 候选人提供的内容

本次会话中，候选人提供/确定了：

- 考核题目：项目题 1「素材审核与发布排期 Agent」；
- 统一交付规范：README、src、tests、examples、data、docs 01–05、依赖文件、.env.example；
- 验收点：正常排期、信息缺失、规则风险、时段冲突、重复提交、保存失败；
- 关键业务边界：只保存草稿、不发布；硬性时间未经同意不得改变；达到上限返回未完成；
- 要求使用真实 ReAct 循环，而不是固定流程或预设决策日志。

候选人**尚未在本次会话中进行人工逐行改写**；提交前应完成代码复核、补充个人贡献说明，并填入真实 GitHub 地址。本文不虚构候选人未执行的修改。

## 3. AI 生成/修改的范围

| 范围 | 具体内容 | 是否人工改写 |
|---|---|---|
| 设计 | 领域模型、工具契约、确认边界、幂等键、最终状态推导 | 否（本次会话） |
| 代码 | `src/media_scheduler_agent/` 下全部模块 | 否 |
| 数据 | `data/` 下 6 个素材、规则、排期、补查工单 | 否 |
| 测试 | `tests/` 下 47 个测试 | 否 |
| 运行记录 | `examples/cases/`、`outputs/`、`traces/` | 由脚本生成 |
| 文档 | README、docs/01–05、examples/README | 否 |
| 依赖 | `requirements.txt`（无第三方运行时依赖）、`pyproject.toml` | 否 |
| 配置 | `.env.example` | 否 |

## 4. AI 生成过程中实际发生的修正

这些不是“完美一次生成”，而是本次会话中可复核的调整：

1. **初始时段粒度为 30 分钟**：导致 18:00 冲突时最近可用是 18:30，不符合参考实例“建议 19:00”。改为 60 分钟刻度，并让替代时段优先选择之后的最近时段。
2. **待办重复**：信息不足场景同时输出规则缺失项、字段清单和提问，造成重复。改为存在 pending 提问时不再重复字段清单。
3. **模型 finish 可能虚报成功**：增加最终状态由工具事实推导的收口逻辑，并用 `SaveThenLiePlanner` 测试“谎报 draft_saved → 运行时 save_failed”。
4. **Windows 测试临时目录权限**：`tempfile.mkdtemp` 的 0o700 在沙箱下产生不可穿透 DACL；测试改用工作区内 `Path.mkdir()` 的 `TestDir`。这是测试环境适配，不是产品逻辑。
5. **Trace 参数清理**：将 `rationale` 从工具参数中剥离，只作为 `decision_basis` 记录，避免同一句依据重复出现。
6. **真实模型适配器**：用本地假 OpenAI-compatible 网关验证 5 次 tool call 全链路；没有把假网关结果写成“真实模型已验证”。

## 5. 验证记录

### 5.1 实际执行的命令

```bash
python -m compileall -q src examples
python run.py verify
python examples/run_examples.py
python scripts/run_tests.py
python -m unittest discover -s tests -t . -v
```

### 5.2 实际结果

| 验证 | 结果 |
|---|---|
| 数据加载 | 6 素材 / 6 规则 / 3 条已有排期 / Asia/Shanghai |
| 示例场景 | 8 个场景、9 次运行、3 份草稿、0 次发布 |
| 场景检查 | 8/8 `checks_passed=true` |
| 测试 | 47 tests, 0 failures, 0 errors, success=True |
| 参考实例 | 18:00 冲突 → 19:00 pending；重复提交同一草稿 |
| 保存失败 | `save_failed`，0 草稿 |
| 执行上限 | `step_limit_reached`，Trace 有 limit 事件 |

产物：

- [`examples/test-results.txt`](../examples/test-results.txt)
- [`examples/test-summary.json`](../examples/test-summary.json)
- [`examples/outputs/index.json`](../examples/outputs/index.json)
- [`examples/traces/`](../examples/traces/)

## 6. AI 未完成/未验证的部分

1. **真实模型调用**：环境没有 API Key，未对真实 DeepSeek/OpenAI 服务发起请求；仅测试了 OpenAI-compatible 协议。不能据 Mock 结果声称真实模型效果好。
2. **GitHub 远端**：当前工作区为本地 Git 仓库；未创建/推送远程仓库（没有账号授权）。
3. **人工复核**：候选人尚未逐文件复核；本文不把自动测试通过等同于人工验收。
4. **生产级能力**：没有真实发布、审批 UI、数据库、多进程锁、监控。
5. **真实业务规则**：规则库为合成示例，未经法务/运营确认。

## 7. 候选人提交前应补充的个人贡献

建议候选人按实际情况填写以下内容（不要照抄）：

- 我额外修改/重写了哪些文件，为什么；
- 我运行了哪些命令，观察到什么与 AI 描述不同的结果；
- 我如何核对“只保存草稿、不发布”“硬性时间待确认”“重复提交幂等”三条边界；
- 我是否用真实模型跑过 case，模型输出与本仓库 Mock 输出有何差异；
- 实际投入时间与未完成部分（可同步到 `docs/05-retrospective.md`）。

## 8. 隐私与密钥

- 所有素材、账号、排期均为合成数据；
- `.env` 被 `.gitignore` 忽略；
- Trace 与结果不包含 API Key；
- 未提交任何真实平台账号、品牌素材或用户隐私。
