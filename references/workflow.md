# Workflow Reference

具体的操作示例。本文档默认你已经读过 SKILL.md。

## 模式一：会话开始时启用

**用户**：`/task-dashboard 帮我重构鉴权中间件，先调研现状，再设计 JWT 方案，最后实现 + 写单测`

**你的步骤**：

1. 准备目录
   ```bash
   mkdir -p .claude-tasks
   ```

2. 写 `.claude-tasks/dashboard.json`（参考 [example.json](example.json) 的格式，初始状态全部 `pending`）：
   ```json
   {
     "session": {
       "id": "2026-05-18T10:00:00",
       "title": "重构鉴权中间件（JWT 改造）",
       "started_at": "2026-05-18T10:00:00Z",
       "updated_at": "2026-05-18T10:00:00Z",
       "project_path": "/Users/me/project"
     },
     "tasks": [
       {"id":"T1","number":1,"title":"调研现有 auth 实现","status":"pending","phase":"plan"},
       {"id":"T2","number":2,"title":"设计 JWT 流程","status":"pending","phase":"plan","depends_on":["T1"]},
       {"id":"T3","number":3,"title":"实现 token 签发","status":"pending","phase":"execute","depends_on":["T2"]},
       {"id":"T4","number":4,"title":"实现 token 验证中间件","status":"pending","phase":"execute","depends_on":["T2"]},
       {"id":"T5","number":5,"title":"写单元测试","status":"pending","phase":"verify","depends_on":["T3","T4"]}
     ]
   }
   ```

3. 渲染：
   ```bash
   python3 "${CLAUDE_SKILL_DIR}/scripts/render.py" .claude-tasks/dashboard.json .claude-tasks/dashboard.html
   ```

4. 告诉用户：
   > 已创建任务看板：`./.claude-tasks/dashboard.html`（在 Finder 双击打开）。共 5 个任务，现在开始 T1。

5. 开始 T1 时，把 T1.status 改 `in_progress`、填 `started_at`、跑 render.py。完成时改 `completed`、填 `completed_at`、跑 render.py。

## 模式二：会话中途补建

**会话已发生**：
- 用户让你"调研现有 auth 实现"，你 grep + Read 完成，给出了结论
- 用户让你"设计 JWT 流程"，你给出了设计稿，等用户确认
- 然后用户说："等等，给我补一个任务看板"

**你的步骤**：

1. 回顾会话：把已经发生的事整理为任务列表。
   - T1 "调研现有 auth 实现" → `completed`，时间戳填实际发生时间
   - T2 "设计 JWT 流程" → `in_progress`（用户还在确认）
   - 推断后续：T3 实现签发、T4 实现验证、T5 单测 → `pending`

2. 写 `dashboard.json`，把已发生任务的关键决策放进 `notes`：
   ```json
   {
     "id": "T1", "number": 1, "title": "调研现有 auth 实现",
     "status": "completed",
     "started_at": "2026-05-18T10:00:00Z",
     "completed_at": "2026-05-18T10:08:00Z",
     "notes": [{"at": "2026-05-18T10:08:00Z", "text": "现状：基于 cookie session + Redis"}]
   }
   ```

3. 渲染，告诉用户：
   > 已根据本次会话同步看板，包含已完成 T1 和进行中 T2，规划了 T3-T5。后续每轮我会自动维护它。

## 任务的常见操作

### 新增任务（用户：「再加一个 token 撤销」）

```text
读 dashboard.json
→ tasks.append({
    "id":"T6","number":6,
    "title":"实现 token 撤销机制",
    "status":"pending",
    "depends_on":["T3"],
    "tags":["security"]
  })
→ session.updated_at = now
→ 跑 render.py
```

### 推进任务（你开始执行 T3）

```text
T3.status = "in_progress"
T3.started_at = "2026-05-18T11:00:00Z"
→ render
```

T3 完成时：

```text
T3.status = "completed"
T3.completed_at = "2026-05-18T11:35:00Z"
T3.notes.append({"at":"...","text":"采用 jose 库，已封装 issueTokenPair()"})
→ render
```

### 取消任务（用户：「T6 不做了」）

```text
T6.status = "abandoned"
T6.notes.append({"at":"...","text":"运营评估后不做：refresh token 短 TTL 已经够"})
→ render
```

**不要从 tasks 数组里删掉它**。归档列会展示它。

### 删除任务（用户：「T4 删了，换方案」）

```text
T4.status = "deleted"
T4.notes.append({"at":"...","text":"改用方案 B：复用 T3 的验证逻辑"})
→ render
```

同样保留在 JSON 里。

### 受阻（任务卡在外部依赖）

```text
T4.status = "blocked"
T4.notes.append({"at":"...","text":"受阻：等 SecOps 给出 audience claim 规范"})
→ render
```

外部依赖解除后改回 `in_progress`。

## 何时跳过更新

- 用户问概念性问题、闲聊、要求只读看代码 → 没有任务推进，跳过。
- 你只是阅读了文件没改任何任务状态 → 跳过。
- 用户要求一个明显不属于当前 session 主题的工作 → 询问是否新建 session 或独立处理，不要把无关任务塞进同一份 dashboard。

## 反模式

| 错误做法 | 正确做法 |
|---|---|
| 每轮都跑 render.py 但任务无变化 | 只在数据真有变化时跑 |
| 用户取消任务 → 从 JSON 删除 | 改 `status` 为 `abandoned/deleted`，保留数据 |
| 把整段对话粘进 `notes` | `notes` 只记结论与决策 |
| 线性流程不写 `depends_on` | 每个任务都标 `depends_on`，流程图才有正确层级 |
| 拼命给短任务加 `phase`/`tags` | 这些字段都可选，没有就别填 |
| 复杂任务塞在一个 task 里 | 拆 3-7 个子任务（更多就分批，超过 12 个流程图会拥挤） |
| 告诉用户每次更新 | 沉默更新，重大变化才提醒 |
| 自动改 `.gitignore` | 首次创建时口头提醒，由用户决定 |
