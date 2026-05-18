#!/usr/bin/env python3
"""Render a task dashboard JSON file to a self-contained HTML dashboard.

Usage:
    python3 render.py <input.json> <output.html> [--open]

The output is a single HTML file with inlined CSS, JS, and data. Open it
directly in a browser; no server required. Pass --open to additionally
launch the file in the default browser after rendering. See
references/schema.md for the expected input shape.

The renderer adapts to task count:
  - up to 15 tasks: full-size flow nodes, expanded feed cards
  - 16-50: smaller flow nodes, feed cards collapsed by default
  - 51+: heatmap-style flow nodes (status-colored squares with id only),
         feed cards collapsed
A search box, status chips, and tag click-to-filter live at the top.
"""
from __future__ import annotations

import json
import math
import sys
import webbrowser
from collections import defaultdict
from datetime import datetime, timezone
from html import escape
from pathlib import Path

STATUS_META = {
    "pending":     {"label": "待办",   "color": "#94a3b8", "icon": "○", "track": "queue"},
    "in_progress": {"label": "进行中", "color": "#3b82f6", "icon": "◐", "track": "feed"},
    "blocked":     {"label": "受阻",   "color": "#f59e0b", "icon": "⏸", "track": "feed"},
    "completed":   {"label": "已完成", "color": "#22c55e", "icon": "✓", "track": "feed"},
    "abandoned":   {"label": "已放弃", "color": "#64748b", "icon": "⊘", "track": "archive"},
    "deleted":     {"label": "已删除", "color": "#64748b", "icon": "✕", "track": "archive"},
}

PHASE_META = {
    "plan":     {"label": "规划",   "color": "#8b5cf6"},
    "execute":  {"label": "执行",   "color": "#06b6d4"},
    "verify":   {"label": "验证",   "color": "#10b981"},
    "followup": {"label": "后续",   "color": "#f97316"},
}

# Three SVG density presets, picked by total task count.
SVG_PRESETS = {
    "big": {
        "node_w": 168, "node_h": 38,
        "gap_x": 16, "gap_y_intra": 14, "gap_y_layer": 36,
        "title_units": 22, "show_status": True, "show_title": True, "fill_status": False,
    },
    "normal": {
        "node_w": 124, "node_h": 30,
        "gap_x": 12, "gap_y_intra": 10, "gap_y_layer": 28,
        "title_units": 14, "show_status": False, "show_title": True, "fill_status": False,
    },
    "compact": {
        "node_w": 56, "node_h": 24,
        "gap_x": 8, "gap_y_intra": 8, "gap_y_layer": 20,
        "title_units": 0, "show_status": False, "show_title": False, "fill_status": True,
    },
}

SVG_PAD = 18
SVG_MAX_W = 1100
FANOUT_BUS_THRESHOLD = 5

DEFAULT_COMPACT_THRESHOLD = 20  # feed cards collapse by default when total tasks exceed this


def pick_svg_preset(total):
    if total <= 15:
        return SVG_PRESETS["big"]
    if total <= 50:
        return SVG_PRESETS["normal"]
    return SVG_PRESETS["compact"]


# ---------- layout ----------

def topo_layers(tasks):
    by_id = {t["id"]: t for t in tasks}
    incoming = {t["id"]: [d for d in (t.get("depends_on") or []) if d in by_id]
                for t in tasks}
    layers, placed, remaining = [], set(), set(by_id)
    while remaining:
        layer = [tid for tid in remaining if all(d in placed for d in incoming[tid])]
        if not layer:
            layer = list(remaining)
        layer.sort(key=lambda x: by_id[x].get("number") or 0)
        layers.append(layer)
        placed.update(layer)
        remaining.difference_update(layer)
    return layers


def layout(tasks, preset):
    layers = topo_layers(tasks)
    if not layers:
        return {}, SVG_MAX_W, 80, {}

    node_w, node_h = preset["node_w"], preset["node_h"]
    gap_x = preset["gap_x"]
    gap_intra = preset["gap_y_intra"]
    gap_layer = preset["gap_y_layer"]

    inner_w = SVG_MAX_W - SVG_PAD * 2
    max_cols = max(1, (inner_w + gap_x) // (node_w + gap_x))

    positions = {}
    y = SVG_PAD
    for li, layer in enumerate(layers):
        rows = math.ceil(len(layer) / max_cols)
        for r in range(rows):
            chunk = layer[r * max_cols: (r + 1) * max_cols]
            row_w = len(chunk) * (node_w + gap_x) - gap_x
            start_x = SVG_PAD + (inner_w - row_w) / 2
            for c, tid in enumerate(chunk):
                positions[tid] = (start_x + c * (node_w + gap_x), y)
            y += node_h
            if r < rows - 1:
                y += gap_intra
        if li < len(layers) - 1:
            y += gap_layer

    height = y + SVG_PAD
    parent_to_children = defaultdict(list)
    for t in tasks:
        for dep in t.get("depends_on") or []:
            if dep in positions:
                parent_to_children[dep].append(t["id"])
    return positions, SVG_MAX_W, height, parent_to_children


# ---------- svg ----------

def truncate_label(text, max_units):
    if max_units <= 0:
        return ""
    out, w = [], 0
    for ch in text:
        cw = 2 if ord(ch) > 127 else 1
        if w + cw > max_units:
            return "".join(out) + "…"
        out.append(ch); w += cw
    return text


def build_svg(tasks):
    visible = [t for t in tasks if t.get("status") not in ("deleted", "abandoned")]
    if not visible:
        return '<div class="svg-empty">暂无活跃任务</div>'

    preset = pick_svg_preset(len(visible))
    positions, width, height, parent_to_children = layout(visible, preset)
    node_w, node_h = preset["node_w"], preset["node_h"]
    gap_layer = preset["gap_y_layer"]

    edges_svg = []
    for parent, children in parent_to_children.items():
        px, py = positions[parent]
        px_c = px + node_w / 2
        py_b = py + node_h

        if len(children) < FANOUT_BUS_THRESHOLD:
            for child in children:
                cx, cy = positions[child]
                cx_c = cx + node_w / 2
                mid_y = (py_b + cy) / 2
                edges_svg.append(
                    f'<path d="M{px_c:.1f},{py_b:.1f} '
                    f'C{px_c:.1f},{mid_y:.1f} {cx_c:.1f},{mid_y:.1f} {cx_c:.1f},{cy:.1f}" '
                    f'class="edge"/>'
                )
        else:
            child_centers = sorted(positions[c][0] + node_w / 2 for c in children)
            bus_x_min, bus_x_max = child_centers[0], child_centers[-1]
            bus_x_mid = (bus_x_min + bus_x_max) / 2
            bus_y = py_b + gap_layer * 0.45
            edges_svg.append(
                f'<path d="M{px_c:.1f},{py_b:.1f} L{px_c:.1f},{bus_y:.1f} L{bus_x_mid:.1f},{bus_y:.1f}" '
                f'class="edge"/>'
            )
            edges_svg.append(
                f'<path d="M{bus_x_min:.1f},{bus_y:.1f} L{bus_x_max:.1f},{bus_y:.1f}" class="edge bus"/>'
            )
            for child in children:
                cx, cy = positions[child]
                cx_c = cx + node_w / 2
                edges_svg.append(
                    f'<path d="M{cx_c:.1f},{bus_y:.1f} L{cx_c:.1f},{cy:.1f}" class="edge"/>'
                )

    nodes_svg = []
    for t in visible:
        x, y = positions[t["id"]]
        meta = STATUS_META.get(t.get("status", "pending"), STATUS_META["pending"])
        color = meta["color"]
        num = escape(str(t.get("number", t.get("id", "?"))))
        full_title = (t.get("title") or "").strip()
        title = escape(truncate_label(full_title, preset["title_units"]))
        tooltip = escape(f"#{num} {full_title} · {meta['label']}")

        if preset["fill_status"]:
            # compact heatmap-style: rect filled with status color, white id
            nodes_svg.append(f'''
<g class="svg-node compact" data-task-id="{escape(t["id"])}" tabindex="0" role="button">
  <title>{tooltip}</title>
  <rect x="{x:.1f}" y="{y:.1f}" rx="4" width="{node_w}" height="{node_h}" fill="{color}" class="node-bg-fill"/>
  <text x="{x + node_w / 2:.1f}" y="{y + node_h / 2 + 4:.1f}" class="node-num-light" text-anchor="middle">#{num}</text>
</g>''')
        elif not preset["show_status"]:
            # normal: title left, status icon on the right
            nodes_svg.append(f'''
<g class="svg-node" data-task-id="{escape(t["id"])}" tabindex="0" role="button">
  <title>{tooltip}</title>
  <rect x="{x:.1f}" y="{y:.1f}" rx="5" width="{node_w}" height="{node_h}" class="node-bg" stroke="{color}"/>
  <text x="{x + 8:.1f}" y="{y + node_h / 2 + 4:.1f}" fill="{color}" class="node-num">#{num}</text>
  <text x="{x + 30:.1f}" y="{y + node_h / 2 + 4:.1f}" class="node-title-sm">{title}</text>
</g>''')
        else:
            # big: id+status on top, title below
            nodes_svg.append(f'''
<g class="svg-node" data-task-id="{escape(t["id"])}" tabindex="0" role="button" aria-label="{tooltip}">
  <title>{tooltip}</title>
  <rect x="{x:.1f}" y="{y:.1f}" rx="5" width="{node_w}" height="{node_h}" class="node-bg" stroke="{color}"/>
  <text x="{x + 11:.1f}" y="{y + 15:.1f}" fill="{color}" class="node-num">#{num}</text>
  <text x="{x + node_w - 11:.1f}" y="{y + 15:.1f}" fill="{color}" class="node-status" text-anchor="end">{escape(meta['label'])}</text>
  <text x="{x + 11:.1f}" y="{y + 29:.1f}" class="node-title">{title}</text>
</g>''')

    return f'''
<svg width="{int(width)}" height="{int(height)}" viewBox="0 0 {int(width)} {int(height)}"
     xmlns="http://www.w3.org/2000/svg" class="flow-svg" role="img" aria-label="任务流程图">
  {''.join(edges_svg)}
  {''.join(nodes_svg)}
</svg>'''


# ---------- cards ----------

def task_search_text(t):
    """Lowercased haystack for the search box."""
    bits = [
        str(t.get("number") or ""),
        str(t.get("id") or ""),
        (t.get("title") or ""),
        (t.get("description") or ""),
        " ".join(t.get("tags") or []),
    ]
    return " ".join(bits).lower()


def render_task_card(t, default_compact=False):
    meta = STATUS_META.get(t.get("status", "pending"), STATUS_META["pending"])
    color = meta["color"]
    archived = t.get("status") in ("abandoned", "deleted")

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
        chips = "".join(
            f'<button type="button" class="chip tag-chip" data-tag="{escape(str(g))}">{escape(str(g))}</button>'
            for g in tags
        )
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

    classes = ["card"]
    if archived:        classes.append("archived")
    if default_compact: classes.append("compact")

    return f'''
<article class="{' '.join(classes)}" data-task-id="{escape(t["id"])}"
         data-status="{escape(t.get("status", "pending"))}"
         data-phase="{escape(t.get("phase") or "")}"
         data-tags="{escape(','.join(tags))}"
         data-search="{escape(task_search_text(t))}"
         style="--c:{color}">
  <header>
    <button type="button" class="toggle" aria-label="展开/收起">▸</button>
    <span class="num">#{escape(str(t.get("number", t["id"])))}</span>
    <h3>{escape(t.get("title", "(无标题)"))}</h3>
    <span class="pill status" style="--c:{color}">{meta['icon']} {escape(meta['label'])}</span>
    {phase_html}
  </header>
  <div class="card-body">
    {desc_html}{deps_html}{tags_html}{times_html}{notes_html}
  </div>
</article>'''


def feed_sort_key(t):
    status = t.get("status")
    bucket = 1 if status in ("in_progress", "blocked") else 0
    last_activity = t.get("completed_at") or t.get("started_at") or ""
    return (bucket, last_activity, t.get("number") or 0)


def render_board(tasks, default_compact):
    feed, queue, archive = [], [], []
    for t in tasks:
        meta = STATUS_META.get(t.get("status", "pending"), STATUS_META["pending"])
        if meta["track"] == "feed":      feed.append(t)
        elif meta["track"] == "queue":   queue.append(t)
        else:                            archive.append(t)

    feed.sort(key=feed_sort_key, reverse=True)
    queue.sort(key=lambda t: t.get("number") or 0)
    archive.sort(key=lambda t: t.get("number") or 0, reverse=True)

    feed_cards = "".join(render_task_card(t, default_compact) for t in feed) \
        if feed else '<div class="empty" data-empty="feed">暂无进展</div>'
    queue_cards = "".join(render_task_card(t, default_compact) for t in queue)
    archive_cards = "".join(render_task_card(t, default_compact=True) for t in archive)

    has_queue = bool(queue)
    has_archive = bool(archive)

    queue_html = (
        f'<aside class="queue" data-track="queue"><h2 class="section-title">'
        f'<span class="col-name">队列</span><span class="count" data-count="queue">{len(queue)}</span>'
        f'</h2><div class="track-body">{queue_cards}'
        f'<div class="empty hidden" data-empty="queue">无匹配结果</div></div></aside>'
    ) if has_queue else ""

    archive_html = (
        f'<details class="archive" data-track="archive"{(" open" if len(archive) <= 3 else "")}>'
        f'<summary>已归档 · <span data-count="archive">{len(archive)}</span></summary>'
        f'<div class="archive-body">{archive_cards}'
        f'<div class="empty hidden" data-empty="archive">无匹配结果</div></div></details>'
    ) if has_archive else ""

    board_cls = "board" + ("" if has_queue else " no-queue")
    return f'''
<div class="{board_cls}">
  <section class="feed" data-track="feed"><h2 class="section-title">
    <span class="col-name">进展</span><span class="count" data-count="feed">{len(feed)}</span>
  </h2><div class="track-body">{feed_cards}
    <div class="empty hidden" data-empty="feed-filtered">无匹配结果</div>
  </div></section>
  {queue_html}
</div>
{archive_html}'''


# ---------- toolbar ----------

def render_toolbar(tasks):
    counts = defaultdict(int)
    for t in tasks:
        counts[t.get("status") or "pending"] += 1

    status_chips = []
    for key in ("in_progress", "blocked", "pending", "completed", "abandoned", "deleted"):
        if counts[key] == 0:
            continue
        meta = STATUS_META[key]
        status_chips.append(
            f'<button type="button" class="status-chip active" data-status="{key}" '
            f'style="--c:{meta["color"]}" aria-pressed="true">'
            f'<span class="ch-icon">{meta["icon"]}</span>'
            f'<span class="ch-label">{escape(meta["label"])}</span>'
            f'<span class="ch-count">{counts[key]}</span>'
            f'</button>'
        )

    return f'''
<section class="toolbar" aria-label="筛选">
  <div class="search-wrap">
    <svg class="search-icon" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
      <circle cx="7" cy="7" r="5"/><path d="M11 11l3 3"/>
    </svg>
    <input id="searchInput" type="search" placeholder="搜索任务（标题、描述、标签、编号）…" aria-label="搜索任务">
  </div>
  <div class="status-chips">{''.join(status_chips)}</div>
  <div class="active-tags" id="activeTags" hidden></div>
  <button type="button" class="clear-filters" id="clearFilters" hidden>清除筛选</button>
</section>'''


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
button{font:inherit}
.hidden{display:none !important}

/* topbar */
.topbar{position:sticky;top:0;z-index:10;background:var(--panel);
  border-bottom:1px solid var(--line);padding:14px 22px;
  display:flex;align-items:center;gap:14px;flex-wrap:wrap}
.topbar h1{margin:0;font-size:15px;font-weight:600;letter-spacing:.2px}
.topbar .meta{color:var(--fg-2);font-size:11.5px;display:flex;gap:12px;flex-wrap:wrap;align-items:center;margin:0;padding:0}
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

/* toolbar */
.toolbar{background:var(--panel);padding:10px 22px;border-bottom:1px solid var(--line);
  display:flex;align-items:center;gap:10px;flex-wrap:wrap}
.search-wrap{position:relative;flex:1;min-width:220px;max-width:400px;display:flex;align-items:center}
.search-icon{position:absolute;left:9px;width:13px;height:13px;color:var(--fg-3);pointer-events:none}
.search-wrap input{width:100%;background:var(--panel-2);color:var(--fg);
  border:1px solid var(--line-2);border-radius:6px;padding:6px 10px 6px 28px;font-size:12px;
  outline:none;transition:border-color .15s}
.search-wrap input:focus{border-color:var(--accent)}
.search-wrap input::-webkit-search-cancel-button{filter:invert(.4)}
.status-chips{display:flex;gap:5px;flex-wrap:wrap}
.status-chip{display:inline-flex;align-items:center;gap:5px;background:transparent;
  border:1px solid var(--line-2);border-radius:6px;padding:4px 8px;font-size:11.5px;
  color:var(--fg-3);cursor:pointer;transition:all .12s}
.status-chip .ch-count{background:var(--panel-2);padding:0 5px;border-radius:8px;font-size:10.5px;color:var(--fg-3)}
.status-chip.active{color:var(--c);border-color:color-mix(in srgb,var(--c) 50%,transparent);
  background:color-mix(in srgb,var(--c) 10%,transparent)}
.status-chip.active .ch-count{background:color-mix(in srgb,var(--c) 18%,transparent);color:var(--c)}
.status-chip:hover{border-color:var(--c, var(--accent))}
.active-tags{display:flex;gap:4px;flex-wrap:wrap;align-items:center}
.active-tags::before{content:"标签";color:var(--fg-3);font-size:11px;margin-right:4px}
.active-tag{display:inline-flex;align-items:center;gap:4px;background:var(--panel-2);
  border:1px solid var(--line-2);border-radius:10px;padding:2px 7px;font-size:10.5px;color:var(--fg-2);
  cursor:pointer}
.active-tag::after{content:"✕";color:var(--fg-3);font-size:10px;margin-left:1px}
.active-tag:hover{border-color:var(--accent);color:var(--fg)}
.clear-filters{background:transparent;color:var(--fg-3);border:0;font-size:11px;
  text-decoration:underline;cursor:pointer;padding:4px 6px}
.clear-filters:hover{color:var(--fg)}

/* flow */
.flow{padding:18px 22px;border-bottom:1px solid var(--line)}
.section-title{margin:0 0 10px;font-size:11px;font-weight:600;color:var(--fg-3);
  letter-spacing:.7px;text-transform:uppercase;display:flex;align-items:center;justify-content:space-between}
.flow-wrap{overflow:auto;max-height:480px;
  background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:6px 14px;
  display:flex;justify-content:center;align-items:flex-start}
.flow-svg{display:block;max-width:100%;height:auto;flex:none}
.svg-node{cursor:pointer}
.svg-node:focus{outline:none}
.svg-node:focus rect{stroke-width:2.5}
.svg-node:hover rect{filter:brightness(1.08)}
.svg-node.hidden{display:none}
.svg-node.compact rect{stroke-width:0}
.svg-node.compact:hover rect{filter:brightness(1.12)}
.node-bg{fill:var(--panel-2);stroke-width:1.5}
.node-bg-fill{}
.node-num{font:600 11px/1 ui-monospace,SFMono-Regular,Menlo,monospace}
.node-num-light{font:600 11px/1 ui-monospace,SFMono-Regular,Menlo,monospace;fill:#fff}
.node-status{font:500 10px/1 -apple-system,system-ui,sans-serif}
.node-title{font:500 12px/1 -apple-system,system-ui,sans-serif;fill:var(--fg)}
.node-title-sm{font:500 11px/1 -apple-system,system-ui,sans-serif;fill:var(--fg)}
.edge{fill:none;stroke:var(--line-2);stroke-width:1.2}
.edge.bus{stroke-width:1.6;stroke-linecap:round}
.svg-empty{padding:28px;text-align:center;color:var(--fg-3);font-size:12.5px}

/* main board */
main{padding:18px 22px 32px}
.board{display:grid;grid-template-columns:minmax(0,2fr) minmax(0,1fr);gap:14px;align-items:start}
.board.no-queue{grid-template-columns:1fr}
.feed,.queue{background:var(--panel);border:1px solid var(--line);border-radius:8px;
  display:flex;flex-direction:column}
.feed > .section-title,.queue > .section-title{margin:0;padding:11px 14px;
  border-bottom:1px solid var(--line);color:var(--fg-2)}
.col-name{color:var(--fg-2);font-weight:600;letter-spacing:.6px}
.feed .col-name{color:#3b82f6}
.queue .col-name{color:#94a3b8}
.count{background:var(--panel-2);padding:1px 8px;border-radius:10px;font-size:10.5px;color:var(--fg-2);
  text-transform:none;letter-spacing:0}
.track-body{padding:10px;display:flex;flex-direction:column;gap:9px}
.empty{color:var(--fg-3);font-size:11.5px;text-align:center;padding:22px 0}

/* archive */
.archive{margin-top:14px;background:var(--panel);border:1px solid var(--line);border-radius:8px}
.archive > summary{padding:11px 14px;cursor:pointer;font-size:11px;font-weight:600;
  letter-spacing:.6px;text-transform:uppercase;color:var(--fg-3);list-style:none;
  display:flex;align-items:center;gap:8px}
.archive > summary::-webkit-details-marker{display:none}
.archive > summary::before{content:"▸";color:var(--fg-3);transition:transform .15s}
.archive[open] > summary::before{transform:rotate(90deg)}
.archive-body{padding:10px 14px 14px;display:grid;grid-template-columns:repeat(auto-fill,minmax(248px,1fr));gap:10px}

/* card */
.card{background:var(--panel-2);border:1px solid var(--line);border-radius:7px;
  padding:11px 12px;display:flex;flex-direction:column;gap:7px;
  border-left:3px solid var(--c);transition:border-color .15s,transform .15s}
.card:hover{border-color:var(--accent);border-left-color:var(--c)}
.card.archived{opacity:.55}
.card.archived h3{text-decoration:line-through;text-decoration-color:var(--fg-3)}
.card.flash{box-shadow:0 0 0 2px var(--accent)}
.card.hidden{display:none}
.card header{display:flex;align-items:center;gap:7px;flex-wrap:wrap;cursor:default}
.card h3{margin:0;font-size:13px;font-weight:600;flex:1;min-width:90px;line-height:1.4}
.card .num{font:600 11px/1 ui-monospace,SFMono-Regular,Menlo,monospace;color:var(--c)}
.toggle{background:transparent;border:0;color:var(--fg-3);cursor:pointer;padding:0;
  width:16px;height:16px;display:inline-flex;align-items:center;justify-content:center;
  font-size:10px;line-height:1;transition:transform .15s,color .15s}
.toggle:hover{color:var(--fg)}
.card:not(.compact) .toggle{transform:rotate(90deg)}
.card.compact .card-body{display:none}
.card.compact{padding:8px 12px;gap:0}
.pill{font-size:10.5px;padding:2px 7px;border-radius:10px;font-weight:600;
  background:color-mix(in srgb,var(--c) 14%,transparent);color:var(--c);
  border:1px solid color-mix(in srgb,var(--c) 28%,transparent)}
.desc{margin:0;color:var(--fg-2);font-size:12.5px;line-height:1.55}
.card-body{display:flex;flex-direction:column;gap:7px}
.row{display:flex;align-items:center;gap:5px;flex-wrap:wrap;font-size:11px}
.row-label{color:var(--fg-3)}
.chip{display:inline-block;padding:1px 7px;background:var(--panel);
  border:1px solid var(--line);border-radius:8px;font-size:10.5px;color:var(--fg-2)}
button.chip{font:inherit;cursor:pointer;transition:border-color .12s,color .12s,background .12s}
button.chip:hover{border-color:var(--accent);color:var(--fg)}
button.chip.active{background:color-mix(in srgb,var(--accent) 18%,transparent);
  border-color:var(--accent);color:var(--accent)}
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

@media (max-width:780px){
  .board{grid-template-columns:1fr}
  .archive-body{grid-template-columns:1fr}
  .topbar,.progress,.toolbar,.flow,main,footer{padding-left:14px;padding-right:14px}
  .search-wrap{max-width:none}
}
@media (prefers-reduced-motion:reduce){
  *,*::before,*::after{transition-duration:.01ms !important;animation-duration:.01ms !important}
}
@supports not (color: color-mix(in srgb, red, blue)){
  .pill,.status-chip.active,.status-chip.active .ch-count{background:var(--panel);border-color:var(--line-2)}
  button.chip.active{background:var(--panel)}
}
"""

SUN_SVG = '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><circle cx="8" cy="8" r="2.5"/><path d="M8 1v1.5M8 13.5V15M1 8h1.5M13.5 8H15M3 3l1.1 1.1M11.9 11.9 13 13M3 13l1.1-1.1M11.9 4.1 13 3"/></svg>'
MOON_SVG = '<svg viewBox="0 0 16 16" fill="currentColor"><path d="M6 1a7 7 0 1 0 9 9 5.6 5.6 0 0 1-9-9z"/></svg>'
REFRESH_SVG = '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M14 8a6 6 0 1 1-1.76-4.24"/><path d="M14 2v4h-4"/></svg>'


def render_html(data):
    session = data.get("session") or {}
    tasks = data.get("tasks") or []
    stats = compute_stats(tasks)
    total = len(tasks)
    default_compact = total > DEFAULT_COMPACT_THRESHOLD

    title = escape(session.get("title") or "任务看板")
    project = escape(session.get("project_path") or "")
    started = escape(session.get("started_at") or "")
    updated = escape(session.get("updated_at") or "")

    svg = build_svg(tasks)
    toolbar = render_toolbar(tasks)
    board = render_board(tasks, default_compact)

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

{toolbar}

<section class="flow">
  <h2 class="section-title"><span>流程</span></h2>
  <div class="flow-wrap">{svg}</div>
</section>

<main>{board}</main>

<footer>
  由 <code>task-dashboard</code> 生成 · 数据 <code>.claude-tasks/dashboard.json</code> · 手动刷新页面查看最新状态
</footer>

<script>
(function(){{
  // ---------- theme ----------
  const sun = `{SUN_SVG}`, moon = `{MOON_SVG}`;
  const btn = document.getElementById('themeBtn');
  const icon = btn.querySelector('.theme-icon');
  const saved = localStorage.getItem('task-dashboard-theme') || 'dark';
  applyTheme(saved);
  btn.addEventListener('click', () => applyTheme(document.body.dataset.theme === 'dark' ? 'light' : 'dark'));
  function applyTheme(t) {{
    document.body.dataset.theme = t;
    localStorage.setItem('task-dashboard-theme', t);
    icon.innerHTML = t === 'dark' ? moon : sun;
  }}

  // ---------- filter state ----------
  const allStatuses = Array.from(document.querySelectorAll('.status-chip'))
    .map(b => b.dataset.status);
  const state = {{
    q: '',
    statuses: new Set(allStatuses),
    tags: new Set(),
  }};
  const searchInput = document.getElementById('searchInput');
  const activeTagsBox = document.getElementById('activeTags');
  const clearBtn = document.getElementById('clearFilters');

  // ---------- URL hash ----------
  function loadHash() {{
    const h = location.hash.replace(/^#/, '');
    if (!h) return;
    const params = new URLSearchParams(h);
    if (params.get('q')) {{ state.q = params.get('q'); searchInput.value = state.q; }}
    if (params.get('status')) {{
      const set = new Set(params.get('status').split(',').filter(Boolean));
      state.statuses = new Set(allStatuses.filter(s => set.has(s)));
      document.querySelectorAll('.status-chip').forEach(c => {{
        const on = state.statuses.has(c.dataset.status);
        c.classList.toggle('active', on);
        c.setAttribute('aria-pressed', on);
      }});
    }}
    if (params.get('tags')) {{
      state.tags = new Set(params.get('tags').split(',').filter(Boolean));
    }}
  }}
  function syncHash() {{
    const params = new URLSearchParams();
    if (state.q) params.set('q', state.q);
    if (state.statuses.size !== allStatuses.length)
      params.set('status', [...state.statuses].join(','));
    if (state.tags.size) params.set('tags', [...state.tags].join(','));
    const h = params.toString();
    history.replaceState(null, '', h ? '#' + h : location.pathname + location.search);
  }}

  // ---------- apply ----------
  function apply() {{
    const q = state.q.toLowerCase().trim();
    const trackVisible = {{ feed: 0, queue: 0, archive: 0 }};
    document.querySelectorAll('.card').forEach(card => {{
      const status = card.dataset.status;
      const tags = (card.dataset.tags || '').split(',').filter(Boolean);
      const search = card.dataset.search || '';
      const ok = state.statuses.has(status)
        && (!state.tags.size || tags.some(t => state.tags.has(t)))
        && (!q || search.includes(q));
      card.classList.toggle('hidden', !ok);
      if (ok) {{
        const trackEl = card.closest('[data-track]');
        if (trackEl) trackVisible[trackEl.dataset.track]++;
      }}
    }});

    // sync svg nodes
    document.querySelectorAll('.svg-node').forEach(n => {{
      const id = n.dataset.taskId;
      const card = document.querySelector('.card[data-task-id="' + CSS.escape(id) + '"]');
      n.classList.toggle('hidden', !card || card.classList.contains('hidden'));
    }});

    // update counts + empty states per track
    Object.entries(trackVisible).forEach(([track, n]) => {{
      const countEl = document.querySelector(`[data-count="${{track}}"]`);
      if (countEl) countEl.textContent = n;
      const emptyEl = document.querySelector(`[data-empty="${{track}}-filtered"], [data-empty="${{track}}"]`);
      if (emptyEl) emptyEl.classList.toggle('hidden', n > 0);
    }});

    // active tag chips
    activeTagsBox.innerHTML = '';
    state.tags.forEach(t => {{
      const el = document.createElement('button');
      el.type = 'button';
      el.className = 'active-tag';
      el.textContent = t;
      el.addEventListener('click', () => {{
        state.tags.delete(t);
        document.querySelectorAll(`.tag-chip[data-tag="${{CSS.escape(t)}}"]`)
          .forEach(c => c.classList.remove('active'));
        apply();
      }});
      activeTagsBox.appendChild(el);
    }});
    activeTagsBox.hidden = state.tags.size === 0;

    // clear button visibility
    const dirty = state.q || state.statuses.size !== allStatuses.length || state.tags.size > 0;
    clearBtn.hidden = !dirty;

    syncHash();
  }}

  // ---------- chip events ----------
  document.querySelectorAll('.status-chip').forEach(chip => {{
    chip.addEventListener('click', () => {{
      const s = chip.dataset.status;
      if (state.statuses.has(s)) state.statuses.delete(s); else state.statuses.add(s);
      chip.classList.toggle('active', state.statuses.has(s));
      chip.setAttribute('aria-pressed', state.statuses.has(s));
      apply();
    }});
  }});
  document.querySelectorAll('.tag-chip').forEach(chip => {{
    chip.addEventListener('click', (e) => {{
      e.stopPropagation();
      const t = chip.dataset.tag;
      if (state.tags.has(t)) state.tags.delete(t); else state.tags.add(t);
      document.querySelectorAll(`.tag-chip[data-tag="${{CSS.escape(t)}}"]`)
        .forEach(c => c.classList.toggle('active', state.tags.has(t)));
      apply();
    }});
  }});

  // ---------- search ----------
  let searchTimer;
  searchInput.addEventListener('input', () => {{
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => {{ state.q = searchInput.value; apply(); }}, 80);
  }});

  // ---------- clear ----------
  clearBtn.addEventListener('click', () => {{
    state.q = '';
    searchInput.value = '';
    state.statuses = new Set(allStatuses);
    state.tags = new Set();
    document.querySelectorAll('.status-chip').forEach(c => {{
      c.classList.add('active');
      c.setAttribute('aria-pressed', 'true');
    }});
    document.querySelectorAll('.tag-chip.active').forEach(c => c.classList.remove('active'));
    apply();
  }});

  // ---------- card expand/collapse ----------
  document.querySelectorAll('.card .toggle').forEach(t => {{
    t.addEventListener('click', (e) => {{
      e.stopPropagation();
      t.closest('.card').classList.toggle('compact');
    }});
  }});

  // ---------- svg node click → focus card ----------
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
    card.classList.remove('compact');  // expand on focus
    const a = card.closest('details');
    if (a) a.open = true;
    card.scrollIntoView({{behavior:'smooth', block:'center'}});
    card.classList.add('flash');
    setTimeout(() => card.classList.remove('flash'), 1100);
  }}

  // initial
  loadHash();
  apply();
}})();
</script>
</body>
</html>
'''


def main():
    argv = sys.argv[1:]
    auto_open = False
    if "--open" in argv:
        auto_open = True
        argv = [a for a in argv if a != "--open"]
    if len(argv) < 2:
        print("Usage: render.py <input.json> <output.html> [--open]", file=sys.stderr)
        sys.exit(2)
    src, dst = Path(argv[0]), Path(argv[1])
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
    if auto_open:
        try:
            webbrowser.open(f"file://{dst.resolve()}")
            print(f"Opened in browser")
        except Exception as e:
            print(f"(could not auto-open browser: {e})", file=sys.stderr)


if __name__ == "__main__":
    main()
