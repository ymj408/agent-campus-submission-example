# data/ 合成资料

全部为合成内容，不含真实品牌、账号或用户数据。

| 文件 | 用途 |
|---|---|
| `materials.json` | 5 个素材；MAT-04 授权过期，会被检索排除 |
| `tool_costs.json` | 5 个工具的费用与耗时 |
| `plan_templates.json` | 4 个方案模板与步骤依赖 |

## 成本口径

```text
方案总成本 = 素材成本 + 所有步骤工具费用
方案总耗时 = 所有步骤耗时之和
```

参考实例：

- `premium_video`：MAT-03（4）+ 脚本 2 + 剪辑 3 + 审核 1 + 分发 2 = **12**
- `lean_article`：MAT-01（2）+ 脚本 2 + 海报 1 + 审核 1 + 分发 2 = **8**
- `lean_no_poster`：MAT-01（2）+ 脚本 2 + 审核 1 + 分发 2 = **7**
- `organic_text`：MAT-05（1）+ 脚本 2 + 审核 1 + 分发 2 = **6**

预算 10 时，初始 `premium_video` 超 2；替代 `lean_article` 剩余 2。

## 步骤依赖

- `script`：无依赖
- `poster` / `video_edit`：依赖 `script`
- `review`：依赖对应的制作步骤
- `distribution`：依赖 `review`

工具执行器会拒绝未满足依赖的步骤，模型不能通过提示词绕过。

## 方案保存

`save_plan` 只写本地 `plans/PLAN-*.json` 与 artifacts；不调用真实发布。重复请求由幂等键去重。
