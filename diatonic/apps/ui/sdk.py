"""What a small model needs to write a panel that works: the few things `diatonic` offers, and three small panels to copy.

The text goes into the add_panel tool description, so every word costs context on each build request. Keep it short, and
keep every template correct: tests/test_ui.py syntax-checks them, and the build eval runs what the model makes from them.
"""
from __future__ import annotations

SDK = (
    "In the code: `el` is the empty panel. diatonic.h(tag, {attrs, onclick}, ...children) builds an element "
    "(h('button', {class: 'btn', onclick: fn}, 'Go')); append with el.append(...). Style with style attrs; text input: "
    "h('input', {type: 'text'}). diatonic.every(ms, fn) repeats; ctx.state.get(key, default) and ctx.state.set(key, value) keep data between visits "
    "(use them, not localStorage). Do not use fetch. Always await server calls. The user's own data: const todos = await ctx.tool('todos_list_todos', {}) (any app's read-only tool: <app>_<tool>, e.g. gcal_list_events, notes_list_notes); only trackers made with create_app use await ctx.data('app-id').get() or .add(list, {field: value}). Write plain JavaScript, no imports.")

TEMPLATES = [
    ("a counter or timer",
     "let n = 0;\n"
     "const out = diatonic.h('div', {style: 'font-size:32px;margin:12px 0'}, '0');\n"
     "const btn = diatonic.h('button', {class: 'btn primary', onclick: () => { n += 1; out.textContent = String(n); }}, '+1');\n"
     "el.append(out, btn);"),
    ("something that works out a result as you type",
     "const inp = diatonic.h('input', {type: 'text', placeholder: 'kilometres'});\n"
     "const out = diatonic.h('div', {style: 'margin-top:10px'}, '0 miles');\n"
     "inp.addEventListener('input', () => { out.textContent = ((parseFloat(inp.value) || 0) * 0.621371).toFixed(2) + ' miles'; });\n"
     "el.append(inp, out);"),
    ("a list you add to and keep",
     "const items = ctx.state.get('items', []);\n"
     "const list = diatonic.h('div');\n"
     "const inp = diatonic.h('input', {type: 'text', placeholder: 'Add an item'});\n"
     "const paint = () => list.replaceChildren(...items.map((t) => diatonic.h('div', {style: 'padding:6px 0'}, t)));\n"
     "inp.addEventListener('keydown', (e) => { if (e.key === 'Enter' && inp.value.trim()) { items.push(inp.value.trim()); ctx.state.set('items', items); inp.value = ''; paint(); } });\n"
     "paint(); el.append(inp, list);"),
]


def examples() -> str:
    return " Examples of the code argument, by what is asked for: " + " ".join(f"({i}) {what}: {code}" for i, (what, code) in enumerate(TEMPLATES, 1))
