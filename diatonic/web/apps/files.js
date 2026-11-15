// Files: explorer + viewers (code with colour, Markdown, PDF, images). Reading only; changes go through the agent.
import { render as md } from "/js/md.js";
import { lines as hlLines } from "/js/highlight.js";
import { icon } from "/js/icons.js";
import { state, emit } from "/js/store.js";

const B = "/api/apps/files";
export const wide = true;
export const width = 760;

const st = { path: "", file: null, hidden: false, q: "", wrap: false, source: false, scroll: 0 };
const enc = encodeURIComponent;
const rawUrl = (p) => `${B}/raw?path=${enc(p)}`;
const size = (n) => (n == null ? "" : n < 1024 ? `${n} B` : n < 1048576 ? `${(n / 1024).toFixed(1)} KB` : `${(n / 1048576).toFixed(1)} MB`);
const ago = (t) => new Date(t * 1000).toLocaleDateString([], { month: "short", day: "numeric", year: t * 1000 < Date.now() - 3e10 ? "numeric" : undefined });
const KIND_ICON = { folder: "folder", markdown: "file-text", pdf: "file-text", image: "image", text: "code", binary: "file" };

let host = null;
export const current = () => (st.file ? { kind: "file", ref: st.file, title: st.file.split("/").pop() }
  : host?.el?.isConnected ? { kind: "folder", ref: st.path || ".", title: st.path || "the shared folders" } : null);
/** Called by the agent: open a file in the viewer, or a folder in the browser. */
export async function navigate(p) {
  if (!p.item || !host) return;
  const path = p.item.replace(/^@/, "");
  const isFile = p.kind ? p.kind === "file" : /\.[A-Za-z0-9]{1,8}$/.test(path.split("/").pop());
  if (isFile) { st.file = path; st.path = path.replace(/\/[^/]*$/, ""); } else { st.file = null; st.path = path; }
  await render(host.el, host.ctx);
}

export async function render(el, ctx) {
  const { api, h, toast, reload, prompt } = ctx;
  host = { el, ctx };
  const info = await api.get(`${B}/state`);
  if (state.openFile) { st.file = state.openFile; st.path = state.openFile.replace(/\/[^/]*$/, ""); state.openFile = null; }
  if (!info.roots.length) return onboarding(el, ctx, info);
  queueMicrotask(() => emit("screen"));
  if (st.file) return viewer(el, ctx, info);
  return browser(el, ctx, info);
}

/* ---------------------------------------------------------------- onboarding / folders */
function onboarding(el, ctx, info) {
  const { h } = ctx;
  el.replaceChildren(
    h("div", { class: "files-intro" },
      h("h3", {}, "Share a folder"),
      h("p", { class: "muted" }, "Pick the folders the viewer and the agent may use. Nothing outside them is ever readable. Credentials and key files are always blocked, the agent asks before changing anything (and shows you the diff), and deleted files go to the Trash."),
      h("div", { class: "chips" }, ...info.suggestions.map((p) => h("button", { class: "btn", onclick: () => addRoot(ctx, p) }, `+ ${p.split("/").pop()}`))),
      h("div", { class: "quick-add", style: "margin-top:14px" }, pathInput(ctx), h("button", { class: "btn", onclick: () => picker(ctx) }, "Browse…"))));
}
function pathInput(ctx) {
  const { h } = ctx;
  const inp = h("input", { type: "text", placeholder: "/Users/you/Documents/project", "aria-label": "Folder path" });
  inp.addEventListener("keydown", (e) => e.key === "Enter" && inp.value.trim() && addRoot(ctx, inp.value.trim()));
  pathInput.last = inp;
  return inp;
}
async function addRoot(ctx, path) {
  try { await ctx.api.post(`${B}/roots`, { path }); ctx.reload(); } catch (e) { ctx.toast(e.message, "error"); }
}
function picker(ctx) {
  const { h, api } = ctx;
  const list = h("div", { class: "picker-list" }), head = h("div", { class: "p-s", style: "word-break:break-all" });
  const d = h("dialog", { class: "modal dialog-small" }, h("div", { class: "body" }, h("strong", {}, "Choose a folder to share"), head, list,
    h("div", { class: "btns" }, h("button", { class: "btn", onclick: () => d.close() }, "Cancel"), h("button", { class: "btn primary", onclick: () => { const p = d.dataset.path; d.close(); addRoot(ctx, p); } }, "Share this folder"))));
  async function go(path) {
    const r = await api.get(`${B}/dirs?path=${enc(path || "")}`);
    d.dataset.path = r.path; head.textContent = r.path;
    list.replaceChildren(...(r.parent ? [h("button", { class: "p-item", onclick: () => go(r.parent) }, "↑ ..")] : []), ...r.dirs.map((x) => h("button", { class: "p-item", onclick: () => go(x.path) }, `📁 ${x.name}`)));
  }
  d.addEventListener("close", () => d.remove());
  document.body.append(d); d.showModal(); go("");
}
function manageRoots(el, ctx, info) {
  const { h, api, reload } = ctx;
  return h("details", { class: "newjob" }, h("summary", {}, `Shared folders (${info.roots.length})`),
    ...info.roots.map((r) => h("div", { class: "p-row" }, h("div", { class: "grow p-s", style: "word-break:break-all" }, r.path),
      h("button", { class: "btn sm danger", onclick: async () => { await api.del(`${B}/roots?path=${enc(r.path)}`); st.path = ""; st.file = null; reload(); } }, "Stop sharing"))),
    h("div", { class: "quick-add", style: "margin-top:8px" }, pathInput(ctx), h("button", { class: "btn", onclick: () => picker(ctx) }, "Browse…")),
    h("label", { class: "chk", style: "margin-top:8px" }, h("input", { type: "checkbox", checked: info.allow_sensitive, onchange: async (e) => {
      if (e.target.checked && !confirm("Allow reading credential-like files (.env, keys, secrets.json) in shared folders? The agent could then read them.")) { e.target.checked = false; return; }
      await api.post(`${B}/sensitive`, { value: e.target.checked }); reload(); } }), "Allow credential-like files (not recommended)"));
}

/* ---------------------------------------------------------------- browser */
async function browser(el, ctx, info) {
  const { h, api, reload, prompt } = ctx;
  const listing = await api.get(`${B}/ls?path=${enc(st.path)}${st.hidden ? "&hidden=1" : ""}`).catch(() => { st.path = ""; return api.get(`${B}/ls?path=`); });
  const body = h("div", { class: "files-list" });
  const rows = (entries) => entries.map((e) => h("div", { class: "frow", tabindex: 0, role: "button",
    onclick: () => { if (e.dir) { st.path = e.path; st.q = ""; reload(); } else { st.file = e.path; st.scroll = 0; reload(); } },
    onkeydown: (ev) => ev.key === "Enter" && ev.currentTarget.click() },
    h("span", { class: `fi ${e.kind}`, html: icon(KIND_ICON[e.kind] || "file", 16) }), h("span", { class: "fn" }, e.name),
    h("span", { class: "fm" }, e.dir ? "" : size(e.size)), h("span", { class: "fm" }, e.mtime ? ago(e.mtime) : ""),
    h("button", { class: "icon-btn sm fref", title: "Reference in chat", "aria-label": `Reference ${e.name} in chat`, html: icon("at", 14),
      onclick: (ev) => { ev.stopPropagation(); prompt(`@${q(e.path, info)} `); } })));
  body.append(...rows(listing.entries));
  if (!listing.entries.length) body.append(h("div", { class: "p-empty" }, "Empty folder."));

  const crumbs = h("div", { class: "crumbs" }, h("button", { class: "crumb", onclick: () => { st.path = ""; reload(); } }, "Folders"),
    ...listing.crumbs.flatMap((c) => [h("span", { class: "sep" }, "/"), h("button", { class: "crumb", onclick: () => { st.path = c.path; reload(); } }, c.name)]));
  const search = h("input", { type: "search", placeholder: "Find a file…", value: st.q, "aria-label": "Find a file" });
  let timer;
  search.addEventListener("input", () => {
    clearTimeout(timer);
    timer = setTimeout(async () => {
      st.q = search.value.trim();
      if (!st.q) return body.replaceChildren(...rows(listing.entries));
      const found = await api.get(`${B}/find?q=${enc(st.q)}&limit=30`);
      body.replaceChildren(...(found.length ? found.map((f) => h("div", { class: "frow", tabindex: 0, role: "button", onclick: () => { if (f.dir) { st.path = f.path; st.q = ""; } else { st.file = f.path; } reload(); } },
        h("span", { class: "fi", html: icon(f.dir ? "folder" : "file", 16) }), h("span", { class: "fn" }, f.rel), h("span", {}, ""), h("span", {}, ""),
        h("button", { class: "icon-btn sm fref", title: "Reference in chat", html: icon("at", 14), onclick: (ev) => { ev.stopPropagation(); prompt(`@${q(f.path, info)} `); } })))
        : [h("div", { class: "p-empty" }, "No matches.")]));
    }, 180);
  });
  el.replaceChildren(crumbs, h("div", { class: "files-tools" }, search, st.path && openIn(ctx, st.path, true),
    h("label", { class: "chk", style: "margin:0;white-space:nowrap" }, h("input", { type: "checkbox", checked: st.hidden, onchange: (e) => { st.hidden = e.target.checked; reload(); } }), "hidden")),
    body, manageRoots(el, ctx, info), h("div", { class: "p-s", style: "margin-top:12px" }, "Click @ on a file (or type @ in the chat box) to reference it. Ask the agent to edit, create or delete files in the chat."));
  if (st.q) search.dispatchEvent(new Event("input"));
}

/** "Open in" menu: the Terminal app at this folder, Finder, or VS Code. For a file: its folder in the Terminal,
 *  the file selected in Finder, the file in VS Code. */
function openIn(ctx, path, isDir) {
  const { h, api, toast } = ctx;
  const dir = isDir ? path : path.replace(/\/[^/]*$/, "");
  const native = (app) => api.post(`${B}/open`, { path, app }).then(() => toast(app === "code" ? "Opened in VS Code" : "Opened in Finder")).catch((e) => toast(e.message, "error"));
  const items = [
    ["Terminal", "command", () => emit("ui", { action: "open", app: "shell", params: { path: dir } })],
    [isDir ? "Finder" : "Show in Finder", "folder", () => native("finder")],
    ["VS Code", "code", () => native("code")],
  ];
  const menu = h("div", { class: "openin-menu", role: "menu", hidden: true }, ...items.map(([label, ic, run]) =>
    h("button", { type: "button", role: "menuitem", onclick: () => { menu.hidden = true; run(); } }, h("span", { html: icon(ic, 14) }), label)));
  const close = (e) => { if (!wrap.contains(e.target)) { menu.hidden = true; removeEventListener("pointerdown", close, true); } };
  const wrap = h("div", { class: "openin" },
    h("button", { type: "button", class: "btn sm", "aria-haspopup": "menu", title: `Open ${isDir ? "this folder" : "this file"} in another app`,
      onclick: () => { menu.hidden = !menu.hidden; if (!menu.hidden) addEventListener("pointerdown", close, true); } }, "Open in ▾"), menu);
  return wrap;
}

/** @-reference text for a path: relative to its shared folder, quoted when it has spaces. */
function q(abs, info) {
  const root = info.roots.find((r) => abs === r.path || abs.startsWith(r.path + "/"));
  const rel = root ? (info.roots.length > 1 ? root.name + abs.slice(root.path.length) : abs.slice(root.path.length + 1)) || root.name : abs;
  return /\s/.test(rel) ? `"${rel}"` : rel;
}

/* ---------------------------------------------------------------- viewer */
async function viewer(el, ctx, info) {
  const { h, api, toast, reload, prompt } = ctx;
  let f;
  try { f = await api.get(`${B}/file?path=${enc(st.file)}`); }
  catch (e) { st.file = null; toast(e.message, "error"); return browser(el, ctx, info); }
  const ref = q(f.path, info);
  const back = () => { st.file = null; st.scroll = 0; reload(); };
  const bar = h("div", { class: "vbar" },
    h("button", { class: "btn sm", onclick: back }, "← Back"), h("div", { class: "vname grow", title: f.path }, f.name),
    openIn(ctx, f.path, false),
    h("button", { class: "btn sm", title: "Put @file in the message box", onclick: () => prompt(`@${ref} `) }, "@ Reference"),
    h("button", { class: "btn sm", onclick: () => prompt(`In @${ref}: `) }, "Change with agent"),
    h("button", { class: "btn sm danger", onclick: () => prompt(`Delete @${ref}`) }, "Delete with agent"));
  const meta = h("div", { class: "vmeta" }, [f.lang !== "text" && f.kind === "text" ? f.lang : f.kind, size(f.size), f.lines ? `${f.lines} lines` : null, `modified ${ago(f.mtime)}`].filter(Boolean).join(" · "),
    f.truncated ? h("span", { class: "overdue" }, " · showing the first 1 MB") : null);
  const content = h("div", { class: "vbody" });
  const opts = h("div", { class: "vopts" });
  el.replaceChildren(bar, meta, opts, content);

  if (f.kind === "pdf") {
    let pages = 5;
    const frame = h("iframe", { class: "pdf", src: rawUrl(f.path), title: f.name }), text = h("div", { class: "pdftext", hidden: true });
    const show = async (mode) => {
      frame.hidden = mode !== "doc"; text.hidden = mode === "doc";
      segs.querySelectorAll("button").forEach((b) => b.classList.toggle("on", b.dataset.m === mode));
      if (mode === "text") {
        text.replaceChildren(h("div", { class: "p-s" }, "Reading…"));
        try {
          const r = await api.get(`${B}/pdf-text?path=${enc(f.path)}&pages=${pages}`);
          text.replaceChildren(h("pre", { class: "pdfpre" }, r.content || "(no extractable text; this may be a scan)"),
            r.truncated ? h("button", { class: "btn sm", onclick: () => { pages += 10; show("text"); } }, `${r.truncated} Load more`) : null);
        } catch (e) { text.replaceChildren(h("div", { class: "err" }, e.message)); }
      }
    };
    const segs = h("div", { class: "seg" }, h("button", { type: "button", class: "on", "data-m": "doc", onclick: () => show("doc") }, "Document"), h("button", { type: "button", "data-m": "text", onclick: () => show("text") }, "Text"));
    content.append(frame, text);
    opts.append(segs, h("a", { class: "btn sm", href: rawUrl(f.path), target: "_blank", rel: "noopener", style: "text-decoration:none" }, "Open in a tab"));
  } else if (f.kind === "image") {
    content.append(h("div", { class: "imgwrap" }, h("img", { src: rawUrl(f.path), alt: f.name })));
  } else if (f.kind === "binary") {
    content.append(h("div", { class: "p-empty" }, "This is a binary file, so there's nothing to show as text."), h("a", { class: "btn", href: rawUrl(f.path), style: "text-decoration:none" }, "Download"));
  } else {
    const isMd = f.kind === "markdown";
    if (isMd) opts.append(h("button", { class: "btn sm", onclick: () => { st.source = !st.source; reload(); } }, st.source ? "Preview" : "Show source"));
    else opts.append(h("label", { class: "chk", style: "margin:0" }, h("input", { type: "checkbox", checked: st.wrap, onchange: (e) => { st.wrap = e.target.checked; code.classList.toggle("wrap", st.wrap); } }), "wrap lines"));
    const code = h("div", { class: `codeview ${st.wrap ? "wrap" : ""}` });
    if (isMd && !st.source) {
      const dir = f.dir, join = (u) => { const parts = (u.startsWith("/") ? u : `${dir}/${u}`).split("/"), out = []; for (const x of parts) { if (x === "..") out.pop(); else if (x && x !== ".") out.push(x); } return "/" + out.join("/"); };
      const view = h("div", { class: "md mdview", html: md(f.content, { frontMatter: true, resolveImage: (u) => (/^https?:/.test(u) ? u : rawUrl(join(u))), fileLink: join }) });
      view.addEventListener("click", (e) => { const a = e.target.closest("a[data-file]"); if (a) { e.preventDefault(); st.file = a.dataset.file; st.scroll = 0; reload(); } });
      content.append(view);
    } else {
      const ls = hlLines(f.content.replace(/\n$/, ""), f.kind === "markdown" ? "markdown" : f.lang);
      let anchor = null, sel = null;
      const bubble = h("button", { class: "btn sm primary selref", hidden: true }, "");
      const mark = () => { code.querySelectorAll(".cl").forEach((r, i) => r.classList.toggle("sel", !!sel && i + 1 >= sel[0] && i + 1 <= sel[1])); bubble.hidden = !sel; if (sel) bubble.textContent = `@ Reference line${sel[0] === sel[1] ? ` ${sel[0]}` : `s ${sel[0]}–${sel[1]}`}`; };
      bubble.onclick = () => prompt(`@${ref}:${sel[0]}${sel[1] !== sel[0] ? "-" + sel[1] : ""} `);
      ls.forEach((html, i) => code.append(h("div", { class: "cl" },
        h("span", { class: "ln", title: "Click to select; shift-click to extend", onclick: (e) => { const n = i + 1; if (e.shiftKey && anchor) sel = [Math.min(anchor, n), Math.max(anchor, n)]; else { anchor = n; sel = sel && sel[0] === n && sel[1] === n ? null : [n, n]; } mark(); } }, String(i + 1)),
        h("span", { class: "lc", html: html || " " }))));
      content.append(bubble, code);
    }
  }
  // backups made before the agent changed this file
  api.get(`${B}/history?path=${enc(f.path)}`).then((hist) => {
    if (!hist.length) return;
    opts.append(h("button", { class: "btn sm", onclick: () => historyDialog(ctx, f, hist) }, `History (${hist.length})`));
  }).catch(() => {});
  requestAnimationFrame(() => { el.scrollTop = st.scroll; });
  el.onscroll = () => { st.scroll = el.scrollTop; };
}

function historyDialog(ctx, f, hist) {
  const { h, api, reload, toast } = ctx;
  const d = h("dialog", { class: "modal dialog-small" }, h("div", { class: "body" }, h("strong", {}, "Earlier versions of " + f.name),
    h("div", { class: "p-s" }, "Saved automatically before each change the agent made. Restoring keeps the current version in this list too."),
    ...hist.map((x) => h("div", { class: "p-row" }, h("div", { class: "grow" }, new Date(x.at * 1000).toLocaleString(), h("div", { class: "p-s" }, size(x.size))),
      h("button", { class: "btn sm", onclick: async () => { if (!confirm("Restore this version? The current file is backed up first.")) return;
        try { await api.post(`${B}/restore`, { path: f.path, id: x.id }); d.close(); toast("Restored"); reload(); } catch (e) { toast(e.message, "error"); } } }, "Restore"))),
    h("div", { class: "btns" }, h("button", { class: "btn", onclick: () => d.close() }, "Close"))));
  d.addEventListener("close", () => d.remove());
  document.body.append(d); d.showModal();
}
