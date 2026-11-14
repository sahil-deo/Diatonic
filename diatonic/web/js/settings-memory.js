// Settings → Memory: see, edit, pin and delete what the agent remembers across chats.
import { h, toast } from "./dom.js";
import { api } from "./api.js";
import { state } from "./store.js";
import { field, text, select, toggle, save } from "./form.js";
import { rerenderSettings } from "./settings.js";

let data = null;   // last response from /api/memories
const SRC = { user: "you", agent: "agent (with your OK)", auto: "learned" };

export function memoryPane() {
  const body = h("div", {}, h("div", { class: "p-empty" }, "Loading…"));
  api.get("/api/memories").then((d) => { data = d; draw(); }).catch((e) => body.replaceChildren(h("div", { class: "err" }, e.message)));

  async function act(fn) { try { data = await fn(); draw(); } catch (e) { toast(e.message, "error"); } }

  function draw() {
    const cfg = state.config.memory, enabledFacts = data.items.filter((i) => i.kind === "fact" && i.enabled);
    const facts = data.items.filter((i) => i.kind === "fact"), lessons = data.items.filter((i) => i.kind === "lesson");
    const scopeName = (s) => s === "global" ? "everywhere" : s.startsWith("app:") ? (data.apps.find((a) => a.id === s.slice(4))?.name || s.slice(4)) : s.replace("tool:", "");

    const addText = h("input", { type: "text", maxlength: 200, placeholder: "e.g. My default GitHub repo is sahil-deo/Mid-Map", "aria-label": "New memory" });
    const addScope = select("global", [["global", "Everywhere"], ...data.apps.filter((a) => a.id !== "memory").map((a) => [`app:${a.id}`, `Only with ${a.name}`])], () => {});
    const add = () => addText.value.trim() && act(async () => {
      const r = await api.post("/api/memories", { text: addText.value.trim(), scope: addScope.value });
      toast(r.status === "duplicate" ? "Already remembered" : "Remembered"); return r;
    });
    addText.addEventListener("keydown", (e) => e.key === "Enter" && add());

    const row = (m) => {
      const label = h("span", { class: "mem-text", title: "Click to edit", tabindex: 0 }, m.text);
      const edit = () => {
        const inp = h("input", { type: "text", value: m.text, maxlength: 200, class: "inline", style: "width:100%" });
        label.replaceWith(inp); inp.focus(); inp.select();
        const done = (ok) => ok && inp.value.trim() && inp.value.trim() !== m.text ? act(() => api.put(`/api/memories/${m.id}`, { text: inp.value })) : draw();
        inp.addEventListener("keydown", (e) => { if (e.key === "Enter") done(true); if (e.key === "Escape") done(false); });
        inp.addEventListener("blur", () => done(true));
      };
      label.addEventListener("click", edit); label.addEventListener("keydown", (e) => e.key === "Enter" && edit());
      return h("div", { class: `act-row ${m.enabled ? "" : "dim"}` },
        h("div", { class: "grow" }, label, h("div", { class: "nm" }, `${scopeName(m.scope)} · ${SRC[m.source] || m.source}${m.uses ? ` · used ${m.uses}×` : ""}`)),
        h("button", { class: `icon-btn sm ${m.pinned ? "pinned" : ""}`, title: m.pinned ? "Pinned: always kept first" : "Pin", "aria-pressed": !!m.pinned, onclick: () => act(() => api.put(`/api/memories/${m.id}`, { pinned: !m.pinned })), html: m.pinned ? "★" : "☆" }),
        h("span", { class: "switch", title: m.enabled ? "Used" : "Ignored" }, h("input", { type: "checkbox", checked: !!m.enabled, "aria-label": "Use this memory", onchange: (e) => act(() => api.put(`/api/memories/${m.id}`, { enabled: e.target.checked })) })),
        h("button", { class: "icon-btn sm", title: "Forget", "aria-label": "Forget", onclick: () => act(() => api.del(`/api/memories/${m.id}`)), html: "✕" }));
    };

    body.replaceChildren(
      toggle("Use memory", "Adds a few short facts to the prompt, and learned tool quirks to the matching tool.", cfg.enabled, async (v) => { await save({ memory: { enabled: v } }); }),
      toggle("Learn from mistakes automatically", "When a tool rejects an argument and the retry works, remember the fix for that tool. Numbers, switches and fixed choices only; never free text. You'll see a note in the chat each time.", cfg.auto_learn, async (v) => { await save({ memory: { auto_learn: v } }); }),
      h("div", { class: "notice", style: "margin-top:12px" }, `Per request, at most ${cfg.max_items} facts / ${cfg.budget_chars} characters (about ${Math.round(cfg.budget_chars / 4)} tokens) are added, and only facts for “everywhere” plus the apps involved. You have ${enabledFacts.length} active fact${enabledFacts.length === 1 ? "" : "s"} (${data.facts_chars} characters). Learned quirks cost nothing unless that exact tool is offered, and unused ones are forgotten after 90 days.`),
      h("div", { class: "p-section" }, "Add a fact"),
      h("div", { class: "quick-add" }, addText, addScope, h("button", { class: "btn primary sm", onclick: add }, "Add")),
      h("div", { class: "help", style: "margin-bottom:6px" }, "You can also say “remember that …” in chat (it asks first) or type /remember …"),
      h("div", { class: "p-section" }, `Facts (${facts.length})`),
      ...(facts.length ? facts.map(row) : [h("div", { class: "p-empty" }, "Nothing yet.")]),
      h("div", { class: "p-section" }, `Learned tool quirks (${lessons.length})`),
      ...(lessons.length ? lessons.map(row) : [h("div", { class: "p-empty" }, "None. These appear when a tool rejects an argument and the fix works.")]));
  }
  return [body];
}
