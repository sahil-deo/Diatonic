// Chats panel: browses past conversations, searches chats, and launches new chats.
import { h, relTime } from "../js/dom.js";
import { icon } from "../js/icons.js";
import { state, emit, on } from "../js/store.js";
import * as chat from "../js/chat.js";
import { api } from "../js/api.js";

export const wide = false;

export async function render(el, ctx) {
  const { replaceTab } = ctx;
  let filterQuery = "";

  function indicator(id) {
    const r = state.runs[id];
    if (r) {
      return h("span", {
        class: `ind ${r.status === "waiting" ? "wait" : ""}`,
        title: r.status === "waiting" ? "Needs your approval" : "Working…",
        html: r.status === "waiting" ? icon("shield", 14) : '<span class="spin"></span>'
      });
    }
    if (state.unread.has(id)) {
      return h("span", { class: "ind unread", title: "New activity" });
    }
    return null;
  }

  async function openConv(id) {
    chat.openConversation(id);
    if (!document.querySelector("#messages") && replaceTab) {
      await replaceTab("chat", { conversationId: id });
    }
  }

  async function removeConv(e, id) {
    e.stopPropagation();
    await chat.removeConversation(id);
    await refresh();
  }

  async function startNew() {
    chat.newChat();
    if (!document.querySelector("#messages") && replaceTab) {
      await replaceTab("chat");
    }
  }

  function paint() {
    el.replaceChildren();

    const searchInput = h("input", {
      type: "search",
      class: "chats-search-input",
      placeholder: "Search past chats…",
      value: filterQuery,
      "aria-label": "Search past chats",
    });
    searchInput.addEventListener("input", () => {
      filterQuery = searchInput.value.toLowerCase().trim();
      renderList();
    });

    const newBtn = h("button", {
      class: "btn primary sm chats-new-btn",
      onclick: startNew,
      title: "New chat (⇧⌘O)"
    }, h("span", { html: icon("plus", 14) }), " New chat");

    const header = h("div", { class: "chats-header" },
      h("div", { class: "chats-search-box" },
        h("span", { class: "chats-search-icon", html: icon("search", 15) }),
        searchInput
      ),
      newBtn
    );

    const listContainer = h("div", { class: "chats-list", role: "list" });
    const wrap = h("div", { class: "chats-wrap" }, header, listContainer);
    el.append(wrap);

    renderList();
  }

  function renderList() {
    const list = el.querySelector(".chats-list");
    if (!list) return;

    let convs = state.conversations || [];
    if (filterQuery) {
      convs = convs.filter((c) => (c.title || "").toLowerCase().includes(filterQuery));
    }

    if (!convs.length) {
      list.replaceChildren(h("div", { class: "p-empty" }, filterQuery ? "No conversations match your search." : "No past conversations yet."));
      return;
    }

    const startOfDay = (d) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
    const today = startOfDay(new Date());
    const groups = { Today: [], Yesterday: [], "Previous 7 days": [], Older: [] };

    for (const c of convs) {
      const age = (today - startOfDay(new Date((c.updated_at || c.created_at || Date.now() / 1000) * 1000))) / 864e5;
      groups[age < 1 ? "Today" : age < 2 ? "Yesterday" : age < 8 ? "Previous 7 days" : "Older"].push(c);
    }

    const entries = Object.entries(groups).filter(([, v]) => v.length);
    list.replaceChildren(...entries.flatMap(([name, cs]) => [
      h("div", { class: "conv-group" }, name),
      ...cs.map((c) => {
        const isActive = c.id === state.convId;
        const timeStr = relTime(new Date((c.updated_at || c.created_at || Date.now() / 1000) * 1000).toISOString());
        return h("div", {
          class: `conv-item ${isActive ? "active" : ""}`,
          role: "listitem",
          tabindex: 0,
          onclick: () => openConv(c.id),
          onkeydown: (e) => { if (e.key === "Enter") openConv(c.id); }
        },
          h("div", { class: "conv-item-main" },
            h("span", { class: "conv-item-title" }, c.title || "Untitled chat"),
            h("span", { class: "conv-item-time" }, timeStr)
          ),
          indicator(c.id),
          h("button", {
            class: "conv-del-btn",
            "aria-label": "Delete conversation",
            title: "Delete",
            html: icon("trash", 13),
            onclick: (e) => removeConv(e, c.id)
          })
        );
      })
    ]));
  }

  async function refresh() {
    try {
      state.conversations = await api.conversations();
    } catch {}
    renderList();
  }

  paint();
  refresh();

  const off1 = on("conversations-changed", refresh);
  const off2 = on("runs-changed", renderList);
  const off3 = on("panel", renderList);

  ctx.onClose(() => {
    if (typeof off1 === "function") off1();
    if (typeof off2 === "function") off2();
    if (typeof off3 === "function") off3();
  });
}
