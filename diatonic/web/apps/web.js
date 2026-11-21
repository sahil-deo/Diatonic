// Web panel: search the web, read a page as plain text. No scripts from the page ever run here.
import { icon } from "/js/icons.js";

const BASE = "/api/apps/web";
export const wide = true;

export async function render(el, ctx) {
  const { api, h } = ctx;
  /** The real-browser button: an icon link, so it also works with a middle click. */
  const ext = (u) => h("a", { class: "icon-btn sm web-ext", href: u, target: "_blank", rel: "noopener noreferrer", title: "Open in your browser", "aria-label": "Open in your browser", html: icon("external", 14) });
  const row = (u, ...kids) => h("div", { class: "web-row" }, h("button", { class: "p-item", onclick: () => open(u) }, ...kids), ext(u));
  const input = h("input", { type: "search", placeholder: "Search the web or paste an address…", "aria-label": "Search the web or paste an address" });
  const body = h("div", { class: "web-body" });
  const go = () => {
    const v = input.value.trim();
    if (!v) return;
    /^(https?:\/\/|www\.)\S+$/i.test(v) ? open(v.startsWith("www.") ? "https://" + v : v) : search(v);
  };
  input.addEventListener("keydown", (e) => { if (e.key === "Enter") go(); });
  el.replaceChildren(h("div", { class: "quick-add" }, input, h("button", { class: "btn primary sm", onclick: go }, "Go")), body);
  body.append(h("div", { class: "p-empty" }, "Search the web, or ask the agent to look something up. Pages open as plain text, without scripts."));

  async function search(q) {
    body.replaceChildren(h("div", { class: "p-s" }, "Searching…"));
    try {
      const d = await api.get(`${BASE}/search?q=${encodeURIComponent(q)}`);
      body.replaceChildren(...d.results.map((r) => row(r.url,
        h("div", { class: "p-t" }, r.title), h("div", { class: "p-s" }, new URL(r.url).hostname), h("div", { class: "p-s clamp" }, r.snippet))));
    } catch (e) { body.replaceChildren(h("div", { class: "p-empty" }, e.message)); }
  }

  async function open(u) {
    body.replaceChildren(h("div", { class: "p-s" }, "Opening…"));
    try {
      const d = await api.get(`${BASE}/read?url=${encodeURIComponent(u)}`);
      body.replaceChildren(
        h("div", { class: "web-bar" }, h("button", { class: "btn sm", onclick: () => search(input.value.trim() || d.title) }, "← Results"),
          ext(d.url)),
        h("h3", { class: "p-t" }, d.title || d.url),
        h("div", { class: "web-text" }, d.text),
        ...(d.links.length ? [h("div", { class: "p-s" }, "Links on this page")] : []),
        ...d.links.map((l) => row(l.url, h("div", { class: "p-t" }, l.text), h("div", { class: "p-s" }, l.url))));
    } catch (e) { body.replaceChildren(h("div", { class: "p-empty" }, e.message)); }
  }
}

export const current = () => null;
/** Called by the agent: search for words or open an address. */
export async function navigate(p, ctx) { return undefined; }
