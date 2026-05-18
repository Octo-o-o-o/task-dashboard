#!/usr/bin/env python3
"""Render a task dashboard JSON file to a self-contained HTML dashboard.

Usage:
    python3 render.py <input.json> <output.html>

The output is a single HTML file with inlined CSS, JS, and data. Open it
directly in a browser; no server required. See references/schema.md for
the expected input shape.
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from html import escape
from pathlib import Path

STATUS_META = {
    "pending":     {"label": "待办",   "color": "#94a3b8", "icon": "○", "column": "pending"},
    "in_progress": {"label": "进行中", "color": "#3b82f6", "icon": "◐", "column": "in-progress"},
    "blocked":     {"label": "受阻",   "color": "#f59e0b", "icon": "⏸", "column": "in-progress"},
    "completed":   {"label": "已完成", "color": "#22c55e", "icon": "✓", "column": "completed"},
    "abandoned":   {"label": "已放弃", "color": "#64748b", "icon": "⊘", "column": "archived"},
    "deleted":     {"label": "已删除", "color": "#64748b", "icon": "✕", "column": "archived"},
}

PHASE_META = {
    "plan":     {"label": "规划",   "color": "#8b5cf6"},
    "execute":  {"label": "执行",   "color": "#06b6d4"},
    "verify":   {"label": "验证",   "color": "#10b981"},
    "followup": {"label": "后续",   "color": "#f97316"},
}

KANBAN_COLUMNS = [
    ("pending",     "待办"),
    ("in-progress", "进行中"),
    ("completed",   "已完成"),
    ("archived",    "已归档"),
]


# ---------- layout ----------

def topo_layers(tasks):
    """Group tasks into layers by depends_on. Cycle-safe."""
    by_id = {t["id"]: t for t in tasks}
    incoming = {t["id"]: [d for d in (t.get("depends_on") or []) if d in by_id]
                for t in tasks}
    layers, placed, remaining = [], set(), set(by_id)
    while remaining:
        layer = [tid for tid in remaining if all(d in placed for d in incoming[tid])]
        if not layer:
            # cycle or unresolved — flush remaining as one layer to stay safe
            layer = list(remaining)
        layer.sort(key=lambda x: by_id[x].get("number") or 0)
        layers.append(layer)
        placed.update(layer)
        remaining.difference_update(layer)
    return layers


# ---------- svg ----------

def build_svg(tasks):
    visible = [t for t in tasks if t.get("status") not in ("deleted", "abandoned")]
    if not visible:
        return '<div class="svg-empty">暂无活跃任务</div>'

    layers = topo_layers(visible)
    node_w, node_h = 168, 40
    pad_x, pad_y = 28, 24
    cols = max(len(layer) for layer in layers)
    rows = len(layers)
    width = max(640, cols * (node_w + pad_x) + pad_x)
    height = rows * (node_h + pad_y) + pad_y

    pos = {}
    for r, layer in enumerate(layers):
        row_w = len(layer) * (node_w + pad_x) - pad_x
        start_x = (width - row_w) / 2
        for c, tid in enumerate(layer):
            pos[tid] = (start_x + c * (node_w + pad_x), pad_y + r * (node_h + pad_y))

    edges = []
    for t in visible:
        for dep in t.get("depends_on") or []:
            if dep not in pos:
                continue
            x1, y1 = pos[dep]
            x2, y2 = pos[t["id"]]
            sx, sy = x1 + node_w / 2, y1 + node_h
            ex, ey = x2 + node_w / 2, y2
            cy = (sy + ey) / 2
            edges.append(
                f'<path d="M{sx:.1f},{sy:.1f} C{sx:.1f},{cy:.1f} {ex:.1f},{cy:.1f} {ex:.1f},{ey:.1f}" '
                f'class="edge" marker-end="url(#arr)"/>'
            )

    nodes = []
    for t in visible:
        x, y = pos[t["id"]]
        meta = STATUS_META.get(t.get("status", "pending"), STATUS_META["pending"])
        color = meta["color"]
        num = escape(str(t.get("number", t.get("id", "?"))))
        title = escape((t.get("title") or "").strip())
        # truncate by visual width: ~ 18 CJK chars or 28 ASCII
        if sum(2 if ord(c) > 127 else 1 for c in title) > 28:
            cut = 0; w = 0
            for ch in title:
                w += 2 if ord(ch) > 127 else 1
                if w > 26: break
                cut += 1
            title = title[:cut] + "…"
        aria = escape(f"任务 {num} {meta['label']} {title}")
        nodes.append(f'''
<g class="svg-node" data-task-id="{escape(t["id"])}" tabindex="0" role="button" aria-label="{aria}">
  <rect x="{x:.1f}" y="{y:.1f}" rx="6" width="{node_w}" height="{node_h}" class="node-bg" stroke="{color}"/>
  <text x="{x + 12:.1f}" y="{y + 16:.1f}" fill="{color}" class="node-num">#{num}</text>
  <text x="{x + node_w - 12:.1f}" y="{y + 16:.1f}" fill="{color}" class="node-status" text-anchor="end">{escape(meta['label'])}</text>
  <text x="{x + 12:.1f}" y="{y + 31:.1f}" class="node-title">{title}</text>
</g>''')

    return f'''
<svg width="{int(width)}" height="{int(height)}" viewBox="0 0 {int(width)} {int(height)}"
     xmlns="http://www.w3.org/2000/svg" class="flow-svg" role="img" aria-label="任务流程图">
  <defs>
    <marker id="arr" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
      <path d="M0,0 L10,5 L0,10 z" class="arrow-head"/>
    </marker>
  </defs>
  {''.join(edges)}
  {''.join(nodes)}
</svg>'''


# ---------- cards ----------

def render_task_card(t):
    meta = STATUS_META.get(t.get("status", "pending"), STATUS_META["pending"])
    color = meta["color"]
    archived = t.get("status") in ("abandoned", "deleted")

    parts = []
    phase = PHASE_META.get(t.get("phase") or "")
    phase_html = (
        f'<span class="pill phase" style="--c:{phase["color"]}">{escape(phase["label"])}</span>'
        if phase else ""
    )

    deps = t.get("depends_on") or []
    deps_html = ""
    if deps:
        chips = " ".join(f'<span class="chip">#{escape(str(d))}</span>' for d in deps)
        deps_html = f'<div class="row deps"><span class="row-label">依赖</span>{chips}</div>'

    tags = t.get("tags") or []
    tags_html = ""
    if tags:
        chips = "".join(f'<span class="chip">{escape(str(g))}</span>' for g in tags)
        tags_html = f'<div class="row tags">{chips}</div>'

    times = []
    if t.get("started_at"):   times.append(f'开始 {escape(t["started_at"])}')
    if t.get("completed_at"): times.append(f'完成 {escape(t["completed_at"])}')
    times_html = f'<div class="times">{" · ".join(times)}</div>' if times else ""

    notes = t.get("notes") or []
    notes_html = ""
    if notes:
        items = "".join(
            f'<li><time>{escape(n.get("at",""))}</time><span>{escape(n.get("text",""))}</span></li>'
            for n in notes
        )
        notes_html = f'<details class="notes"><summary>笔记 · {len(notes)}</summary><ul>{items}</ul></details>'

    desc = (t.get("description") or "").strip()
    desc_html = f'<p class="desc">{escape(desc)}</p>' if desc else ""

    return f'''
<article class="card{' archived' if archived else ''}" data-task-id="{escape(t["id"])}" style="--c:{color}">
  <header>
    <span class="num">#{escape(str(t.get("number", t["id"])))}</span>
    <h3>{escape(t.get("title", "(无标题)"))}</h3>
    <span class="pill status" style="--c:{color}">{meta['icon']} {escape(meta['label'])}</span>
    {phase_html}
  </header>
  {desc_html}{deps_html}{tags_html}{times_html}{notes_html}
</article>'''


def render_kanban(tasks):
    cols = defaultdict(list)
    for t in tasks:
        meta = STATUS_META.get(t.get("status", "pending"), STATUS_META["pending"])
        cols[meta["column"]].append(t)

    parts = ['<div class="kanban">']
    for key, label in KANBAN_COLUMNS:
        items = cols[key]
        body = ("".join(render_task_card(t) for t in items)
                if items else '<div class="empty">—</div>')
        parts.append(
            f'<section class="col col-{key}">'
            f'<h2><span class="col-name">{label}</span><span class="count">{len(items)}</span></h2>'
            f'<div class="col-body">{body}</div></section>'
        )
    parts.append('</div>')
    return "".join(parts)


# ---------- stats ----------

def compute_stats(tasks):
    counts = defaultdict(int)
    for t in tasks:
        counts[t.get("status", "pending")] += 1
    active = len(tasks) - counts["abandoned"] - counts["deleted"]
    done = counts["completed"]
    pct = max(0, min(100, int(done / active * 100))) if active else 0
    return {
        "active": active, "done": done, "pct": pct,
        **{k: counts[k] for k in
           ("pending", "in_progress", "blocked", "completed", "abandoned", "deleted")},
    }


# ---------- html ----------

CSS = r"""
*,*::before,*::after{box-sizing:border-box}
:root{
  --bg:#0b1020; --panel:#0f172a; --panel-2:#1a2236;
  --fg:#e6edf7; --fg-2:#9aa6b8; --fg-3:#5d6a80;
  --line:#1d2740; --line-2:#2a3756;
  --accent:#60a5fa;
  --progress-from:#22c55e; --progress-to:#10b981;
}
[data-theme="light"]{
  --bg:#fafbfc; --panel:#ffffff; --panel-2:#f4f6fa;
  --fg:#0f172a; --fg-2:#52607a; --fg-3:#8a96aa;
  --line:#e6eaf2; --line-2:#d3dae7;
  --accent:#2563eb;
}
html,body{margin:0;padding:0;background:var(--bg);color:var(--fg);
  font:13px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB","Microsoft YaHei",sans-serif;
  -webkit-font-smoothing:antialiased}
a{color:var(--accent);text-decoration:none}
button{font:inherit}

/* top bar */
.topbar{position:sticky;top:0;z-index:10;background:var(--panel);
  border-bottom:1px solid var(--line);padding:14px 22px;
  display:flex;align-items:center;gap:14px;flex-wrap:wrap}
.topbar h1{margin:0;font-size:15px;font-weight:600;letter-spacing:.2px}
.topbar .meta{color:var(--fg-2);font-size:11.5px;display:flex;gap:12px;flex-wrap:wrap;align-items:center}
.topbar .meta dt{display:inline;color:var(--fg-3);margin-right:4px}
.topbar .meta dd{display:inline;margin:0}
.topbar .meta code{background:var(--panel-2);padding:1px 6px;border-radius:4px;font-size:11px}
.topbar .spacer{flex:1}
.icon-btn{background:transparent;color:var(--fg-2);border:1px solid var(--line-2);
  border-radius:6px;padding:5px 9px;cursor:pointer;display:inline-flex;align-items:center;gap:4px;
  transition:color .15s,border-color .15s,background .15s}
.icon-btn:hover{color:var(--fg);border-color:var(--accent);background:var(--panel-2)}
.icon-btn svg{width:14px;height:14px}

/* progress */
.progress{background:var(--panel-2);padding:12px 22px;border-bottom:1px solid var(--line);
  display:flex;align-items:center;gap:16px;flex-wrap:wrap}
.progress .summary{font-size:12px;color:var(--fg-2);white-space:nowrap}
.progress .summary b{color:var(--fg);font-weight:600}
.progress .bar{flex:1;min-width:160px;height:6px;background:var(--panel);
  border:1px solid var(--line);border-radius:6px;overflow:hidden;position:relative}
.progress .bar > span{position:absolute;inset:0;width:var(--p,0%);
  background:linear-gradient(90deg,var(--progress-from),var(--progress-to));border-radius:6px;transition:width .3s}
.stats{display:flex;gap:14px;font-size:11.5px;color:var(--fg-2)}
.stats span b{color:var(--fg);font-weight:600;margin-right:3px}

/* flow */
.flow{padding:18px 22px;border-bottom:1px solid var(--line)}
.section-title{margin:0 0 10px;font-size:11px;font-weight:600;color:var(--fg-3);
  letter-spacing:.7px;text-transform:uppercase}
.flow-wrap{overflow:auto;max-height:380px;
  background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:14px;
  display:flex;justify-content:center;align-items:flex-start}
.flow-svg{display:block;max-width:100%;height:auto;flex:none}
.svg-node{cursor:pointer}
.svg-node:focus{outline:none}
.svg-node:focus rect{stroke-width:2.5}
.svg-node:hover rect{filter:brightness(1.08)}
.node-bg{fill:var(--panel-2);stroke-width:1.5}
.node-num{font:600 11px/1 ui-monospace,SFMono-Regular,Menlo,monospace}
.node-status{font:500 10px/1 -apple-system,system-ui,sans-serif}
.node-title{font:500 12px/1 -apple-system,system-ui,sans-serif;fill:var(--fg)}
.edge{fill:none;stroke:var(--line-2);stroke-width:1.2}
.arrow-head{fill:var(--line-2)}
.svg-empty{padding:28px;text-align:center;color:var(--fg-3);font-size:12.5px}

/* kanban */
main{padding:18px 22px 32px}
.kanban{display:grid;grid-template-columns:repeat(auto-fit,minmax(248px,1fr));gap:12px}
.col{background:var(--panel);border:1px solid var(--line);border-radius:8px;
  display:flex;flex-direction:column;max-height:72vh}
.col > h2{margin:0;padding:11px 14px;font-size:11px;font-weight:600;letter-spacing:.6px;
  text-transform:uppercase;color:var(--fg-2);border-bottom:1px solid var(--line);
  display:flex;align-items:center;justify-content:space-between}
.col-pending .col-name{color:#94a3b8}
.col-in-progress .col-name{color:#3b82f6}
.col-completed .col-name{color:#22c55e}
.col-archived .col-name{color:#64748b}
.count{background:var(--panel-2);padding:1px 8px;border-radius:10px;font-size:10.5px;color:var(--fg-2)}
.col-body{padding:10px;overflow-y:auto;display:flex;flex-direction:column;gap:9px}
.empty{color:var(--fg-3);font-size:11.5px;text-align:center;padding:22px 0}

/* card */
.card{background:var(--panel-2);border:1px solid var(--line);border-radius:7px;
  padding:11px 12px;display:flex;flex-direction:column;gap:7px;
  border-left:3px solid var(--c);transition:border-color .15s,transform .15s}
.card:hover{border-color:var(--accent);border-left-color:var(--c)}
.card.archived{opacity:.55}
.card.archived h3{text-decoration:line-through;text-decoration-color:var(--fg-3)}
.card.flash{box-shadow:0 0 0 2px var(--accent)}
.card header{display:flex;align-items:center;gap:7px;flex-wrap:wrap}
.card h3{margin:0;font-size:13px;font-weight:600;flex:1;min-width:90px;line-height:1.4}
.card .num{font:600 11px/1 ui-monospace,SFMono-Regular,Menlo,monospace;color:var(--c)}
.pill{font-size:10.5px;padding:2px 7px;border-radius:10px;font-weight:600;
  background:color-mix(in srgb,var(--c) 14%,transparent);color:var(--c);
  border:1px solid color-mix(in srgb,var(--c) 28%,transparent)}
.desc{margin:0;color:var(--fg-2);font-size:12.5px;line-height:1.55}
.row{display:flex;align-items:center;gap:5px;flex-wrap:wrap;font-size:11px}
.row-label{color:var(--fg-3)}
.chip{display:inline-block;padding:1px 7px;background:var(--panel);
  border:1px solid var(--line);border-radius:8px;font-size:10.5px;color:var(--fg-2)}
.times{font-size:10.5px;color:var(--fg-3)}
.notes{font-size:11.5px;background:var(--panel);border:1px solid var(--line);border-radius:6px}
.notes summary{cursor:pointer;color:var(--fg-2);padding:6px 10px;list-style:none;font-weight:500}
.notes summary::-webkit-details-marker{display:none}
.notes summary::before{content:"▸";display:inline-block;margin-right:6px;transition:transform .15s;color:var(--fg-3)}
.notes[open] summary::before{transform:rotate(90deg)}
.notes ul{margin:0;padding:0 10px 8px;list-style:none;display:flex;flex-direction:column;gap:5px}
.notes li{display:flex;gap:8px;align-items:flex-start;color:var(--fg-2)}
.notes time{font:500 10px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace;color:var(--fg-3);white-space:nowrap;flex-shrink:0}

footer{padding:16px 22px;text-align:center;color:var(--fg-3);font-size:10.5px;border-top:1px solid var(--line)}
footer code{background:var(--panel-2);padding:1px 5px;border-radius:3px;font-size:10px}

@media (max-width:640px){
  .topbar,.progress,.flow,main,footer{padding-left:14px;padding-right:14px}
  .topbar h1{font-size:14px}
  .col{max-height:none}
}
@media (prefers-reduced-motion:reduce){
  *,*::before,*::after{transition-duration:.01ms !important;animation-duration:.01ms !important}
}
@supports not (color: color-mix(in srgb, red, blue)){
  .pill{background:var(--panel);border-color:var(--line-2)}
}
"""


SUN_SVG = '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><circle cx="8" cy="8" r="2.5"/><path d="M8 1v1.5M8 13.5V15M1 8h1.5M13.5 8H15M3 3l1.1 1.1M11.9 11.9 13 13M3 13l1.1-1.1M11.9 4.1 13 3"/></svg>'
MOON_SVG = '<svg viewBox="0 0 16 16" fill="currentColor"><path d="M6 1a7 7 0 1 0 9 9 5.6 5.6 0 0 1-9-9z"/></svg>'
REFRESH_SVG = '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M14 8a6 6 0 1 1-1.76-4.24"/><path d="M14 2v4h-4"/></svg>'


def render_html(data):
    session = data.get("session") or {}
    tasks = data.get("tasks") or []
    stats = compute_stats(tasks)

    title = escape(session.get("title") or "任务看板")
    project = escape(session.get("project_path") or "")
    started = escape(session.get("started_at") or "")
    updated = escape(session.get("updated_at") or "")

    svg = build_svg(tasks)
    kanban = render_kanban(tasks)

    meta_items = []
    if project: meta_items.append(f'<dt>项目</dt><dd><code>{project}</code></dd>')
    if started: meta_items.append(f'<dt>开始</dt><dd>{started}</dd>')
    if updated: meta_items.append(f'<dt>更新</dt><dd>{updated}</dd>')
    meta_html = ('<dl class="meta">' + "".join(meta_items) + '</dl>') if meta_items else ""

    return f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title} · 任务看板</title>
<style>{CSS}</style>
</head>
<body data-theme="dark">

<header class="topbar">
  <h1>{title}</h1>
  {meta_html}
  <div class="spacer"></div>
  <button id="themeBtn" class="icon-btn" type="button" aria-label="切换主题"><span class="theme-icon">{MOON_SVG}</span></button>
  <button class="icon-btn" type="button" onclick="location.reload()" aria-label="刷新">{REFRESH_SVG}</button>
</header>

<section class="progress" aria-label="进度概览">
  <div class="summary">进度 <b>{stats['done']}/{stats['active']}</b> · <b>{stats['pct']}%</b></div>
  <div class="bar" role="progressbar" aria-valuenow="{stats['pct']}" aria-valuemin="0" aria-valuemax="100"><span style="--p:{stats['pct']}%"></span></div>
  <div class="stats">
    <span><b>{stats['pending']}</b>待办</span>
    <span><b>{stats['in_progress']}</b>进行</span>
    <span><b>{stats['blocked']}</b>受阻</span>
    <span><b>{stats['completed']}</b>完成</span>
    <span><b>{stats['abandoned'] + stats['deleted']}</b>归档</span>
  </div>
</section>

<section class="flow">
  <h2 class="section-title">流程</h2>
  <div class="flow-wrap">{svg}</div>
</section>

<main>
  <h2 class="section-title">看板</h2>
  {kanban}
</main>

<footer>
  由 <code>task-dashboard</code> 生成 · 数据 <code>.claude-tasks/dashboard.json</code> · 手动刷新页面查看最新状态
</footer>

<script>
(function(){{
  const sun = `{SUN_SVG}`, moon = `{MOON_SVG}`;
  const btn = document.getElementById('themeBtn');
  const icon = btn.querySelector('.theme-icon');
  const saved = localStorage.getItem('task-dashboard-theme') || 'dark';
  apply(saved);
  btn.addEventListener('click', () => apply(document.body.dataset.theme === 'dark' ? 'light' : 'dark'));
  function apply(t) {{
    document.body.dataset.theme = t;
    localStorage.setItem('task-dashboard-theme', t);
    icon.innerHTML = t === 'dark' ? moon : sun;
  }}

  document.querySelectorAll('.svg-node').forEach(n => {{
    const id = n.dataset.taskId;
    n.addEventListener('click', () => focusCard(id));
    n.addEventListener('keydown', (e) => {{
      if (e.key === 'Enter' || e.key === ' ') {{ e.preventDefault(); focusCard(id); }}
    }});
  }});
  function focusCard(id) {{
    const card = document.querySelector('.card[data-task-id="' + CSS.escape(id) + '"]');
    if (!card) return;
    card.scrollIntoView({{behavior:'smooth', block:'center'}});
    card.classList.add('flash');
    setTimeout(() => card.classList.remove('flash'), 1100);
  }}
}})();
</script>
</body>
</html>
'''


def main():
    if len(sys.argv) < 3:
        print("Usage: render.py <input.json> <output.html>", file=sys.stderr)
        sys.exit(2)
    src, dst = Path(sys.argv[1]), Path(sys.argv[2])
    if not src.exists():
        print(f"Input not found: {src}", file=sys.stderr)
        sys.exit(2)
    try:
        data = json.loads(src.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"Invalid JSON in {src}: {e}", file=sys.stderr)
        sys.exit(2)
    if not isinstance(data, dict):
        print(f"Top-level JSON must be an object, got {type(data).__name__}", file=sys.stderr)
        sys.exit(2)
    data.setdefault("session", {}).setdefault(
        "updated_at",
        datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    )
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(render_html(data), encoding="utf-8")
    print(f"Rendered → {dst}")


if __name__ == "__main__":
    main()
