// Chat panel: renders chat messages and a composer inside any workspace panel.
import { h, esc, toast } from "../js/dom.js";
import { icon } from "../js/icons.js";
import { state, emit, on } from "../js/store.js";
import * as chat from "../js/chat.js";
import { api } from "../js/api.js";
import { render as md } from "../js/md.js";
import { renderHome } from "../js/home.js";

export const wide = false;

const instances = new WeakMap(); // state-ok: keyed by this panel's ctx

export async function render(el, ctx) {
  const { api: clientApi } = ctx;
  const mainEl = document.getElementById("main");

  // The first chat adopts the full-featured main chat element. Once another panel has it (it is no longer a child of the shell), extra chats are self-contained.
  if (mainEl && mainEl.parentElement?.id === "shell" && (!ctx.params?.conversationId || ctx.params.conversationId === state.convId)) {
    mainEl.classList.remove("hidden");
    mainEl.style.cssText = "display: flex !important; flex-direction: column; width: 100%; height: 100%; min-width: 0; min-height: 0; position: relative; background: var(--bg);";
    el.replaceChildren(mainEl);

    ctx.onClose(() => {
      mainEl.classList.add("hidden");
      mainEl.style.cssText = "";
      const shell = document.getElementById("shell");
      const panel = document.getElementById("panel");
      if (shell && panel) shell.insertBefore(mainEl, panel);
    });
    return;
  }

  el.replaceChildren();

  const wrap = h("div", { class: "chat-panel-wrap" });
  const msgs = h("div", { class: "messages chat-panel-messages" });
  const chip = h("div", { class: "ctx-chip", hidden: true });
  const comp = h("div", { class: "chat-panel-composer" });
  const input = h("textarea", { class: "chat-panel-input", placeholder: "Ask anything…", rows: 1 });
  const sendBtn = h("button", { class: "icon-btn send-btn", type: "button", title: "Send", html: icon("send", 16) });

  comp.append(input, sendBtn);
  wrap.append(msgs, chip, comp);
  el.append(wrap);

  input.ondragover = (e) => {
    if (e.dataTransfer.types.includes("text/diatonic-file") || e.dataTransfer.types.includes("text/diatonic-email") || e.dataTransfer.types.includes("text/plain")) {
      e.preventDefault();
      e.dataTransfer.dropEffect = "copy";
      comp.classList.add("drag-over");
    }
  };
  input.ondragleave = () => comp.classList.remove("drag-over");
  input.ondrop = (e) => {
    e.preventDefault();
    comp.classList.remove("drag-over");
    let insertText = "";
    try {
      const fileRaw = e.dataTransfer.getData("text/diatonic-file");
      const emailRaw = e.dataTransfer.getData("text/diatonic-email");
      if (fileRaw) {
        const f = JSON.parse(fileRaw);
        insertText = `@${f.ref || f.name} `;
      } else if (emailRaw) {
        const em = JSON.parse(emailRaw);
        insertText = `"${em.subject}" `;
      } else {
        insertText = e.dataTransfer.getData("text/plain") + " ";
      }
    } catch {
      insertText = e.dataTransfer.getData("text/plain") + " ";
    }
    if (insertText) {
      input.value = (input.value ? input.value + " " : "") + insertText;
      input.focus();
    }
  };

  let cid = ctx.params?.conversationId ?? ctx.state.get("cid", null);   // never state.convId: that belongs to the main chat
  const working = h("div", { class: "msg assistant" }, h("div", { class: "muted" }, "Working…"));

  async function loadConversation(id) {
    if (!id) {
      msgs.replaceChildren(h("div", { class: "empty" },
        h("h2", {}, "New chat"),
        h("p", { class: "muted" }, "Start a conversation or ask the agent to do something.")));
      return;
    }
    try {
      const c = await clientApi.get(`/api/conversations/${encodeURIComponent(id)}`);
      msgs.replaceChildren();
      for (const m of c.messages || []) {
        if (m.role === "user") {
          msgs.append(h("div", { class: "msg user" }, h("div", { class: "bubble" }, m.content)));
        } else {
          const mWrap = h("div", { class: "msg assistant" });
          if (m.content) mWrap.append(h("div", { class: "md", html: md(m.content) }));
          if (m.meta?.error) mWrap.append(h("div", { class: "err" }, m.meta.error));
          msgs.append(mWrap);
        }
      }
      msgs.scrollTop = msgs.scrollHeight;
    } catch (e) {
      msgs.replaceChildren(h("div", { class: "err", style: "margin:16px" }, e.message));
    }
  }

  const doSend = async () => {
    const text = input.value.trim();
    if (!text) return;
    if (cid && state.runs[cid]) return toast("This chat is still working. Wait for it to finish.");
    input.value = "";
    input.style.height = "auto";
    msgs.querySelector(".empty")?.remove();
    msgs.append(h("div", { class: "msg user" }, h("div", { class: "bubble" }, text)), working);
    msgs.scrollTop = msgs.scrollHeight;
    try {
      const r = await api.chat({ conversation_id: cid, message: text, speak: false, screen: null });
      cid = r.conversation_id;
      ctx.state.set("cid", cid);
      state.runs[cid] = state.runs[cid] || { status: r.run.status, title: r.title };
      emit("conversations-changed");
    } catch (e) {
      working.remove();
      input.value = text;
      toast(e.message, "error");
    }
  };

  sendBtn.onclick = doSend;
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      doSend();
    }
  });

  input.addEventListener("input", () => {
    input.style.height = "auto";
    input.style.height = Math.min(180, input.scrollHeight) + "px";
    input.style.overflowY = input.scrollHeight > 180 ? "auto" : "hidden";
  });

  // The shared event stream updates state.runs; when this chat's run ends, show the finished reply.
  let wasRunning = false;
  const offRuns = on("runs-changed", () => {
    const running = !!(cid && state.runs[cid]);
    if (wasRunning && !running) loadConversation(cid);
    wasRunning = running;
  });
  const offPrefill = on("prefill", (text) => {
    input.value = text;
    input.focus();
  });

  ctx.onClose(() => {
    offRuns();
    offPrefill();
  });

  await loadConversation(cid);
}
