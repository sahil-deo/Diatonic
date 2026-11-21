export async function render(el, ctx) {
  const { ui, h } = ctx;
  const UNITS = { sec: 1, min: 60, hour: 3600 };
  let timers = await ctx.timer.list();
  const left = (t) => (t.state === "running" ? Math.max(0, t.ends_at - Date.now() / 1000) : t.remaining);
  const fmt = (s) => { s = Math.ceil(s); const p = (n) => String(n).padStart(2, "0"); return `${p(Math.floor(s / 3600))}:${p(Math.floor((s % 3600) / 60))}:${p(s % 60)}`; };
  const refresh = async () => { timers = await ctx.timer.list(); paint(); };
  const act = (label, fn) => (e) => ctx.ui.run(label, async () => { await fn(); await refresh(); }, e.currentTarget);
  const board = h("div");
  const paint = () => board.replaceChildren(ui.list({ empty: "No timers yet. Start one above.", items: timers.map((t) => ({
    title: t.label || "Timer", subtitle: fmt(left(t)),
    right: [ui.badge({ text: t.state, tone: t.state === "done" ? "ok" : t.state === "paused" ? "warn" : "accent" }),
      t.state === "running" && ui.button({ label: "Pause", size: "sm", onclick: act("Pause", () => ctx.timer.pause(t.id)) }),
      t.state === "paused" && ui.button({ label: "Resume", size: "sm", onclick: act("Resume", () => ctx.timer.resume(t.id)) }),
      ui.button({ label: t.state === "done" ? "Clear" : "Reset", size: "sm", onclick: act("Reset", () => ctx.timer.cancel(t.id)) })] })) }));
  const start = ui.form({
    submit: "Start",
    fields: [{ name: "label", label: "Label", placeholder: "Optional" }, { name: "amount", label: "Length", type: "number", value: ctx.state.get("amount", 5) },
      { name: "unit", label: "Unit", type: "select", options: [["sec", "Seconds"], ["min", "Minutes"], ["hour", "Hours"]], value: ctx.state.get("unit", "min") }],
    onSubmit: (v) => ctx.ui.run("Start timer", async () => {
      if (!(v.amount > 0)) throw new Error("Enter a length above zero.");
      ctx.state.set("amount", v.amount); ctx.state.set("unit", v.unit);
      await ctx.timer.start(v.amount * UNITS[v.unit], { label: v.label });
      await refresh();
    }),
  });
  const tick = setInterval(() => {
    board.querySelectorAll(".ui-row").forEach((row, i) => { const sub = row.querySelector(".ui-sub"); if (sub && timers[i]) sub.textContent = fmt(left(timers[i])); });
  }, 1000);
  ctx.onClose(() => clearInterval(tick));
  ctx.timer.onDone((t) => { ctx.toast(`${t.label || "Timer"} is done`); refresh(); });
  el.append(ui.card({ title: "New timer", body: start }), ui.section({ title: "Timers", body: board }));
  paint();
}
