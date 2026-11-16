// Launcher: browser-style new-tab page for quick app access, search, and recents.
import { icon } from "/js/icons.js";
import { state } from "/js/store.js";
import { openSettings } from "/js/settings.js";

export const wide = true;

const PRIMARY_APPS = [
  { id: "tiles", name: "Home", icon: "layout", desc: "Live tiles from your apps" },
  { id: "chat", name: "Chat", icon: "chat", desc: "AI assistant conversation" },
  { id: "files", name: "Files", icon: "folder", desc: "Explore code and documents" },
  { id: "notes", name: "Notes", icon: "file-text", desc: "Quick notes and thoughts" },
  { id: "gcal", name: "Calendar", icon: "calendar", desc: "Schedules and meetings" },
  { id: "gmail", name: "Mail", icon: "mail", desc: "Email inbox and reader" },
  { id: "shell", name: "Terminal", icon: "command", desc: "Interactive command-line" },
  { id: "presentations", name: "Presentations", icon: "presentation", desc: "Markdown slide decks" },
  { id: "todos", name: "To-Dos", icon: "check", desc: "Tasks and checklists" },
  { id: "telemetry", name: "Telemetry", icon: "activity", desc: "Model usage and timings" },
  { id: "settings", name: "Settings", icon: "sliders", desc: "Preferences, keys, models" },
];

export async function render(el, ctx) {
  const { h, replaceTab } = ctx;

  const wrap = h("div", { class: "launcher-wrap" });
  const searchInput = h("input", {
    type: "search",
    class: "launcher-search",
    placeholder: "Open an app, file, or conversation…",
    "aria-label": "Search apps and conversations",
    autofocus: true,
  });

  const searchBox = h("div", { class: "launcher-search-box" },
    h("span", { class: "launcher-search-icon", html: icon("search", 18) }),
    searchInput
  );

  const appsList = h("div", { class: "launcher-grid" });
  const recentsList = h("div", { class: "launcher-recents" });
  const emptyState = h("div", { class: "p-empty", hidden: true }, "No matching apps or conversations");

  wrap.append(
    h("div", { class: "launcher-header" },
      h("h2", { class: "launcher-title" }, "New Tab"),
      searchBox
    ),
    h("div", { class: "launcher-section" },
      h("div", { class: "launcher-section-title" }, "Apps"),
      appsList
    ),
    h("div", { class: "launcher-section", id: "launcher-recents-sec" },
      h("div", { class: "launcher-section-title" }, "Recent Conversations"),
      recentsList
    ),
    emptyState
  );

  el.replaceChildren(wrap);

  // Compile all available apps
  const allApps = [...PRIMARY_APPS];
  const customApps = [...(state.apps || []), ...(state.modApps || [])].filter((a) => a.enabled && a.panel);
  for (const a of customApps) {
    if (!allApps.some((x) => x.id === a.id)) {
      allApps.push({
        id: a.id,
        name: a.name || a.id,
        icon: a.icon || "box",
        desc: a.description || "Custom panel app",
      });
    }
  }

  let selectedIdx = 0;
  let currentItems = [];

  const launchItem = async (it, ev = null) => {
    const isSplitRow = ev && (ev.metaKey || ev.ctrlKey);
    const isSplitCol = ev && ev.shiftKey;

    if (it.kind === "settings") {
      openSettings();
      return;
    }

    if (isSplitRow || isSplitCol) {
      const ws = await import("/js/workspace.js");
      await ws.splitActive(isSplitRow ? "row" : "col", it.appId, it.params || {});
      return;
    }

    if (replaceTab) {
      replaceTab(it.appId, it.params || {});
    } else {
      const ws = await import("/js/workspace.js");
      ws.openPanel(it.appId, it.params || {});
    }
  };

  const renderItems = () => {
    const q = searchInput.value.trim().toLowerCase();
    appsList.replaceChildren();
    recentsList.replaceChildren();
    currentItems = [];

    // Filter apps
    const matchedApps = allApps.filter((a) => !q || a.name.toLowerCase().includes(q) || a.id.toLowerCase().includes(q) || (a.desc && a.desc.toLowerCase().includes(q)));
    matchedApps.forEach((a) => {
      currentItems.push({
        kind: a.id === "settings" ? "settings" : "app",
        appId: a.id,
        label: a.name,
        icon: a.icon,
        desc: a.desc,
      });
    });

    matchedApps.forEach((a) => {
      const cardIdx = currentItems.findIndex((it) => it.appId === a.id);
      const isSel = cardIdx === selectedIdx;
      const card = h("button", {
        type: "button",
        class: `launcher-card ${isSel ? "sel" : ""}`,
        title: `${a.name} (Enter to open, ⌘Enter to split)`,
        onclick: (ev) => launchItem(currentItems[cardIdx], ev),
      },
        h("div", { class: "launcher-card-icon", html: icon(a.icon || "box", 22) }),
        h("div", { class: "launcher-card-info" },
          h("div", { class: "launcher-card-title" }, a.name),
          h("div", { class: "launcher-card-desc" }, a.desc || "")
        )
      );
      appsList.append(card);
    });

    // Filter conversations
    const convs = (state.conversations || []).slice(0, 6);
    const matchedConvs = convs.filter((c) => !q || c.title.toLowerCase().includes(q));
    const recentsSec = wrap.querySelector("#launcher-recents-sec");
    if (!matchedConvs.length) {
      if (recentsSec) recentsSec.hidden = true;
    } else {
      if (recentsSec) recentsSec.hidden = false;
      matchedConvs.forEach((c) => {
        const itemIdx = currentItems.length;
        currentItems.push({
          kind: "conv",
          appId: "chat",
          params: { conversationId: c.id },
          label: c.title,
          icon: "chat",
        });
        const isSel = itemIdx === selectedIdx;
        const row = h("button", {
          type: "button",
          class: `launcher-recent-row ${isSel ? "sel" : ""}`,
          onclick: (ev) => launchItem(currentItems[itemIdx], ev),
        },
          h("span", { class: "launcher-recent-icon", html: icon("chat", 15) }),
          h("span", { class: "launcher-recent-title" }, c.title || "Untitled conversation")
        );
        recentsList.append(row);
      });
    }

    const hasMatches = currentItems.length > 0;
    emptyState.hidden = hasMatches;
    if (selectedIdx >= currentItems.length) selectedIdx = Math.max(0, currentItems.length - 1);
  };

  searchInput.addEventListener("input", () => {
    selectedIdx = 0;
    renderItems();
  });

  searchInput.addEventListener("keydown", (e) => {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      if (currentItems.length) {
        selectedIdx = (selectedIdx + 1) % currentItems.length;
        renderItems();
      }
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      if (currentItems.length) {
        selectedIdx = (selectedIdx - 1 + currentItems.length) % currentItems.length;
        renderItems();
      }
    } else if (e.key === "Enter" && currentItems[selectedIdx]) {
      e.preventDefault();
      launchItem(currentItems[selectedIdx], e);
    } else if (e.key === "Escape") {
      if (searchInput.value) {
        e.preventDefault();
        searchInput.value = "";
        selectedIdx = 0;
        renderItems();
      }
    }
  });

  renderItems();
  queueMicrotask(() => searchInput.focus());
}
