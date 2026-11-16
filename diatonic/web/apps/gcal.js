// Calendar: a Google-Calendar-style UI over every connected Google account (week, day, month, agenda).
import { icon } from "/js/icons.js";
import { state } from "/js/store.js";

const B = "/api/apps/gcal";
export const full = true;
export const flush = true;

const instances = new WeakMap(); // state-ok: keyed by this panel's ctx

export async function navigate(p, ctx) { return instances.get(ctx)?.navigate(p); }

export async function render(el, ctx) {
  let inst = instances.get(ctx);
  if (!inst) {
    inst = mount(el, ctx);
    instances.set(ctx, inst);
  }
  return inst.render(el, ctx);
}

function mount(initialEl, initialCtx) {
  const HOUR = 48;                       // px per hour in the time grid
  const SNAP = 15;                       // minutes
  const S = { view: "week", cursor: new Date(), cals: [], accounts: [], events: [], errors: [], side: true, lastAccount: null, defaultAccount: "", mini: null, ctx: null, el: null, token: 0 };
  try { S.view = localStorage.getItem("cal:view") || "week"; } catch {}

/* ------------------------------------------------------------------ dates */
const FIRST = (() => { try { const l = new Intl.Locale(navigator.language), w = l.weekInfo || l.getWeekInfo?.(); return (w?.firstDay ?? 1) % 7; } catch { return 1; } })();
const sod = (d) => new Date(d.getFullYear(), d.getMonth(), d.getDate());
const addDays = (d, n) => { const x = new Date(d); x.setDate(x.getDate() + n); return x; };
const same = (a, b) => a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
const weekStart = (d) => addDays(sod(d), -((d.getDay() - FIRST + 7) % 7));
const pad = (n) => String(n).padStart(2, "0");
const ymd = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
const hm = (d) => `${pad(d.getHours())}:${pad(d.getMinutes())}`;
const localDate = (s) => { const [y, m, d] = s.slice(0, 10).split("-").map(Number); return new Date(y, m - 1, d); };
function toISO(d) { const o = -d.getTimezoneOffset(), a = Math.abs(o); return `${ymd(d)}T${pad(d.getHours())}:${pad(d.getMinutes())}:00${o >= 0 ? "+" : "-"}${pad(Math.floor(a / 60))}:${pad(a % 60)}`; }
const fmtTime = (d) => d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }).replace(" ", " ").toLowerCase();
const fmtDay = (d, o) => d.toLocaleDateString([], o);
const TZ = Intl.DateTimeFormat().resolvedOptions().timeZone;

function parse(e) {
  if (e.all_day) { const s = localDate(e.start), en = localDate(e.end); return { ...e, s, e: en, allDay: true }; }
  return { ...e, s: new Date(e.start), e: new Date(e.end), allDay: false };
}
function lum(hex) { const [r, g, b] = [1, 3, 5].map((i) => parseInt((hex || "#4285f4").slice(i, i + 2), 16) / 255); return 0.2126 * r + 0.7152 * g + 0.0722 * b; }
const ink = (hex) => (lum(hex) > 0.55 ? "#1f2937" : "#ffffff");

/* ------------------------------------------------------------------ data */
function range() {
  const c = S.cursor;
  if (S.view === "day") return [sod(c), addDays(sod(c), 1)];
  if (S.view === "week") return [weekStart(c), addDays(weekStart(c), 7)];
  if (S.view === "month") { const f = weekStart(new Date(c.getFullYear(), c.getMonth(), 1)); return [f, addDays(f, 42)]; }
  return [sod(new Date()), addDays(sod(new Date()), 30)];
}
async function load() {
  const my = ++S.token, [a, b] = range();
  setLoading(true);
  try {
    const d = await S.ctx.api.get(`${B}/events?start=${encodeURIComponent(toISO(a))}&end=${encodeURIComponent(toISO(b))}`);
    if (my !== S.token) return;
    S.events = d.events.map(parse); S.errors = d.errors || [];
  } catch (e) { S.errors = [e.message]; S.events = []; }
  setLoading(false); paintMain(); paintBanner(); paintTitle();
}
const loading = () => S.el?.querySelector(".cal-loading");
const setLoading = (on) => { const l = loading(); if (l) l.style.opacity = on ? "1" : "0"; };
const writable = () => S.cals.filter((c) => c.role === "owner" || c.role === "writer");
function defaultCal() {
  const pick = (acct) => S.cals.find((c) => c.account === acct && c.primary && (c.role === "owner" || c.role === "writer")) || S.cals.find((c) => c.account === acct && (c.role === "owner" || c.role === "writer"));
  return pick(S.defaultAccount) || pick(S.lastAccount) || writable().find((c) => c.primary) || writable()[0];
}

/* ------------------------------------------------------------------ entry */
async function render(el, ctx) {
  const { api, h } = ctx;
  S.ctx = ctx; S.el = el;
  ctx.onClose(() => { clearInterval(S.timer); document.removeEventListener("keydown", keys); });
  const info = await api.get(`${B}/calendars`);
  S.cals = info.calendars; S.accounts = info.accounts; S.defaultAccount = info.default_account || ""; S.errors = info.errors || [];
  if (!info.accounts.length) {
    return el.replaceChildren(h("div", { class: "files-intro", style: "padding:24px" }, h("h3", {}, "Connect a Google account"),
      h("p", { class: "muted" }, "Add one or more Google accounts to see all your calendars together, create events here, and let the agent manage them."),
      h("button", { class: "btn primary", onclick: () => ctx.openSettings?.("accounts") || (location.href = "/?settings=accounts") }, "Open Accounts settings")));
  }
  shell();
  await load();
  clearInterval(S.timer);
  S.timer = setInterval(() => { if (!el.isConnected) return clearInterval(S.timer); const n = el.querySelector(".nowline"); if (n) positionNow(); }, 60000);
}

/* ------------------------------------------------------------------ shell */
function shell() {
  const { h } = S.ctx;
  const title = h("h3", { class: "cal-title" }), bar = h("div", { class: "cal-bar" },
    h("button", { class: "icon-btn", title: "Calendars", "aria-label": "Toggle calendar list", onclick: () => { S.side = !S.side; S.el.querySelector(".cal").classList.toggle("noside", !S.side); }, html: icon("menu", 18) }),
    h("button", { class: "btn sm", onclick: () => { S.cursor = new Date(); nav(0); } }, "Today"),
    h("button", { class: "icon-btn", "aria-label": "Previous", onclick: () => nav(-1), html: icon("chevleft", 18) }),
    h("button", { class: "icon-btn", "aria-label": "Next", onclick: () => nav(1), html: icon("chevright", 18) }),
    title, h("i", { class: "cal-loading" }), h("div", { class: "grow" }),
    quickAdd(),
    h("div", { class: "seg", role: "group" }, ...[["day", "Day"], ["week", "Week"], ["month", "Month"], ["agenda", "Agenda"]].map(([v, l]) => h("button", { type: "button", "data-v": v, class: v === S.view ? "on" : "", onclick: () => setView(v) }, l))),
    h("button", { class: "btn primary sm", onclick: () => openEditor({}) }, "+ Create"));
  S.el.replaceChildren(h("div", { class: `cal ${S.side ? "" : "noside"}` }, bar, h("div", { class: "cal-banner", hidden: true }),
    h("div", { class: "cal-body" }, h("aside", { class: "cal-side" }), h("div", { class: "cal-main" }))));
  paintTitle(); paintSide();
}

function quickAdd() {
  const { h, api, toast } = S.ctx;
  const inp = h("input", { type: "text", class: "cal-quick", placeholder: "Quick add: lunch with Sam tomorrow 1pm", "aria-label": "Quick add event" });
  inp.addEventListener("keydown", async (e) => {
    if (e.key !== "Enter" || !inp.value.trim()) return;
    const c = defaultCal();
    try { const ev = await api.post(`${B}/quickadd`, { account: c.account, text: inp.value.trim() }); inp.value = ""; toast(`Added “${ev.summary}”`); await load(); }
    catch (err) { toast(err.message, "error"); }
  });
  return inp;
}

function nav(dir) {
  const c = S.cursor;
  if (dir) {
    if (S.view === "day") S.cursor = addDays(c, dir); else if (S.view === "week") S.cursor = addDays(c, 7 * dir);
    else if (S.view === "month") S.cursor = new Date(c.getFullYear(), c.getMonth() + dir, 1); else S.cursor = addDays(c, 30 * dir);
  }
  S.mini = null; paintSide(); load();
}
function setView(v) {
  S.view = v; try { localStorage.setItem("cal:view", v); } catch {}
  S.el.querySelectorAll(".cal-bar .seg button").forEach((b) => b.classList.toggle("on", b.dataset.v === v)); S.mini = null; paintSide(); load();
}
function paintTitle() {
  const t = S.el.querySelector(".cal-title"); if (!t) return;
  const c = S.cursor, [a, b] = range(), last = addDays(b, -1);
  t.textContent = S.view === "day" ? fmtDay(c, { weekday: "long", month: "long", day: "numeric", year: "numeric" })
    : S.view === "month" ? fmtDay(c, { month: "long", year: "numeric" })
    : S.view === "agenda" ? "Next 30 days"
    : a.getMonth() === last.getMonth() ? fmtDay(a, { month: "long", year: "numeric" }) : `${fmtDay(a, { month: "short" })} – ${fmtDay(last, { month: "short", year: "numeric" })}`;
}
function paintBanner() {
  const b = S.el.querySelector(".cal-banner"); if (!b) return;
  b.hidden = !S.errors.length;
  b.replaceChildren(...S.errors.slice(0, 2).map((e) => S.ctx.h("div", {}, "⚠ ", e)), ...(S.errors.length ? [S.ctx.h("a", { href: "/?settings=accounts", style: "margin-left:6px" }, "Open Accounts")] : []));
}

/* ------------------------------------------------------------------ sidebar */
function paintSide() {
  const { h, api } = S.ctx, side = S.el.querySelector(".cal-side"); if (!side) return;
  const c = S.cursor;
  const m = S.mini ? new Date(S.mini.getFullYear(), S.mini.getMonth(), 1) : new Date(c.getFullYear(), c.getMonth(), 1), first = weekStart(m);
  const [ra, rb] = range();
  const days = Array.from({ length: 42 }, (_, i) => addDays(first, i));
  const dow = Array.from({ length: 7 }, (_, i) => addDays(first, i).toLocaleDateString([], { weekday: "narrow" }));
  const mini = h("div", { class: "mini" }, h("div", { class: "mini-head" }, h("strong", {}, fmtDay(m, { month: "long", year: "numeric" })), h("div", { class: "grow" }),
    h("button", { class: "icon-btn sm", "aria-label": "Previous month", onclick: () => { S.mini = new Date(m.getFullYear(), m.getMonth() - 1, 1); paintSide(); }, html: icon("chevleft", 14) }),
    h("button", { class: "icon-btn sm", "aria-label": "Next month", onclick: () => { S.mini = new Date(m.getFullYear(), m.getMonth() + 1, 1); paintSide(); }, html: icon("chevright", 14) })),
    h("div", { class: "mini-grid" }, ...dow.map((d) => h("span", { class: "mdow" }, d)),
      ...days.map((d) => h("button", { type: "button", class: `mday ${d.getMonth() !== m.getMonth() ? "out" : ""} ${same(d, new Date()) ? "today" : ""} ${d >= ra && d < rb && S.view !== "agenda" ? "inrange" : ""}`,
        onclick: () => { S.cursor = d; S.mini = new Date(d.getFullYear(), d.getMonth(), 1); if (S.view === "agenda") S.view = "week"; setView(S.view); } }, String(d.getDate())))));
  const groups = S.accounts.map((a) => h("div", { class: "cal-group" },
    h("div", { class: "cal-ghead" }, h("i", { style: `background:${a.color}` }), h("span", {}, a.label)),
    ...S.cals.filter((c) => c.account === a.email).map((c) => h("label", { class: "cal-item", title: c.name },
      h("input", { type: "checkbox", checked: c.visible, onchange: async (e) => { c.visible = e.target.checked; paintSide(); try { await api.put(`${B}/calendar-prefs`, { account: c.account, id: c.id, visible: c.visible }); } catch {} load(); } }),
      h("i", { class: "box", style: `--c:${c.color}` }), h("span", { class: "nm" }, c.name)))));
  side.replaceChildren(mini, h("div", { class: "p-section" }, "My calendars"), ...groups);
}

/* ------------------------------------------------------------------ main views */
function paintMain() {
  const main = S.el.querySelector(".cal-main"); if (!main) return;
  const keep = main.querySelector(".wk-scroll")?.scrollTop;
  main.replaceChildren(S.view === "month" ? monthView() : S.view === "agenda" ? agendaView() : timeView(S.view === "day" ? 1 : 7));
  const sc = main.querySelector(".wk-scroll");
  if (sc) { sc.scrollTop = keep ?? Math.max(0, (new Date().getHours() - 2) * HOUR); positionNow(); }
}

function chipBg(e) { return e.my_response === "declined" ? "transparent" : e.color || "#4285f4"; }
function evStyle(e) {
  const bg = e.color || "#4285f4", declined = e.my_response === "declined", pending = e.my_response === "needsAction";
  return declined ? `background:transparent;color:${bg};border:1px solid ${bg};text-decoration:line-through`
    : pending ? `background:repeating-linear-gradient(135deg, ${bg}, ${bg} 6px, color-mix(in srgb, ${bg} 70%, #fff) 6px, color-mix(in srgb, ${bg} 70%, #fff) 12px);color:${ink(bg)}`
    : `background:${bg};color:${ink(bg)}`;
}

/* ---- week / day time grid ---- */
function timeView(n) {
  const { h } = S.ctx;
  const start = n === 1 ? sod(S.cursor) : weekStart(S.cursor), cols = Array.from({ length: n }, (_, i) => addDays(start, i));
  const timed = S.events.filter((e) => !e.allDay && e.status !== "cancelled"), allday = S.events.filter((e) => e.allDay);
  const head = h("div", { class: "wk-head", style: `--n:${n}` }, h("div", { class: "gut" }),
    ...cols.map((d) => h("button", { type: "button", class: `dh ${same(d, new Date()) ? "today" : ""}`, onclick: () => { S.cursor = d; setView("day"); } },
      h("span", { class: "dw" }, fmtDay(d, { weekday: "short" })), h("span", { class: "dn" }, String(d.getDate())))));
  // all-day bars spanning columns
  const bars = allday.map((e) => { const a = Math.max(0, Math.round((sod(e.s) - start) / 864e5)), b = Math.min(n, Math.round((sod(e.e) - start) / 864e5)); return { e, a, b: Math.max(b, a + 1) }; }).filter((x) => x.a < n && x.b > 0);
  const rows = []; bars.sort((x, y) => x.a - y.a || y.b - x.b).forEach((x) => { let r = rows.findIndex((row) => row.every((o) => o.b <= x.a || o.a >= x.b)); if (r < 0) { rows.push([]); r = rows.length - 1; } rows[r].push(x); x.r = r; });
  const ad = h("div", { class: "wk-allday", style: `--n:${n}; height:${Math.max(1, rows.length) * 22 + 6}px` }, h("div", { class: "gut" }, "all-day"),
    h("div", { class: "ad-cells" }, ...cols.map(() => h("i", {})), ...bars.map((x) => h("button", { type: "button", class: "adbar", style: `left:calc(${x.a} * 100% / ${n} + 2px);width:calc(${x.b - x.a} * 100% / ${n} - 4px);top:${x.r * 22 + 3}px;${evStyle(x.e)}`,
      title: x.e.summary, onclick: () => openDetails(x.e) }, x.e.summary))));
  // hour grid
  const gutter = h("div", { class: "gut hours" }, ...Array.from({ length: 24 }, (_, i) => h("span", { style: `top:${i * HOUR}px` }, i === 0 ? "" : new Date(2000, 0, 1, i).toLocaleTimeString([], { hour: "numeric" }).toLowerCase())));
  const colEls = cols.map((d, i) => dayColumn(d, i, timed, cols));
  const grid = h("div", { class: "wk-grid", style: `--n:${n}; height:${24 * HOUR}px` }, gutter, ...colEls);
  return h("div", { class: "wk" }, head, ad, h("div", { class: "wk-scroll" }, grid));
}

function dayColumn(d, idx, timed, cols) {
  const { h } = S.ctx, a = d, b = addDays(d, 1);
  const col = h("div", { class: `dcol ${same(d, new Date()) ? "today" : ""}`, "data-i": idx, "data-day": ymd(d) });
  // clip events to this day, then lay out overlaps
  const items = timed.filter((e) => e.s < b && e.e > a).map((e) => ({ e, s: Math.max(e.s, a), en: Math.min(e.e, b) })).sort((x, y) => x.s - y.s || y.en - x.en);
  const clusters = []; let cur = null;
  items.forEach((it) => { if (!cur || it.s >= cur.end) { cur = { items: [], end: 0, cols: [] }; clusters.push(cur); } let c = cur.cols.findIndex((end) => end <= it.s); if (c < 0) { c = cur.cols.length; cur.cols.push(0); } cur.cols[c] = it.en; it.col = c; cur.items.push(it); cur.end = Math.max(cur.end, it.en); });
  clusters.forEach((cl) => cl.items.forEach((it) => {
    const e = it.e, top = ((it.s - a) / 36e5) * HOUR, height = Math.max(((it.en - it.s) / 36e5) * HOUR, 20), w = 100 / cl.cols.length;
    const editable = e.can_edit;
    const el = h("div", { class: `ev ${editable ? "editable" : ""}`, role: "button", tabindex: 0, style: `top:${top}px;height:${height}px;left:calc(${it.col * w}% + 1px);width:calc(${w}% - 3px);${evStyle(e)}`,
      title: `${e.summary}\n${fmtTime(e.s)} – ${fmtTime(e.e)}${e.location ? "\n" + e.location : ""}\n${e.account_label}`, onkeydown: (ev) => ev.key === "Enter" && openDetails(e) },
      h("div", { class: "et" }, e.summary), height > 32 && h("div", { class: "es" }, `${fmtTime(e.s)} – ${fmtTime(e.e)}`), height > 50 && e.location && h("div", { class: "es" }, e.location),
      editable && h("i", { class: "rz", "aria-hidden": "true" }));
    el.addEventListener("mousedown", (ev) => beginDrag(ev, e, it, el, col));
    col.append(el);
  }));
  col.addEventListener("mousedown", (ev) => { if (ev.target === col) beginCreate(ev, col, d); });
  if (same(d, new Date())) col.append(h("div", { class: "nowline" }, h("i", {})));
  return col;
}
function positionNow() { const n = S.el.querySelector(".nowline"); if (!n) return; const t = new Date(); n.style.top = `${(t.getHours() + t.getMinutes() / 60) * HOUR}px`; }

const snap = (min) => Math.round(min / SNAP) * SNAP;
function minutesAt(col, clientY) { const r = col.getBoundingClientRect(); return Math.max(0, Math.min(24 * 60, ((clientY - r.top) / HOUR) * 60)); }

function beginCreate(ev, col, day) {
  if (ev.button !== 0) return;
  const { h } = S.ctx, m0 = snap(minutesAt(col, ev.clientY)), ghost = h("div", { class: "ghost" });
  let m1 = m0, moved = false;
  const move = (e) => { m1 = snap(minutesAt(col, e.clientY)); moved = moved || Math.abs(m1 - m0) >= SNAP; const a = Math.min(m0, m1), b = Math.max(m0, m1, a + SNAP); ghost.style.top = `${(a / 60) * HOUR}px`; ghost.style.height = `${((b - a) / 60) * HOUR}px`; ghost.textContent = `${fmtTime(new Date(day.getFullYear(), day.getMonth(), day.getDate(), 0, a))} – ${fmtTime(new Date(day.getFullYear(), day.getMonth(), day.getDate(), 0, b))}`; };
  col.append(ghost); move(ev);
  const up = () => {
    removeEventListener("mousemove", move); removeEventListener("mouseup", up); ghost.remove();
    let a = Math.min(m0, m1), b = Math.max(m0, m1);
    if (!moved) { a = Math.floor(m0 / 30) * 30; b = a + 60; }
    const mk = (m) => new Date(day.getFullYear(), day.getMonth(), day.getDate(), 0, Math.min(m, 24 * 60 - 1));
    openEditor({ start: mk(a), end: b >= 24 * 60 ? new Date(day.getFullYear(), day.getMonth(), day.getDate(), 23, 59) : mk(b) });
  };
  addEventListener("mousemove", move); addEventListener("mouseup", up);
}

function beginDrag(ev, e, it, el, col) {
  if (ev.button !== 0) return;
  ev.stopPropagation();
  const resize = ev.target.classList.contains("rz"), y0 = ev.clientY, x0 = ev.clientX, top0 = parseFloat(el.style.top), h0 = parseFloat(el.style.height);
  const cols = [...S.el.querySelectorAll(".dcol")], i0 = cols.indexOf(col);
  let dy = 0, di = 0, moved = false;
  const move = (m) => {
    if (!e.can_edit) return;
    if (!moved && Math.hypot(m.clientX - x0, m.clientY - y0) < 4) return;
    moved = true; el.classList.add("dragging");
    dy = snap(((m.clientY - y0) / HOUR) * 60);
    if (resize) el.style.height = `${Math.max(HOUR * SNAP / 60, h0 + (dy / 60) * HOUR)}px`;
    else {
      const under = cols.findIndex((c) => { const r = c.getBoundingClientRect(); return m.clientX >= r.left && m.clientX < r.right; });
      di = under < 0 ? 0 : under - i0; el.style.top = `${top0 + (dy / 60) * HOUR}px`; el.style.transform = `translateX(${(cols[Math.max(0, Math.min(cols.length - 1, i0 + di))].getBoundingClientRect().left - col.getBoundingClientRect().left)}px)`;
    }
  };
  const up = async () => {
    removeEventListener("mousemove", move); removeEventListener("mouseup", up); el.classList.remove("dragging");
    if (!moved) return openDetails(e);
    if (!dy && !di) return paintMain();
    const old = { start: e.start, end: e.end };
    const s = resize ? e.s : new Date(e.s.getTime() + dy * 6e4 + di * 864e5), en = resize ? new Date(e.e.getTime() + dy * 6e4) : new Date(e.e.getTime() + dy * 6e4 + di * 864e5);
    try {
      await patchEvent({ ref: e.ref, start: toISO(s), end: toISO(en), summary: undefined, scope: "this", tz: TZ });
      S.ctx.toast(resize ? "Event resized" : "Event moved", "", { label: "Undo", run: async () => { await patchEvent({ ref: e.ref, start: old.start, end: old.end, scope: "this", tz: TZ }); load(); } });
    } catch (err) { S.ctx.toast(err.message, "error"); }
    load();
  };
  addEventListener("mousemove", move); addEventListener("mouseup", up);
}
async function patchEvent(body) {
  const r = await fetch(`${B}/event`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const d = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(d.error || `Failed (${r.status})`);
  return d;
}

/* ---- month ---- */
function monthView() {
  const { h } = S.ctx, [a] = range(), days = Array.from({ length: 42 }, (_, i) => addDays(a, i)), m = S.cursor.getMonth();
  const dow = Array.from({ length: 7 }, (_, i) => fmtDay(addDays(a, i), { weekday: "short" }));
  const cells = days.map((d) => {
    const evs = S.events.filter((e) => e.s < addDays(d, 1) && e.e > d && e.status !== "cancelled").sort((x, y) => (y.allDay - x.allDay) || x.s - y.s);
    const show = evs.slice(0, 3);
    return h("div", { class: `mcell ${d.getMonth() !== m ? "out" : ""} ${same(d, new Date()) ? "today" : ""}`, onclick: (ev) => { if (ev.target.closest(".mchip, .more")) return; openEditor({ start: new Date(d.getFullYear(), d.getMonth(), d.getDate(), 9), end: new Date(d.getFullYear(), d.getMonth(), d.getDate(), 10), allDay: true }); } },
      h("button", { type: "button", class: "mnum", onclick: (ev) => { ev.stopPropagation(); S.cursor = d; setView("day"); } }, d.getDate() === 1 ? fmtDay(d, { month: "short", day: "numeric" }) : String(d.getDate())),
      ...show.map((e) => h("button", { type: "button", class: `mchip ${e.allDay ? "solid" : ""}`, style: e.allDay ? evStyle(e) : `--c:${e.color}`, title: e.summary, onclick: () => openDetails(e) },
        !e.allDay && h("i", { style: `background:${e.color}` }), !e.allDay && h("span", { class: "mt" }, fmtTime(e.s).replace(/:00/, "")), h("span", { class: "mn" }, e.summary))),
      evs.length > 3 && h("button", { type: "button", class: "more", onclick: () => { S.cursor = d; setView("day"); } }, `${evs.length - 3} more`));
  });
  return h("div", { class: "month" }, h("div", { class: "mdows" }, ...dow.map((d) => h("span", {}, d))), h("div", { class: "mgrid" }, ...cells));
}

/* ---- agenda ---- */
function agendaView() {
  const { h } = S.ctx, byDay = new Map();
  S.events.filter((e) => e.status !== "cancelled").forEach((e) => { for (let d = sod(e.s < sod(new Date()) ? new Date() : e.s); d < e.e && d < addDays(sod(new Date()), 30); d = addDays(d, 1)) { const k = ymd(d); (byDay.get(k) || byDay.set(k, []).get(k)).push(e); if (!e.allDay && e.e <= addDays(d, 1)) break; } });
  if (!byDay.size) return h("div", { class: "p-empty", style: "padding:40px" }, "Nothing scheduled in the next 30 days.");
  return h("div", { class: "agenda" }, ...[...byDay.entries()].sort().map(([k, evs]) => { const d = localDate(k);
    return h("div", { class: "ag-day" }, h("div", { class: `ag-date ${same(d, new Date()) ? "today" : ""}` }, h("b", {}, String(d.getDate())), h("span", {}, fmtDay(d, { month: "short", weekday: "short" }))),
      h("div", { class: "ag-list" }, ...evs.sort((x, y) => (y.allDay - x.allDay) || x.s - y.s).map((e) => h("button", { type: "button", class: "ag-ev", onclick: () => openDetails(e) },
        h("i", { style: `background:${e.color}` }), h("span", { class: "ag-t" }, e.allDay ? "All day" : `${fmtTime(e.s)} – ${fmtTime(e.e)}`), h("span", { class: "ag-n" }, e.summary),
        h("span", { class: "ag-m" }, [e.location, S.accounts.length > 1 ? e.account_label : ""].filter(Boolean).join(" · "))))));
  }));
}

/* ------------------------------------------------------------------ details */
function dialog(content, cls = "") {
  const { h } = S.ctx, d = h("dialog", { class: `modal cal-dialog ${cls}` }, content);
  d.addEventListener("close", () => d.remove()); d.addEventListener("click", (e) => { if (e.target === d) d.close(); });
  document.body.append(d); d.showModal(); return d;
}
function whenText(e) {
  if (e.allDay) { const last = addDays(e.e, -1); return same(e.s, last) ? fmtDay(e.s, { weekday: "long", month: "long", day: "numeric" }) : `${fmtDay(e.s, { month: "short", day: "numeric" })} – ${fmtDay(last, { month: "short", day: "numeric" })}`; }
  return same(e.s, e.e) ? `${fmtDay(e.s, { weekday: "long", month: "long", day: "numeric" })} · ${fmtTime(e.s)} – ${fmtTime(e.e)}` : `${fmtDay(e.s, { month: "short", day: "numeric" })} ${fmtTime(e.s)} – ${fmtDay(e.e, { month: "short", day: "numeric" })} ${fmtTime(e.e)}`;
}
function openDetails(e) {
  const { h, api, toast, prompt } = S.ctx;
  const rsvp = async (r) => { try { await api.post(`${B}/rsvp`, { ref: e.ref, response: r }); toast(`Responded: ${r}`); d.close(); load(); } catch (err) { toast(err.message, "error"); } };
  const mine = e.my_response;
  const d = dialog(h("div", { class: "cd" },
    h("div", { class: "cd-head" }, h("i", { class: "cd-dot", style: `background:${e.color}` }), h("h3", {}, e.summary), h("div", { class: "grow" }),
      e.can_edit && h("button", { class: "icon-btn", title: "Edit", "aria-label": "Edit event", onclick: () => { d.close(); openEditor({ event: e }); }, html: icon("edit", 16) }),
      e.can_edit && h("button", { class: "icon-btn", title: "Delete", "aria-label": "Delete event", onclick: () => { d.close(); confirmDelete(e); }, html: icon("trash", 16) }),
      h("button", { class: "icon-btn", "aria-label": "Close", onclick: () => d.close(), html: "✕" })),
    h("div", { class: "cd-row" }, "🕒 ", whenText(e), e.recurring && h("span", { class: "pill" }, "repeats")),
    e.location && h("div", { class: "cd-row" }, "📍 ", e.location),
    e.meet_url && h("div", { class: "cd-row" }, h("a", { class: "btn sm primary", style: "text-decoration:none", href: e.meet_url, target: "_blank", rel: "noopener noreferrer" }, "Join with Google Meet")),
    e.description && h("div", { class: "cd-desc" }, e.description),
    e.attendees.length ? h("div", { class: "cd-att" }, h("div", { class: "p-section", style: "margin:10px 0 4px" }, `Guests (${e.attendees.length})`),
      ...e.attendees.slice(0, 12).map((a) => h("div", { class: "p-s" }, `${a.name || a.email}${a.organizer ? " (organizer)" : ""} · ${a.response}`))) : null,
    h("div", { class: "cd-row p-s" }, `${e.account_label}${e.calendar_name && e.calendar_name !== e.account ? ` · ${e.calendar_name}` : ""}`),
    mine ? h("div", { class: "cd-rsvp" }, h("span", { class: "p-s" }, "Going?"), ...[["accepted", "Yes"], ["declined", "No"], ["tentative", "Maybe"]].map(([v, l]) => h("button", { class: `btn sm ${mine === v ? "primary" : ""}`, onclick: () => rsvp(v) }, l))) : null,
    h("div", { class: "cd-foot" }, h("button", { class: "btn sm", onclick: () => { d.close(); prompt(`About my event “${e.summary}” on ${fmtDay(e.s, { month: "short", day: "numeric" })}: `); } }, "Ask the agent"),
      e.link && h("a", { class: "btn sm", style: "text-decoration:none", href: e.link, target: "_blank", rel: "noopener noreferrer" }, "Open in Google"))), "cd-modal");
}
async function scopeChoice(title) {
  const { h } = S.ctx;
  return new Promise((resolve) => {
    const d = dialog(h("div", { class: "cd" }, h("h3", {}, title), h("div", { class: "cd-scope" },
      ...[["this", "This event"], ["all", "All events in the series"]].map(([v, l]) => h("button", { class: "btn", onclick: () => d.close(v) }, l)), h("button", { class: "btn", onclick: () => d.close("") }, "Cancel"))), "cd-modal small");
    d.addEventListener("close", () => resolve(d.returnValue || null));
  });
}
async function confirmDelete(e) {
  const { api, toast } = S.ctx;
  let scope = "this";
  if (e.recurring) { scope = await scopeChoice("Delete recurring event"); if (!scope) return; } else if (!confirm(`Delete “${e.summary}”?`)) return;
  try { await api.del(`${B}/event?ref=${encodeURIComponent(e.ref)}&scope=${scope}`); toast("Deleted"); } catch (err) { toast(err.message, "error"); }
  load();
}

/* ------------------------------------------------------------------ editor */
function openEditor({ event, start, end, allDay }) {
  const { h, api, toast } = S.ctx, editing = !!event;
  const e = event || {};
  const s0 = event ? event.s : start || new Date(Math.ceil(Date.now() / 18e5) * 18e5), e0 = event ? event.e : end || new Date(s0.getTime() + 36e5);
  const ad0 = event ? event.allDay : !!allDay;
  const cals = writable(), cur = event ? cals.find((c) => c.account === e.account && c.id === e.calendar_id) : defaultCal();
  const title = h("input", { type: "text", class: "ed-title", autofocus: true, placeholder: "Add title", value: e.summary && e.summary !== "(no title)" ? e.summary : "" });
  const mkD = (v) => h("input", { type: "date", value: ymd(v) }), mkT = (v) => h("input", { type: "time", step: 300, value: hm(v) });
  const sd = mkD(s0), st = mkT(s0), ed = mkD(ad0 ? addDays(e0, -1) : e0), et = mkT(e0);
  if (ad0 && !event) { ed.value = ymd(s0); }
  const all = h("input", { type: "checkbox", checked: ad0 });
  const timeRow = h("div", { class: "ed-times" }, sd, st, h("span", {}, "–"), et, ed);
  const syncAll = () => { st.hidden = et.hidden = all.checked; }; all.addEventListener("change", syncAll); syncAll();
  const sel = h("select", {}, ...S.accounts.map((a) => { const list = cals.filter((c) => c.account === a.email); return list.length ? h("optgroup", { label: a.label }, ...list.map((c) => h("option", { value: `${c.account}|${c.id}`, selected: cur && c.account === cur.account && c.id === cur.id }, c.name))) : null; }).filter(Boolean));
  if (editing) sel.disabled = true;
  const rep = h("select", {}, ...[["none", "Does not repeat"], ["daily", "Every day"], ["weekly", "Every week"], ["weekdays", "Every weekday"], ["monthly", "Every month"], ["yearly", "Every year"]].map(([v, l]) => h("option", { value: v }, l)));
  const guests = h("input", { type: "text", placeholder: "Add guests (comma-separated emails)", value: (e.attendees || []).filter((a) => !a.self).map((a) => a.email).join(", ") });
  const meet = h("input", { type: "checkbox" }), loc = h("input", { type: "text", placeholder: "Add location", value: e.location || "" }), desc = h("textarea", { placeholder: "Add description", rows: 4 }, e.description || "");
  const err = h("div", { class: "form-err" });
  const swatch = h("i", { class: "cd-dot" }); const paintSw = () => { const [a, id] = sel.value.split("|"); swatch.style.background = cals.find((c) => c.account === a && c.id === id)?.color || "#4285f4"; };
  sel.addEventListener("change", paintSw); paintSw();

  const save = async () => {
    err.textContent = "";
    const [account, calendar] = sel.value.split("|");
    let a, b;
    if (all.checked) { a = sd.value; b = ymd(addDays(localDate(ed.value || sd.value), 1)); if (localDate(b) <= localDate(a)) b = ymd(addDays(localDate(a), 1)); }
    else {
      const [y, mo, dd] = sd.value.split("-").map(Number), [hh, mm] = st.value.split(":").map(Number), [y2, mo2, d2] = (ed.value || sd.value).split("-").map(Number), [h2, m2] = et.value.split(":").map(Number);
      const A = new Date(y, mo - 1, dd, hh, mm), Bd = new Date(y2, mo2 - 1, d2, h2, m2);
      if (isNaN(A) || isNaN(Bd)) return (err.textContent = "Pick a start and end time.");
      a = toISO(A); b = toISO(Bd <= A ? new Date(A.getTime() + 36e5) : Bd);
    }
    const body = { summary: title.value.trim() || "(no title)", all_day: all.checked, start: a, end: b, location: loc.value.trim(), description: desc.value, attendees: guests.value.split(/[,\s;]+/).filter(Boolean), tz: TZ };
    if (meet.checked) body.meet = true;
    btn.disabled = true;
    try {
      if (editing) {
        let scope = "this";
        if (e.recurring) { scope = await scopeChoice("Edit recurring event"); if (!scope) { btn.disabled = false; return; } }
        await patchEvent({ ref: e.ref, ...body, scope });
      } else {
        S.lastAccount = account;
        await api.post(`${B}/events`, { ...body, account, calendar, repeat: rep.value });
      }
      d.close(); toast(editing ? "Event saved" : "Event created"); load();
    } catch (x) { err.textContent = x.message; btn.disabled = false; }
  };
  const btn = h("button", { class: "btn primary", onclick: save }, "Save");
  const d = dialog(h("div", { class: "cd ed" }, h("div", { class: "cd-head" }, h("h3", {}, editing ? "Edit event" : "New event"), h("div", { class: "grow" }), h("button", { class: "icon-btn", "aria-label": "Close", onclick: () => d.close(), html: "✕" })),
    title, timeRow, h("label", { class: "chk" }, all, "All day"),
    h("div", { class: "ed-row" }, swatch, sel), !editing && h("div", { class: "ed-row" }, "🔁", rep), h("div", { class: "ed-row" }, "👥", guests),
    (!e.meet_url) && h("label", { class: "chk" }, meet, "Add Google Meet video conferencing"),
    h("div", { class: "ed-row" }, "📍", loc), h("div", { class: "ed-row top" }, "📝", desc), err,
    h("div", { class: "cd-foot" }, editing && e.can_edit && h("button", { class: "btn danger", onclick: () => { d.close(); confirmDelete(e); } }, "Delete"), h("div", { class: "grow" }), h("button", { class: "btn", onclick: () => d.close() }, "Cancel"), btn)), "cd-modal");
  d.addEventListener("keydown", (k) => { if (k.key === "Enter" && !k.shiftKey && k.target.tagName === "INPUT" && k.target.type === "text") { k.preventDefault(); save(); } });
  setTimeout(() => title.focus(), 30);
}

/** Called by the agent (or anything else) to land on a view, a date or one event. */
async function navigate(p) {
  const { api } = S.ctx;
  let ev = null;
  if (p.item) { try { ev = parse(await api.get(`${B}/event?ref=${encodeURIComponent(p.item)}`)); } catch { throw new Error("I couldn't find that event."); } }
  if (["day", "week", "month", "agenda"].includes(p.view)) { S.view = p.view; try { localStorage.setItem("cal:view", p.view); } catch {} }
  if (p.date) { const [y, m, d] = p.date.split("-").map(Number); S.cursor = new Date(y, m - 1, d); } else if (ev) S.cursor = ev.s;
  S.mini = null;
  S.el.querySelectorAll(".cal-bar .seg button").forEach((b) => b.classList.toggle("on", b.dataset.v === S.view));
  paintSide(); await load();
  if (ev) openEditor({ event: ev });
}

return { render, navigate };
}
