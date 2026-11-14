export async function render(el, { api, h, prompt }) {
  const d = await api.get("/api/apps/gcal/agenda?days=7");
  if (d.error) return el.append(h("div", { class: "p-empty" }, d.error));
  if (!d.events.length) return el.append(h("div", { class: "p-empty" }, "Nothing on your calendar for the next 7 days."));
  const fmtT = (iso) => new Date(iso).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  const dayKey = (iso) => new Date(iso.length === 10 ? iso + "T00:00" : iso).toDateString();
  const today = new Date().toDateString(), now = Date.now();
  const days = new Map();
  for (const e of d.events) { const k = dayKey(e.start); (days.get(k) || days.set(k, []).get(k)).push(e); }
  for (const [k, evs] of days) {
    const dt = new Date(k), label = k === today ? "Today" : dt.toLocaleDateString([], { weekday: "long", month: "short", day: "numeric" });
    el.append(h("div", { class: "p-day" }, h("h4", { class: k === today ? "today" : "" }, label),
      ...evs.map((e) => {
        const live = !e.all_day && Date.parse(e.start) <= now && now < Date.parse(e.end);
        return h("button", { class: `p-ev ${live ? "now" : ""}`, onclick: () => prompt(`Tell me about my event “${e.summary}” on ${dt.toDateString()}`) },
          h("span", { class: "p-time" }, e.all_day ? "All day" : fmtT(e.start)),
          h("span", {}, h("div", { class: "p-t" }, e.summary), e.location && h("div", { class: "p-s" }, e.location)));
      })));
  }
}
