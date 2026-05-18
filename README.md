# task-dashboard

A [Claude Code skill](https://code.claude.com/docs/en/skills) that maintains a beautiful, self-contained HTML dashboard so you can watch Claude work on multi-step tasks in your browser.

> 一个 Claude Code skill，在执行多步骤任务时维护一份漂亮的、自包含的 HTML 看板，让你可以在浏览器里实时看到 Claude 正在做什么、做完了什么、任务之间怎么关联。

## What it gives you

- A **single-file HTML** at `.claude-tasks/dashboard.html` — double-click to open, no server, no dependencies.
- An auto-laid-out **SVG flow chart** showing task dependencies at the top.
- A **Kanban board** with four columns: Pending / In Progress / Completed / Archived.
- **Persistent history** — abandoned and deleted tasks are kept in the archive with their reasons, never silently removed.
- Built-in **dark/light themes**, responsive layout, keyboard navigation, reduced-motion support.

## How it triggers

The skill activates when:

- You type `/task-dashboard` or say "open the task dashboard", "维护任务看板", etc.
- You say something mid-session like "give me a task board to see what's going on" — Claude will reconstruct one from the conversation history.
- `.claude-tasks/dashboard.json` already exists in the project — Claude keeps it updated automatically.

It does **not** auto-trigger just because a task has many steps. You ask, it shows up.

## How it works

1. Claude maintains `.claude-tasks/dashboard.json` (a small JSON file you can also edit by hand).
2. Whenever a task changes state, Claude runs `scripts/render.py` to regenerate the HTML.
3. You refresh your browser to see the latest state.

Data flow is one-way: Claude writes, you read. If you edit the JSON manually, Claude respects your version.

## Installation

```bash
git clone <this-repo> ~/.claude/skills/task-dashboard
```

Claude Code picks up skills under `~/.claude/skills/` automatically. No restart needed for fresh installs into an already-watched directory.

If you'd rather scope it to one project:

```bash
git clone <this-repo> .claude/skills/task-dashboard
```

## Layout

```
task-dashboard/
├── SKILL.md                    # what Claude reads to know how to use the skill
├── scripts/
│   └── render.py               # JSON → HTML renderer (Python 3 stdlib only)
└── references/
    ├── schema.md               # full JSON schema reference
    ├── workflow.md             # how-to with concrete examples
    └── example.json            # a complete sample you can render right now
```

## Try it without Claude

The renderer is just a Python script. You can run it on the bundled sample:

```bash
python3 scripts/render.py references/example.json /tmp/dashboard.html
open /tmp/dashboard.html
```

## Designing your own tasks

See [references/schema.md](references/schema.md) for the data model and [references/workflow.md](references/workflow.md) for how Claude maintains it across a session.

## Suggested `.gitignore`

If you don't want to commit the dashboard to your repo, add this to `.gitignore`:

```
.claude-tasks/
```

If you *do* want it in version control (e.g. so reviewers can see the work in PRs), commit `dashboard.json` and ignore the generated HTML:

```
.claude-tasks/dashboard.html
```

## Compatibility

- Python 3.9+ (uses standard library only — no pip install needed)
- Works in Claude Code and any other agent runtime that respects the AgentSkills convention
- Renderer is pure Python and never reaches the network

## License

MIT.
