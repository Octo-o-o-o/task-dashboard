---
name: task-dashboard
description: 维护一份项目本地的 HTML 任务看板（SVG 流程图 + Kanban + 任务详情），让用户用浏览器实时跟踪 Claude 的多步骤工作。当用户输入 `/task-dashboard`（可选附带任务描述）、说"打开/维护/补一个任务看板"、"task dashboard"、"可视化任务"，或当前项目已存在 `.claude-tasks/dashboard.json` 时启用。`/task-dashboard` 是单一入口，不论用户在会话什么阶段输入都按 SKILL.md 中的"启用决策"流程走：根据上下文自动判断是初始化新看板、还是从已发生的对话补建。已有看板的会话，后续每个有任务进展的轮次结束前自动同步并重渲染。
license: MIT
allowed-tools: Bash(python3 *) Bash(mkdir *) Read Write Edit
---

# Task Dashboard

在项目根目录的 `.claude-tasks/` 下维护两个文件：

- `dashboard.json` — 任务数据（你直接读写）
- `dashboard.html` — 由 `render.py` 从 JSON 生成的单文件页面（用户在浏览器查看）

## 启用决策

当用户输入 `/task-dashboard`（带或不带参数），或说出等效的中文请求时，按这棵决策树执行——**用户不需要解释自己在会话什么阶段**：

```
.claude-tasks/dashboard.json 已经存在？
├── 是 → 同步当前会话与 JSON 之间的差异（新增/状态变化/notes）→ 渲染 → 简短告知"已刷新"
└── 否
    ├── 用户在本条消息或紧邻的上文里给了明确的任务/工作目标
    │   （例如 "/task-dashboard 帮我重构鉴权" 或刚刚才说"我要做 X、Y、Z"）
    │   → 用这些描述拆分初始任务（全部 pending） → 渲染 → 告诉用户文件路径
    └── 用户只输入 /task-dashboard，没有附带新任务描述
        → 回顾整个会话上下文（用户消息 + 你的回复 + TodoWrite 状态），
          把已计划/执行/讨论过的工作整理为任务列表，按真实状态填好：
            • 已经做完的 → completed + 真实 started_at/completed_at
            • 正在做的    → in_progress + started_at
            • 还没开始的 → pending
            • 受阻的     → blocked + notes 写阻塞原因
          每个任务的关键决策放进 notes（只记结论）。
          → 渲染 → 告诉用户"已根据会话补建看板"+ 文件路径
```

**核心承诺：用户只输入 `/task-dashboard` 一句话，你就要自动做对的事。** 不要反问"你是要新建还是补建"。

## 操作步骤（无论哪种分支都是这套）

1. 准备目录：`mkdir -p .claude-tasks`
2. 写入/更新 `.claude-tasks/dashboard.json`（schema 见下方速查或 [references/schema.md](references/schema.md)）
3. 渲染：
   ```bash
   python3 "${CLAUDE_SKILL_DIR}/scripts/render.py" .claude-tasks/dashboard.json .claude-tasks/dashboard.html
   ```
4. 首次创建时告诉用户：路径 `./.claude-tasks/dashboard.html`，在 Finder/资源管理器双击即可打开。后续更新不必每次汇报。

## 持续维护

启用后，每个**会引起任务状态变化**的轮次结束前：

1. **判断本轮有没有动**：新增任务？某个任务进入 `in_progress`？完成？放弃？记下决策？纯讨论/读代码且没有任何任务推进的轮次跳过。
2. **更新 JSON**：
   - 新任务 → append，分配下一个 `id`（`T` + 递增数字）和 `number`。
   - 状态变化 → 改 `status`；首次进入 `in_progress` 时填 `started_at`，进入 `completed` 时填 `completed_at`（ISO8601 UTC，例如 `2026-05-18T10:30:00Z`）。
   - 关键决策/受阻原因/放弃理由 → append 到 `notes`（只记结论，不复述对话）。
   - 更新 `session.updated_at`。
3. **重新渲染**：跑同一条 render.py 命令。

只有这几种情况要主动告诉用户："新增了 ≥3 个任务"、"放弃/删除了任务"、"完成了一个关键里程碑"。其他时候沉默更新。

## 数据 schema（速查）

```json
{
  "session": {
    "id": "<会话标识>",
    "title": "<用户能看懂的工作目标>",
    "started_at": "<ISO8601>",
    "updated_at": "<ISO8601>",
    "project_path": "<cwd>"
  },
  "tasks": [
    {
      "id": "T1",
      "number": 1,
      "title": "动词开头的简短标题",
      "description": "为什么/做什么（可选）",
      "status": "pending|in_progress|blocked|completed|abandoned|deleted",
      "phase": "plan|execute|verify|followup",
      "depends_on": ["T0"],
      "tags": ["backend"],
      "started_at": "<ISO8601 或省略>",
      "completed_at": "<ISO8601 或省略>",
      "notes": [{"at": "<ISO8601>", "text": "决策或事实"}]
    }
  ]
}
```

完整字段说明见 [references/schema.md](references/schema.md)。可直接复制的完整样例见 [references/example.json](references/example.json)。工作流示例见 [references/workflow.md](references/workflow.md)。

## 关键规则

- **永远不要从 JSON 物理删除任务**。用户说"取消"用 `status: "abandoned"`，说"删除"用 `status: "deleted"`，并在 `notes` 写原因。归档区会展示这些任务，保留历史决策的可追溯性。
- **`depends_on` 决定流程图布局**。线性工作流也要标依赖（T2 依赖 T1、T3 依赖 T2），SVG 才会有正确的层级。被依赖的在上层。
- **TodoWrite 是首选短期追踪**，dashboard 是长期可视化。两者单向同步：TodoWrite 状态变化 → 同步到 dashboard。不要反过来用 dashboard 驱动 TodoWrite。
- **用户编辑了 JSON** → 以用户版本为准，再触发渲染即可，不要回滚。
- **JSON 损坏导致 render 失败** → 用 Read 检查 dashboard.json，修正 JSON 语法后重试。错误信息会指出问题行。

## 不要做的事

- 不要每次回答都汇报"我更新了看板"。
- 不要在 `notes` 里复述整段对话——只记决策与结论。
- 不要因为任务超过 3 步就自动启用看板——只在用户明确请求或已有看板时启用。
- 不要在用户输入 `/task-dashboard` 后反问"你是要初始化还是补建"——按决策树自己判断。
- 不要自动改 `.gitignore`——首次创建时可提醒用户把 `.claude-tasks/` 加进去，是否加由用户决定。
- 不要尝试做实时刷新（WebSocket 等）——刷新由用户在浏览器手动完成。
