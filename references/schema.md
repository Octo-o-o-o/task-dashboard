# Dashboard JSON Schema

`.claude-tasks/dashboard.json` 的完整数据规范。

## 顶层

```json
{ "session": { ... }, "tasks": [ ... ] }
```

只有这两个 key。renderer 忽略其他顶层字段。

## session 对象

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | string | 是 | 会话标识。可用 `${CLAUDE_SESSION_ID}` 或会话开始时间 |
| `title` | string | 是 | 用户能看懂的本次工作目标（如 `"重构鉴权中间件"`） |
| `started_at` | string | 是 | ISO8601 UTC，会话开始时间，例如 `"2026-05-18T10:00:00Z"` |
| `updated_at` | string | 是 | ISO8601 UTC，最近一次 JSON 更新时间。renderer 若发现缺失会自动填当前时间 |
| `project_path` | string | 否 | 项目根目录绝对路径 |

## task 对象

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | string | 是 | 全局唯一 ID。建议形式：`T1`, `T2`, ... |
| `number` | integer | 是 | 显示用编号，从 1 递增（id 与 number 通常等价） |
| `title` | string | 是 | 简短标题（动词开头，建议 ≤ 24 个汉字 / 36 个 ASCII） |
| `description` | string | 否 | 详细说明（为什么 / 做什么 / 怎么验收） |
| `status` | enum | 是 | 见下方"状态机" |
| `phase` | enum | 否 | `plan` / `execute` / `verify` / `followup` |
| `depends_on` | string[] | 否 | 当前任务依赖的任务 ID 数组。renderer 据此自动布局流程图 |
| `tags` | string[] | 否 | 自由标签 |
| `started_at` | string | 否 | 首次进入 `in_progress` 时填，ISO8601 UTC |
| `completed_at` | string | 否 | 进入 `completed` 时填，ISO8601 UTC |
| `notes` | object[] | 否 | 决策时间线，每项 `{ "at": "<ISO8601>", "text": "<结论或事实>" }` |

### notes 写什么

只记**决策、关键事实、阻塞原因**。不要复述整段对话，不要把代码片段贴进来。

正面例子：
- `"和团队确认采用 RS256（非 HS256），理由：跨服务验签更安全"`
- `"运营评估后不做：refresh token 短 TTL 已经够"`
- `"受阻：等 SecOps 给出 audience claim 规范"`

反面例子：
- ❌ `"用户问：'JWT 怎么实现？' 我回答：'我们可以用...'"`（复述对话）
- ❌ `"function signToken(payload) { return jwt.sign(...) }"`（代码细节）

## 状态机

```
                    ┌──────────┐
       (用户取消) ◄─┤ pending  ├─► (开始干活)
            │       └──────────┘            │
            │                                ▼
            │           ┌──────────────┐
            │           │ in_progress  ├──► completed
            │           └──────────────┘
            │                  ▲ │
            │                  │ ▼
            │           ┌──────────────┐
            │           │   blocked    │  (受外部因素)
            │           └──────────────┘
            ▼
       ┌──────────┐
       │ abandoned│   (用户改主意，不做了)
       └──────────┘

  deleted 与 abandoned 类似，但语义更强（用户明确说"删了"）
```

### 各状态的渲染

| status | 含义 | 看板列 | 视觉 |
|---|---|---|---|
| `pending` | 已规划未开始 | 待办 | 灰色 ○ |
| `in_progress` | 正在执行 | 进行中 | 蓝色 ◐ |
| `blocked` | 受阻（依赖或外部因素） | 进行中 | 橙色 ⏸ |
| `completed` | 已完成 | 已完成 | 绿色 ✓ |
| `abandoned` | 用户主动放弃 | 已归档 | 灰 + 删除线 ⊘ |
| `deleted` | 用户明确删除 | 已归档 | 灰 + 删除线 ✕ |

### 保留原则

任务一旦创建，**永远不要从 `tasks` 数组中删除**。即便用户说"删了它"，也只是改 `status: "deleted"` 并在 `notes` 写原因。这样：

- 用户可以在归档区看到曾经的决策和放弃理由
- 流程图不会因任务消失出现悬空依赖
- 重启会话或回顾历史时仍能追溯每个想法

## 时间戳规范

- 全部用 ISO8601 UTC，带 `Z` 后缀
- 精度：秒级即可（`2026-05-18T10:30:00Z`）
- 不需要毫秒、不要本地时区
- renderer 不解析时间戳，按字符串原样展示

## 完整示例

见 [example.json](example.json)。
