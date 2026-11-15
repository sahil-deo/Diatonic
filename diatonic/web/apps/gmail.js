// Mail: a Gmail-style client over every connected Google account (unified or per-account).
import { icon } from "/js/icons.js";

const B = "/api/apps/gmail";
export const full = true;
export const flush = true;

const M = { account: "all", label: "INBOX", q: "", threads: [], next: "", sel: new Set(), open: null, labels: { accounts: [] }, accounts: [], ctx: null, el: null, showImages: new Set(), busy: false };
const FOLDERS = [["INBOX", "Inbox", "inbox"], ["STARRED", "Starred", "star"], ["SENT", "Sent", "forward"], ["DRAFT", "Drafts", "edit"], ["SPAM", "Spam", "spam"], ["TRASH", "Trash", "trash"]];
const enc = encodeURIComponent;

const today = new Date();
function fmtDate(iso, long = false) {
  const d = new Date(iso);
  if (long) return d.toLocaleString([], { weekday: "short", month: "short", day: "numeric", year: "numeric", hour: "numeric", minute: "2-digit" });
  if (d.toDateString() === today.toDateString()) return d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  return d.getFullYear() === today.getFullYear() ? d.toLocaleDateString([], { month: "short", day: "numeric" }) : d.toLocaleDateString([], { year: "2-digit", month: "numeric", day: "numeric" });
}
const initial = (p) => (p.name || p.email || "?").trim()[0].toUpperCase();
const hue = (s) => { let h = 0; for (const c of s || "") h = (h * 31 + c.charCodeAt(0)) % 360; return `hsl(${h} 45% 42%)`; };
const acctOf = (email) => M.accounts.find((a) => a.email === email);
const canManage = (a) => a && (a.level === "manage" || a.level === "send");

export async function render(el, ctx) {
  const { api, h } = ctx;
  M.ctx = ctx; M.el = el;
  M.accounts = await api.get(`${B}/accounts`);
  if (!M.accounts.length) {
    return el.replaceChildren(h("div", { class: "files-intro", style: "padding:24px" }, h("h3", {}, "Connect Gmail"),
      h("p", { class: "muted" }, "Add one or more Google accounts with Gmail access to read, organise and send mail from here, and let the agent help with your inbox."),
      h("button", { class: "btn primary", onclick: () => (location.href = "/?settings=accounts") }, "Open Accounts settings")));
  }
  if (M.account !== "all" && !acctOf(M.account)) M.account = "all";
  shell();
  await Promise.all([loadLabels(), loadThreads(true)]);
  document.removeEventListener("keydown", keys); document.addEventListener("keydown", keys);
}

function keys(e) {
  if (!M.el?.isConnected) return document.removeEventListener("keydown", keys);
  if (/INPUT|TEXTAREA|SELECT/.test(document.activeElement?.tagName) || e.metaKey || e.ctrlKey || e.altKey || document.querySelector("dialog[open]")) return;
  const idx = M.threads.findIndex((t) => t.ref === M.open?.ref), k = e.key;
  if (k === "c") compose({}); else if (k === "/") M.el.querySelector(".mail-search").focus();
  else if (k === "j" || k === "k") { const n = M.threads[Math.max(0, Math.min(M.threads.length - 1, idx + (k === "j" ? 1 : -1)))]; if (n) openThread(n); }
  else if (k === "e" && M.open) act("archive", [M.open.ref], true); else if (k === "#" && M.open) act("trash", [M.open.ref], true);
  else if (k === "u" || k === "Escape") closeReader(); else return;
  e.preventDefault();
}

/* ------------------------------------------------------------ shell */
function shell() {
  const { h } = M.ctx;
  const search = h("input", { type: "search", class: "mail-search", placeholder: "Search mail (from:, subject:, is:unread…)", value: M.q, "aria-label": "Search mail" });
  search.addEventListener("keydown", (e) => { if (e.key === "Enter") { M.q = search.value.trim(); loadThreads(true); } });
  search.addEventListener("search", () => { if (!search.value) { M.q = ""; loadThreads(true); } });
  M.el.replaceChildren(h("div", { class: "mail" }, h("aside", { class: "mail-nav" }),
    h("section", { class: "mail-list" }, h("div", { class: "mail-tools" }, search), h("div", { class: "mail-bulk", hidden: true }), h("div", { class: "mail-rows" })),
    h("section", { class: "mail-read" })));
  paintNav(); paintReader();
}

function paintNav() {
  const { h } = M.ctx, nav = M.el.querySelector(".mail-nav"); if (!nav) return;
  const accts = M.labels.accounts.filter((a) => M.account === "all" || a.account === M.account);
  const unread = (id) => accts.reduce((n, a) => n + (a.system.find((s) => s.id === id)?.unread || 0), 0);
  const sendable = M.accounts.some((a) => a.level === "send");
  nav.replaceChildren(...[
    h("button", { class: "btn primary mail-compose", disabled: !sendable, title: sendable ? "" : "No account with permission to send. Change access in Settings → Accounts.", onclick: () => compose({}) }, "✎ Compose"),
    h("div", { class: "mail-accts" }, ...[{ email: "all", label: "All accounts", color: "var(--accent)" }, ...M.accounts].map((a) => h("button", { type: "button", class: `macct ${M.account === a.email ? "on" : ""}`, onclick: () => { M.account = a.email; M.open = null; M.sel.clear(); paintNav(); paintReader(); loadLabels(); loadThreads(true); } },
      h("i", { style: `background:${a.color}` }), h("span", {}, a.label)))),
    h("div", { class: "mfold-list" }, ...FOLDERS.map(([id, name, ic]) => { const u = id === "TRASH" ? 0 : unread(id); return h("button", { type: "button", class: `mfold ${M.label === id ? "on" : ""}`, onclick: () => { M.label = id; M.open = null; M.sel.clear(); paintNav(); paintReader(); loadThreads(true); } },
      h("span", { html: icon(ic, 16) }), h("span", { class: "grow" }, name), u ? h("b", {}, String(u)) : null); })),
    M.account !== "all" && accts[0]?.user.length ? h("div", { class: "p-section", style: "margin:14px 8px 4px" }, "Labels") : null,
    ...(M.account !== "all" && accts[0] ? accts[0].user.map((l) => h("button", { type: "button", class: `mfold ${M.label === l.id ? "on" : ""}`, onclick: () => { M.label = l.id; M.open = null; paintNav(); paintReader(); loadThreads(true); } }, h("span", {}, "🏷"), h("span", { class: "grow" }, l.name))) : [])].filter(Boolean));   // (replaceChildren would print null as text)
}

async function loadLabels() {
  try { M.labels = await M.ctx.api.get(`${B}/labels?account=${enc(M.account)}`); } catch { /* counts are optional */ }
  paintNav();
  if (M.labels.errors?.length) banner(M.labels.errors[0]);
}
function banner(msg) { const t = M.el.querySelector(".mail-tools"); if (!t) return; t.querySelector(".mail-warn")?.remove(); if (msg) t.append(M.ctx.h("div", { class: "mail-warn" }, "⚠ " + msg)); }

async function loadThreads(reset) {
  const { api, h } = M.ctx, rows = M.el.querySelector(".mail-rows");
  if (reset) { M.threads = []; M.next = ""; rows.replaceChildren(h("div", { class: "skeleton", style: "margin:10px" }), h("div", { class: "skeleton", style: "margin:10px" }), h("div", { class: "skeleton", style: "margin:10px" })); }
  M.busy = true;
  try {
    const d = await api.get(`${B}/threads?account=${enc(M.account)}&label=${enc(M.label)}&q=${enc(M.q)}${reset ? "" : `&next=${enc(M.next)}`}`);
    M.threads = reset ? d.threads : M.threads.concat(d.threads); M.next = d.next; banner(d.errors?.[0]);
  } catch (e) { rows.replaceChildren(h("div", { class: "err", style: "margin:10px" }, e.message)); M.busy = false; return; }
  M.busy = false; paintRows(); paintBulk();
}

/* ------------------------------------------------------------ list */
function paintRows() {
  const { h } = M.ctx, rows = M.el.querySelector(".mail-rows"); if (!rows) return;
  const multi = M.account === "all" && M.accounts.length > 1;
  if (!M.threads.length) return rows.replaceChildren(h("div", { class: "p-empty", style: "padding:40px" }, M.q ? "No conversations match." : "Nothing here."));
  rows.replaceChildren(...M.threads.map((t) => {
    const who = t.from_me ? "me" : (t.from.name || t.from.email), a = acctOf(t.account);
    return h("div", { class: `mrow ${t.unread ? "unread" : ""} ${M.open?.ref === t.ref ? "open" : ""} ${M.sel.has(t.ref) ? "sel" : ""}`, role: "button", tabindex: 0, onclick: () => openThread(t), onkeydown: (e) => e.key === "Enter" && openThread(t) },
      h("input", { type: "checkbox", checked: M.sel.has(t.ref), "aria-label": "Select", onclick: (e) => e.stopPropagation(), onchange: (e) => { e.target.checked ? M.sel.add(t.ref) : M.sel.delete(t.ref); paintRows(); paintBulk(); } }),
      h("button", { type: "button", class: `mstar ${t.starred ? "on" : ""}`, "aria-label": t.starred ? "Unstar" : "Star", onclick: (e) => { e.stopPropagation(); t.starred = !t.starred; paintRows(); M.ctx.api.post(`${B}/action`, { refs: [t.ref], action: t.starred ? "star" : "unstar" }); }, html: icon("star", 15) }),
      h("div", { class: "mbody" },
        h("div", { class: "m1" }, multi && a && h("i", { class: "mdot", style: `background:${a.color}`, title: a.label }), h("span", { class: "mfrom" }, who), t.count > 1 && h("span", { class: "mcount" }, String(t.count)), h("span", { class: "mdate" }, fmtDate(t.date))),
        h("div", { class: "m2" }, h("span", { class: "msub" }, t.subject), h("span", { class: "msnip" }, " – " + t.snippet))),
      h("div", { class: "mhover" }, canManage(a) && h("button", { type: "button", class: "icon-btn sm", title: "Archive", "aria-label": "Archive", onclick: (e) => { e.stopPropagation(); act("archive", [t.ref]); }, html: icon("archive", 14) }),
        canManage(a) && h("button", { type: "button", class: "icon-btn sm", title: "Delete", "aria-label": "Delete", onclick: (e) => { e.stopPropagation(); act("trash", [t.ref]); }, html: icon("trash", 14) })));
  }), ...(M.next ? [h("button", { class: "btn sm", style: "margin:10px auto;display:block", onclick: () => loadThreads(false) }, "Load more")] : []));
}

function paintBulk() {
  const { h } = M.ctx, bar = M.el.querySelector(".mail-bulk"); if (!bar) return;
  bar.hidden = !M.sel.size; if (!M.sel.size) return;
  const refs = [...M.sel], b = (label, action, ic) => h("button", { type: "button", class: "btn sm", onclick: () => act(action, refs) }, ic ? h("span", { html: icon(ic, 14) }) : null, label);
  bar.replaceChildren(h("strong", {}, `${M.sel.size} selected`), h("div", { class: "grow" }), b("Archive", "archive", "archive"), b("Delete", "trash", "trash"), b("Spam", "spam", "spam"), b("Mark read", "mark_read"), b("Mark unread", "mark_unread"),
    h("button", { type: "button", class: "btn sm", onclick: () => { M.sel.clear(); paintRows(); paintBulk(); } }, "Clear"));
}

async function act(action, refs, closeAfter = false) {
  const { api, toast } = M.ctx;
  try {
    const r = await api.post(`${B}/action`, { refs, action });
    if (!r.ok) throw new Error(r.errors?.[0] || "That didn't work");
    const gone = ["archive", "trash", "spam"].includes(action) && !["SENT", "DRAFT"].includes(M.label) ? new Set(refs) : null;
    if (gone) { M.threads = M.threads.filter((t) => !gone.has(t.ref)); if (M.open && gone.has(M.open.ref)) closeReader(); }
    else M.threads.forEach((t) => { if (refs.includes(t.ref)) { if (action === "mark_read") t.unread = false; if (action === "mark_unread") t.unread = true; } });
    refs.forEach((x) => M.sel.delete(x));
    toast({ archive: "Archived", trash: "Moved to Trash", spam: "Marked as spam", mark_read: "Marked as read", mark_unread: "Marked as unread" }[action] || "Done");
  } catch (e) { toast(e.message, "error"); }
  paintRows(); paintBulk(); loadLabels();
}

/* ------------------------------------------------------------ reader */
function closeReader() { M.open = null; M.el.querySelector(".mail")?.classList.remove("reading"); paintReader(); paintRows(); }

async function openThread(t) {
  const { api, h } = M.ctx;
  M.open = { ref: t.ref, data: null }; t.unread = false; paintRows();
  M.el.querySelector(".mail").classList.add("reading");
  const pane = M.el.querySelector(".mail-read"); pane.replaceChildren(h("div", { class: "skeleton", style: "margin:20px" }), h("div", { class: "skeleton", style: "margin:20px" }));
  try { M.open.data = await api.get(`${B}/thread?ref=${enc(t.ref)}&mark_read=1`); } catch (e) { pane.replaceChildren(h("div", { class: "err", style: "margin:20px" }, e.message)); return; }
  paintReader(); loadLabels();
}

function paintReader() {
  const { h } = M.ctx, pane = M.el.querySelector(".mail-read"); if (!pane) return;
  const d = M.open?.data;
  if (!d) return pane.replaceChildren(h("div", { class: "mail-empty" }, h("span", { html: icon("mail", 40) }), h("div", {}, "Select a conversation")));
  const a = acctOf(d.account), manage = canManage(a);
  const tb = (label, ic, fn, disabled) => h("button", { type: "button", class: "icon-btn", title: label, "aria-label": label, disabled, onclick: fn, html: icon(ic, 17) });
  const last = d.messages[d.messages.length - 1];
  pane.replaceChildren(
    h("div", { class: "mr-bar" }, h("button", { type: "button", class: "btn sm back-narrow", onclick: closeReader }, "← Back"),
      tb("Archive", "archive", () => act("archive", [d.ref], true), !manage), tb("Delete", "trash", () => act("trash", [d.ref], true), !manage), tb("Report spam", "spam", () => act("spam", [d.ref], true), !manage),
      tb("Mark as unread", "mail", () => act("mark_unread", [d.ref]).then(closeReader), !manage), h("div", { class: "grow" }),
      h("button", { type: "button", class: "btn sm", onclick: () => M.ctx.prompt(`Summarize the email “${d.subject}” from ${last.from.name || last.from.email} (${d.account_label}). `) }, "Summarize with the agent")),
    h("div", { class: "mr-scroll" }, h("h2", { class: "mr-subject" }, d.subject, d.account_label && M.accounts.length > 1 && h("span", { class: "pill", style: `color:${a?.color}` }, d.account_label)),
      ...d.messages.map((m, i) => messageCard(m, d, i === d.messages.length - 1 || m.unread))));
}

function messageCard(m, d, expanded) {
  const { h } = M.ctx, a = acctOf(d.account), send = a?.level === "send";
  const card = h("div", { class: `mcard ${expanded ? "" : "collapsed"}` });
  const rcpt = (m.to.length ? "to " + m.to.map((x) => x.name || x.email).slice(0, 3).join(", ") : "") + (m.to.length > 3 ? ` +${m.to.length - 3}` : "");
  const head = h("div", { class: "mc-head", onclick: () => card.classList.toggle("collapsed") },
    h("div", { class: "avatar", style: `background:${hue(m.from.email)}` }, initial(m.from)),
    h("div", { class: "grow" }, h("div", {}, h("strong", {}, m.from.name || m.from.email), " ", h("span", { class: "p-s" }, `<${m.from.email}>`)), h("div", { class: "p-s mc-sub" }, expanded ? rcpt : m.snippet)),
    h("div", { class: "p-s" }, fmtDate(m.date, expanded)),
    send && h("button", { type: "button", class: "icon-btn sm", title: "Reply", "aria-label": "Reply", onclick: (e) => { e.stopPropagation(); reply(m, d, false); }, html: icon("reply", 15) }));
  const body = h("div", { class: "mc-body" });
  if (m.body_html) {
    const hasRemote = /<img[^>]+src=["']?https?:/i.test(m.body_html) || /url\(\s*["']?https?:/i.test(m.body_html), allow = M.showImages.has(m.id);
    const frame = h("iframe", { class: "mframe", title: "Email content", sandbox: "allow-same-origin allow-popups allow-popups-to-escape-sandbox" });
    frame.srcdoc = `<!doctype html><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src ${allow ? "https: http: data:" : "data:"}; style-src 'unsafe-inline'; font-src data:">`
      + `<base target="_blank"><style>html,body{margin:0;background:#fff;color:#202124;font:14px/1.5 -apple-system,system-ui,sans-serif;word-wrap:break-word}body{padding:12px 14px}img{max-width:100%;height:auto}a{color:#1a73e8}pre{white-space:pre-wrap}table{max-width:100%}</style>${m.body_html}`;
    frame.addEventListener("load", () => { try { const doc = frame.contentDocument; frame.style.height = Math.max(80, doc.documentElement.scrollHeight + 4) + "px"; } catch {} });
    body.append(hasRemote && !allow ? h("div", { class: "mc-img" }, "Images are hidden to protect your privacy. ", h("button", { type: "button", class: "btn sm", onclick: () => { M.showImages.add(m.id); paintReader(); } }, "Show images")) : null, frame);
  } else body.append(h("pre", { class: "mc-text" }, m.body_text || m.snippet));
  const atts = (m.attachments || []).length ? h("div", { class: "mc-atts" }, ...m.attachments.map((x) => h("a", { class: "attach", href: `${B}/attachment?account=${enc(d.account)}&message=${enc(m.id)}&id=${enc(x.id)}&name=${enc(x.name)}`, download: x.name, title: `${x.type}, ${(x.size / 1024).toFixed(1)} KB` },
    h("span", { html: icon("paperclip", 14) }), x.name))) : null;
  const foot = send ? h("div", { class: "mc-foot" }, h("button", { type: "button", class: "btn sm", onclick: () => reply(m, d, false) }, h("span", { html: icon("reply", 14) }), "Reply"),
    m.to.length + m.cc.length > 1 && h("button", { type: "button", class: "btn sm", onclick: () => reply(m, d, true) }, "Reply all"), h("button", { type: "button", class: "btn sm", onclick: () => forward(m, d) }, h("span", { html: icon("forward", 14) }), "Forward")) : null;
  card.append(head, h("div", { class: "mc-content" }, body, atts, foot));
  return card;
}

/* ------------------------------------------------------------ compose */
const quoteOf = (m) => `\n\nOn ${fmtDate(m.date, true)}, ${m.from.name || m.from.email} <${m.from.email}> wrote:\n` + (m.body_text || m.snippet).split("\n").slice(0, 60).map((l) => "> " + l).join("\n");
function reply(m, d, all) {
  const me = d.account.toLowerCase(), to = [m.reply_to || m.from.email], cc = all ? [...m.to, ...m.cc].map((x) => x.email).filter((e) => e.toLowerCase() !== me && !to.includes(e)) : [];
  compose({ account: d.account, to: to.join(", "), cc: cc.join(", "), subject: /^re:/i.test(m.subject) ? m.subject : `Re: ${m.subject}`, body: quoteOf(m), thread_id: m.thread_id, in_reply_to: m.message_id, references: m.references });
}
function forward(m, d) {
  compose({ account: d.account, subject: /^fwd?:/i.test(m.subject) ? m.subject : `Fwd: ${m.subject}`, body: `\n\n---------- Forwarded message ----------\nFrom: ${m.from.name} <${m.from.email}>\nDate: ${fmtDate(m.date, true)}\nSubject: ${m.subject}\n\n${m.body_text || m.snippet}` });
}

function compose(init) {
  const { h, api, toast } = M.ctx, senders = M.accounts.filter((a) => a.level === "send");
  if (!senders.length) return toast("No connected account can send mail. Change access in Settings → Accounts.", "error");
  const from = h("select", {}, ...senders.map((a) => h("option", { value: a.email, selected: a.email === (init.account || (M.account !== "all" ? M.account : "")) }, `${a.label} <${a.email}>`)));
  const to = h("input", { type: "text", placeholder: "To", value: init.to || "", autofocus: true }), cc = h("input", { type: "text", placeholder: "Cc", value: init.cc || "" }), bcc = h("input", { type: "text", placeholder: "Bcc" });
  const subj = h("input", { type: "text", placeholder: "Subject", value: init.subject || "" }), body = h("textarea", { placeholder: "Write your message…", rows: 12 }, init.body || "");
  const ccRow = h("div", { class: "ed-row", hidden: !init.cc && true }, "Cc", cc), bccRow = h("div", { class: "ed-row", hidden: true }, "Bcc", bcc), err = h("div", { class: "form-err" });
  const go = async (draft) => {
    err.textContent = ""; sendBtn.disabled = true;
    try {
      await api.post(`${B}/send`, { account: from.value, to: to.value, cc: cc.value, bcc: bcc.value, subject: subj.value, body: body.value, draft, thread_id: init.thread_id, in_reply_to: init.in_reply_to, references: init.references });
      d.close(); toast(draft ? "Draft saved" : "Sent"); if (!draft) { loadThreads(true); loadLabels(); }
    } catch (e) { err.textContent = e.message; sendBtn.disabled = false; }
  };
  const sendBtn = h("button", { class: "btn primary", onclick: () => go(false) }, "Send");
  const d = h("dialog", { class: "modal cal-dialog mail-compose-dlg" }, h("div", { class: "cd ed" }, h("div", { class: "cd-head" }, h("h3", {}, init.thread_id ? "Reply" : "New message"), h("div", { class: "grow" }), h("button", { class: "icon-btn", "aria-label": "Close", onclick: () => d.close(), html: "✕" })),
    h("div", { class: "ed-row" }, "From", from), h("div", { class: "ed-row" }, "To", to, h("button", { type: "button", class: "btn sm", onclick: () => { ccRow.hidden = false; bccRow.hidden = false; } }, "Cc/Bcc")), ccRow, bccRow, h("div", { class: "ed-row" }, "", subj), body, err,
    h("div", { class: "cd-foot" }, sendBtn, h("button", { class: "btn", onclick: () => go(true) }, "Save draft"), h("div", { class: "grow" }), h("button", { class: "btn", onclick: () => d.close() }, "Discard"))));
  d.addEventListener("close", () => d.remove()); document.body.append(d); d.showModal();
  d.addEventListener("keydown", (e) => { if ((e.metaKey || e.ctrlKey) && e.key === "Enter") go(false); });
  if (init.thread_id) setTimeout(() => { body.focus(); body.setSelectionRange(0, 0); }, 30);
}
