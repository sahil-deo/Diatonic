// Telemetry panel: how fast and how reliable the agent is. Reads only when opened; costs the harness nothing otherwise.
const BASE = "/api/apps/telemetry";
export const wide = true;
let days = 1;

const sec = (ms) => (ms == null ? "–" : ms < 950 ? `${Math.round(ms)} ms` : `${(ms / 1000).toFixed(1)} s`);
const num = (n) => (n == null ? "–" : n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n));

export async function render(el, { api, h, toast, reload, prompt }) {
  const s = await api.get(`${BASE}/summary?days=${days}`);
  const range = h("div", { class: "seg" }, ...[[0.25, "6h"], [1, "Today"], [7, "7 days"], [30, "30 days"]].map(([d, l]) =>
    h("button", { type: "button", class: d === days ? "on" : "", onclick: () => { days = d; reload(); } }, l)));

  const head = h("div", { class: "tel-head" }, range, h("div", { class: "grow" }),
    h("label", { class: "chk" }, h("input", { type: "checkbox", checked: s.cfg.enabled, onchange: async (e) => { await api.put("/api/config", { telemetry: { enabled: e.target.checked } }); toast(e.target.checked ? "Telemetry on" : "Telemetry off"); } }), "Record"));
  el.append(head);
  if (!s.runs) return el.append(h("div", { class: "p-empty" }, s.cfg.enabled ? "No runs in this period yet. Send a message and come back." : "Telemetry is off. Tick “Record” to start."), footer());

  const stat = (label, value, sub, tone) => h("div", { class: `stat ${tone || ""}` }, h("div", { class: "stat-v" }, value), h("div", { class: "stat-l" }, label), sub && h("div", { class: "stat-s" }, sub));
  const errRate = s.tool_calls ? Math.round(100 * s.tool_errors / s.tool_calls) : 0;
  el.append(h("div", { class: "stats" },
    stat("Runs", String(s.runs), `${s.ok} ok · ${s.errors} failed · ${s.cancelled} stopped`, s.errors ? "warn" : ""),
    stat("Median time", sec(s.p50_ms), `90th pct ${sec(s.p90_ms)}`),
    stat("First output", sec(s.ttft_p50_ms), "median, after sending"),
    stat("Generation", s.gen_tok_s ? `${s.gen_tok_s} tok/s` : "–", `${num(s.completion_tokens)} tokens written`),
    stat("Prompt size", `${num(s.avg_prompt_tokens)} tok`, "average per model call"),
    stat("Cache reuse", s.cache_pct == null ? "–" : `~${s.cache_pct}%`, "estimated; higher = faster", s.cache_pct != null && s.cache_pct < 30 ? "warn" : ""),
    stat("Tool errors", `${errRate}%`, `${s.tool_errors} of ${s.tool_calls} calls`, errRate > 15 ? "warn" : ""),
    stat("Model calls / run", (s.model_calls / s.runs).toFixed(1), "fewer is faster")));

  // where the time goes
  const sh = s.share, other = Math.max(0, sh.total_ms - sh.prefill_ms - sh.gen_ms - sh.tool_ms - sh.load_ms);
  const parts = [["Reading prompts", sh.prefill_ms, "var(--accent)"], ["Writing", sh.gen_ms, "var(--ok)"], ["Tools", sh.tool_ms, "var(--warn)"], ["Model loading", sh.load_ms, "var(--danger)"], ["Other", other, "var(--faint)"]];
  const tot = parts.reduce((a, p) => a + p[1], 0) || 1;
  el.append(h("div", { class: "p-section" }, "Where the time goes"),
    h("div", { class: "split", role: "img", "aria-label": "Time split" }, ...parts.filter((p) => p[1] > 0).map((p) => h("i", { style: `flex:${p[1]};background:${p[2]}`, title: `${p[0]}: ${sec(p[1])}` }))),
    h("div", { class: "legend" }, ...parts.filter((p) => p[1] > 0).map((p) => h("span", {}, h("i", { style: `background:${p[2]}` }), `${p[0]} ${Math.round(100 * p[1] / tot)}%`))));

  // recent runs, stacked bars
  const mx = Math.max(...s.series.map((r) => r.total || 0), 1);
  el.append(h("div", { class: "p-section" }, `Last ${s.series.length} runs (taller = slower)`),
    h("div", { class: "bars" }, ...s.series.map((r) => {
      const seg = (v, c) => h("i", { style: `height:${Math.max(0, 100 * (v || 0) / mx)}%;background:${c}` });
      return h("button", { class: `bar ${r.status !== "ok" ? "bad" : ""}`, title: `${sec(r.total)} · ${new Date(r.at * 1000).toLocaleTimeString()}`, onclick: () => detail(r.id) },
        seg(r.tool, "var(--warn)"), seg(r.gen, "var(--ok)"), seg(r.prefill, "var(--accent)"));
    })));

  if (s.guards.length) el.append(h("div", { class: "p-section" }, "Guard events"), h("div", { class: "chips" }, ...s.guards.map((g) => h("span", { class: "pill" }, `${g.kind} × ${g.count}`))));
  if (s.tools.length) el.append(h("div", { class: "p-section" }, "Tools"), h("table", { class: "tel-table" },
    h("thead", {}, h("tr", {}, ...["Tool", "Calls", "Errors", "Avg"].map((t) => h("th", {}, t)))),
    h("tbody", {}, ...s.tools.map((t) => h("tr", {}, h("td", {}, t.name), h("td", {}, t.calls), h("td", { class: t.errors ? "overdue" : "" }, t.errors), h("td", {}, sec(t.avg_ms)))))));

  // run lists
  const list = h("div", {});
  const load = async (sort, status) => {
    const rows = await api.get(`${BASE}/runs?days=${days}&sort=${sort}&limit=12${status ? `&status=${status}` : ""}`);
    list.replaceChildren(...(rows.length ? rows.map(runRow) : [h("div", { class: "p-empty" }, "Nothing here.")]));
  };
  const runRow = (r) => h("button", { class: "p-item", onclick: () => detail(r.id) },
    h("div", { class: "p-t clamp" }, r.request || "(request text not stored)"),
    h("div", { class: "p-s" }, [sec(r.total_ms), `${num(r.prompt_tokens)} tok`, r.cache_pct != null ? `cache ~${r.cache_pct}%` : null, `${r.model_calls} call${r.model_calls === 1 ? "" : "s"}`, r.tool_calls ? `${r.tool_calls} tool${r.tool_calls === 1 ? "" : "s"}` : null,
      r.status !== "ok" ? r.status : null, ...r.guards.slice(0, 2)].filter(Boolean).join(" · ")));
  const tabs = h("div", { class: "seg", style: "margin:6px 0 8px" }, ...[["Slowest", "slowest", ""], ["Recent", "recent", ""], ["Problems", "recent", "problems"]].map(([l, so, st], i) =>
    h("button", { type: "button", class: i === 0 ? "on" : "", onclick: (e) => { tabs.querySelectorAll("button").forEach((b) => b.classList.remove("on")); e.currentTarget.classList.add("on"); load(so, st); } }, l)));
  el.append(h("div", { class: "p-section" }, "Runs"), tabs, list, footer());
  load("slowest", "");

  function footer() {
    return h("div", { class: "tel-foot" },
      h("label", { class: "chk" }, h("input", { type: "checkbox", checked: s.cfg.store_text, onchange: (e) => api.put("/api/config", { telemetry: { store_text: e.target.checked } }).then(() => toast("Saved")) }), "Keep the first 160 characters of each request"),
      h("div", { class: "row", style: "margin-top:8px" },
        h("button", { class: "btn sm danger", onclick: async () => { if (confirm("Delete all recorded runs?")) { await api.post(`${BASE}/clear`); reload(); } } }, "Clear data"),
        h("span", { class: "p-s" }, `Kept ${s.cfg.retention_days} days. One small write per run, no extra model calls.`)));
  }

  async function detail(id) {
    const r = await api.get(`${BASE}/run/${id}`);
    const maxMs = Math.max(...r.steps.map((x) => x.ms), 1);
    let mi = 0;   // numbers only the model calls
    el.replaceChildren(...[
      h("div", { class: "note-bar" }, h("button", { class: "btn sm", onclick: reload }, "← Back"), h("div", { class: "grow" }), h("span", { class: "p-s" }, new Date(r.started_at * 1000).toLocaleString())),
      h("div", { class: "p-t", style: "margin-bottom:4px" }, r.request || "(request text not stored)"),
      h("div", { class: "p-s" }, [r.source, r.model, `apps: ${r.apps.join(", ") || "none"}`, `${r.tools_offered} tools offered`].join(" · ")),
      h("div", { class: "stats", style: "margin-top:10px" },
        stat("Total", sec(r.total_ms), r.status, r.status !== "ok" ? "warn" : ""), stat("First output", sec(r.ttft_ms)),
        stat("Prompt", `${num(r.prompt_tokens)} tok`, `${sec(r.prefill_ms)} reading`), stat("Written", `${r.completion_tokens} tok`, `${sec(r.gen_ms)} writing`)),
      r.error && h("div", { class: "err" }, r.error),
      r.guards.length ? h("div", { class: "chips", style: "margin:8px 0" }, ...r.guards.map((g) => h("span", { class: "pill" }, g))) : null,
      h("div", { class: "p-section" }, "Timeline"),
      ...r.steps.map((x) => h("div", { class: "tl-row" },
        h("span", { class: `tl-k ${x.k}` }, x.k === "model" ? `model call ${++mi}` : x.n),
        h("div", { class: "tl-bar" }, h("i", { class: x.k, style: `width:${Math.max(2, 100 * x.ms / maxMs)}%` })),
        h("span", { class: "p-s tl-d" }, x.k === "model" ? `${sec(x.ms)} · ${num(x.p)} in${x.cache_pct != null ? ` (cache ~${x.cache_pct}%)` : ""} · ${x.c} out${x.loop ? " · loop" : ""}` : `${sec(x.ms)}${x.ok ? "" : " · failed"}`)))].filter(Boolean));   // (replaceChildren would print null as text)
  }
}
