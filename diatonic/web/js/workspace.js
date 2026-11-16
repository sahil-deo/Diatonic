// Workspace renderer: renders the panel tree (splits, tab stacks, dividers)
// into the layout workspace with per-instance panel lifecycles and state.
import { h, esc, toast } from "./dom.js";
import { icon } from "./icons.js";
import { state, emit, on } from "./store.js";
import { api } from "./api.js";
import { register } from "./commands.js";
import { registerList } from "./nav.js";
import { runPanel } from "./mods.js";
import { panelContext } from "./panel.js";
import { pickApp } from "./palette.js";
import {
  fromPreset,
  validate,
  openPanel as treeOpenPanel,
  closePanel as treeClosePanel,
  split as treeSplit,
  moveTab as treeMoveTab,
  focusNeighbor as treeFocusNeighbor,
  allTabs,
  findTab,
  findStack,
} from "./layout.js";

const STORAGE_KEY = "workspace:tree";
let currentTree = null;
const instances = new Map(); // tabId -> { tab, el, body, pctx, cleanups, mod }
let hostElement = null;

export function loadTree() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw) return validate(JSON.parse(raw));
  } catch {}
  return fromPreset("current");
}

export function saveTree(tree) {
  currentTree = validate(tree);
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(currentTree));
  } catch {}
  return currentTree;
}

export function getTree() {
  if (!currentTree) currentTree = loadTree();
  return currentTree;
}

export function ensureCustomAppStyles(appId, href) {
  let link = document.querySelector(`link[data-custom-app="${CSS.escape(appId)}"]`);
  if (!link) {
    link = document.createElement("link");
    link.rel = "stylesheet";
    link.dataset.customApp = appId;
    link.href = href;
    document.head.append(link);
  }
}

export function reloadAppTabs(appId) {
  const link = document.querySelector(`link[data-custom-app="${CSS.escape(appId)}"]`);
  if (link) {
    try {
      const url = new URL(link.href, location.origin);
      url.searchParams.set("v", Date.now());
      link.href = url.pathname + url.search;
    } catch {}
  }
  for (const [tabId, inst] of instances.entries()) {
    if (inst.tab.app === appId) {
      reloadTab(tabId, true);
    }
  }
}

export async function reloadTab(tabId, reimport = false) {
  const currentInst = instances.get(tabId);
  if (!currentInst) return;
  currentInst.pctx?.dispose?.();
  currentInst.body.replaceChildren();
  const appMeta = [...state.apps, ...(state.modApps || [])].find((a) => a.id === currentInst.tab.app);
  if (reimport || appMeta?.file_app) {
    try {
      const modPath = appMeta?.module || `/apps/${currentInst.tab.app}.js`;
      const url = new URL(modPath, location.origin);
      url.searchParams.set("v", Date.now());
      currentInst.mod = await import(url.pathname + url.search);
    } catch (e) {
      if (appMeta?.file_app) {
        api.post(`/api/custom-apps/${currentInst.tab.app}/logs`, {
          error: `Import failed: ${e.message}\n${e.stack || ""}`,
          source: "client_import",
        }).catch(() => {});
      }
    }
  }
  if (currentInst.mod) {
    try {
      await currentInst.mod.render(currentInst.body, currentInst.pctx);
      if (currentInst.tab.params && currentInst.mod.navigate) {
        await currentInst.mod.navigate(currentInst.tab.params, currentInst.pctx);
      }
    } catch (e) {
      if (appMeta?.file_app) {
        api.post(`/api/custom-apps/${currentInst.tab.app}/logs`, {
          error: `${e.message}\n${e.stack || ""}`,
          source: "client_render",
        }).catch(() => {});
      }
      currentInst.body.replaceChildren(h("div", { class: "p-empty" }, `Couldn't load ${currentInst.tab.app}: ${e.message}`));
    }
  }
}

async function mountTab(tab, container = null) {
  let inst = instances.get(tab.id);
  if (!inst) {
    const isFlush = tab.app === "shell" || tab.app === "presentations";
    const body = h("div", { class: `panel-body workspace-panel-body ${isFlush ? "flush" : ""}` });
    if (container && !container.contains(body)) container.append(body);
    const appMeta = tab.app === "chat"
      ? { id: "chat", name: "Chat", icon: "chat" }
      : tab.app === "launcher"
      ? { id: "launcher", name: "New Tab", icon: "plus" }
      : tab.app === "tiles" || tab.app === "home"
      ? { id: "tiles", name: "Home", icon: "layout" }
      : [...state.apps, ...(state.modApps || [])].find((a) => a.id === tab.app) || { id: tab.app, name: tab.app, icon: "box" };
    const appObj = {
      ...appMeta,
      instanceId: tab.id,
      reload: () => reloadTab(tab.id),
      replaceTab: (newAppId, params = {}) => replaceTab(tab.id, newAppId, params),
    };
    const pctx = panelContext(appObj, body, tab.params);
    inst = { tab, body, pctx, cleanups: [], mod: null };
    instances.set(tab.id, inst);

    try {
      let mod;
      if (tab.app === "chat") {
        mod = await import("/apps/chat.js").catch(() => import("../apps/chat.js"));
      } else if (tab.app === "launcher") {
        mod = await import("/apps/launcher.js").catch(() => import("../apps/launcher.js"));
      } else if (tab.app === "tiles" || tab.app === "home") {
        mod = await import("/apps/tiles.js").catch(() => import("../apps/tiles.js"));
      } else if (appMeta?.mod) {
        mod = { render: (el, c) => runPanel(appMeta.code, el, c) };
      } else {
        const modPath = appMeta?.module || (appMeta?.file_app ? `/custom-apps/${tab.app}/index.js` : `/apps/${tab.app}.js`);
        if (appMeta?.file_app || modPath.startsWith("/custom-apps/")) {
          ensureCustomAppStyles(tab.app, appMeta?.styles || `/custom-apps/${tab.app}/style.css`);
        }
        mod = await import(modPath).catch((err) => {
          if (!appMeta?.file_app) {
            ensureCustomAppStyles(tab.app, `/custom-apps/${tab.app}/style.css`);
            return import(`/custom-apps/${tab.app}/index.js`);
          }
          throw err;
        });
      }
      inst.mod = mod;
      body.classList.toggle("flush", !!mod.flush);
      await mod.render(body, pctx);
      if (tab.params && mod.navigate) await mod.navigate(tab.params, pctx);
    } catch (e) {
      if (appMeta?.file_app) {
        api.post(`/api/custom-apps/${tab.app}/logs`, {
          error: `${e.message}\n${e.stack || ""}`,
          source: "client_render",
        }).catch(() => {});
      }
      body.replaceChildren(h("div", { class: "p-empty" }, `Couldn't load ${tab.app}: ${e.message}`));
    }
  } else if (container && !container.contains(inst.body)) {
    container.append(inst.body);
  }
  return inst;
}

export function disposeTab(tabId) {
  const inst = instances.get(tabId);
  if (!inst) return;
  inst.pctx?.dispose?.();
  inst.cleanups.forEach((f) => { try { f(); } catch {} });
  inst.body.remove();
  instances.delete(tabId);
}

function renderNode(node, tree) {
  if (!node) {
    return h("div", { class: "workspace-empty" },
      h("div", { class: "workspace-empty-title" }, "No panels open"),
      h("p", { class: "muted" }, "Select an app from the left rail or press ⌘K for commands."),
      h("button", {
        class: "btn secondary",
        style: "margin-top: 12px;",
        type: "button",
        onclick: async () => {
          const id = await pickApp("Search app to open…");
          if (id) openPanel(id);
        },
      }, "Open an app")
    );
  }
  if (node.type === "split") {
    const isRow = node.dir === "row";
    const el = h("div", {
      class: `workspace-split workspace-split-${node.dir}`,
      style: `display: flex; flex-direction: ${isRow ? "row" : "column"}; width: 100%; height: 100%; overflow: hidden;`,
    });

    node.children.forEach((child, i) => {
      const sizePct = (node.sizes[i] || (1 / node.children.length)) * 100;
      const childWrap = h("div", {
        class: "workspace-child-wrap",
        style: `flex: 0 0 ${sizePct}%; overflow: hidden; display: flex; position: relative;`,
      }, renderNode(child, tree));
      el.append(childWrap);

      // Divider between children
      if (i < node.children.length - 1) {
        const divider = h("div", {
          class: `workspace-divider workspace-divider-${node.dir}`,
          style: isRow
            ? "width: 5px; cursor: col-resize; background: var(--border); z-index: 10; flex: none;"
            : "height: 5px; cursor: row-resize; background: var(--border); z-index: 10; flex: none;",
        });

        // Double click divider to equalize children sizes
        divider.addEventListener("dblclick", () => {
          const equal = 1 / node.children.length;
          node.sizes = node.children.map(() => equal);
          saveTree(tree);
          renderWorkspace(hostElement, tree);
        });

        divider.addEventListener("mousedown", (e) => {
          e.preventDefault();
          const startCoord = isRow ? e.clientX : e.clientY;
          const totalSize = isRow ? el.clientWidth : el.clientHeight;
          const origSizes = [...node.sizes];
          let currentSizes = origSizes;

          const onMove = (ev) => {
            const delta = (isRow ? ev.clientX : ev.clientY) - startCoord;
            const deltaFrac = delta / totalSize;
            const newSizes = [...origSizes];
            newSizes[i] = Math.max(0.05, origSizes[i] + deltaFrac);
            newSizes[i + 1] = Math.max(0.05, origSizes[i + 1] - deltaFrac);
            const sum = newSizes[i] + newSizes[i + 1];
            const targetSum = origSizes[i] + origSizes[i + 1];
            newSizes[i] = (newSizes[i] / sum) * targetSum;
            newSizes[i + 1] = (newSizes[i + 1] / sum) * targetSum;
            currentSizes = newSizes;

            const wraps = el.querySelectorAll(":scope > .workspace-child-wrap");
            if (wraps[i]) wraps[i].style.flex = `0 0 ${newSizes[i] * 100}%`;
            if (wraps[i + 1]) wraps[i + 1].style.flex = `0 0 ${newSizes[i + 1] * 100}%`;
          };

          const onUp = () => {
            removeEventListener("mousemove", onMove);
            removeEventListener("mouseup", onUp);
            node.sizes = currentSizes;
            saveTree(tree);
          };
          addEventListener("mousemove", onMove);
          addEventListener("mouseup", onUp);
        });

        el.append(divider);
      }
    });
    return el;
  }

  if (node.type === "stack") {
    const stackEl = h("div", { class: "workspace-stack", style: "display: flex; flex-direction: column; width: 100%; height: 100%; overflow: hidden; background: var(--surface);" });
    const tabbar = h("div", { class: "workspace-tabbar", style: "display: flex; align-items: center; border-bottom: 1px solid var(--border); background: var(--surface-2); min-height: 38px; height: 38px; padding: 0 6px; gap: 4px;" });
    const tabsEl = h("div", { class: "workspace-tabs" });   // scrolls on its own so the buttons on the right stay in reach
    const content = h("div", { class: "workspace-stack-content", style: "flex: 1; min-height: 0; position: relative; overflow: hidden;" });

    node.tabs.forEach((tab, idx) => {
      const isActive = idx === node.active;
      if (isActive && (!activeTabId || activeTabId === tab.id)) {
        activeTabId = tab.id;
        state.panelApp = tab.app;
      }
      const appMeta = tab.app === "chat"
        ? { id: "chat", name: "Chat", icon: "chat" }
        : tab.app === "launcher"
        ? { id: "launcher", name: "New Tab", icon: "plus" }
        : tab.app === "tiles" || tab.app === "home"
        ? { id: "tiles", name: "Home", icon: "layout" }
        : [...state.apps, ...(state.modApps || [])].find((a) => a.id === tab.app) || { id: tab.app, name: tab.app, icon: "box" };

      const tabBtn = h("div", {
        class: `workspace-tab ${isActive ? "active" : ""} ${tab.id === activeTabId ? "focused" : ""}`,
        title: appMeta.name || tab.app,
        draggable: "true",
        "data-tab-id": tab.id,
        "data-stack-id": node.id,
        onclick: () => {
          node.active = idx;
          activeTabId = tab.id;
          state.panelApp = tab.app;
          emit("panel");
          saveTree(tree);
          renderWorkspace(hostElement, tree);
        },
      },
        h("span", { html: icon(appMeta.icon || "box", 14) }),
        h("span", { class: "workspace-tab-title" }, appMeta.name || tab.app),
        h("button", {
          class: "icon-btn sm",
          type: "button",
          title: "Close",
          onclick: (e) => {
            e.stopPropagation();
            closePanel(tab.id);
          },
          html: icon("x", 12),
        })
      );

      tabBtn.ondragstart = (e) => {
        e.dataTransfer.setData("text/diatonic-tab", JSON.stringify({ tabId: tab.id, stackId: node.id }));
        e.dataTransfer.effectAllowed = "move";
        tabBtn.classList.add("dragging");
      };

      tabBtn.ondragend = () => {
        tabBtn.classList.remove("dragging");
        hostElement?.querySelectorAll(".drag-over").forEach((el) => el.classList.remove("drag-over"));
      };

      tabBtn.ondragover = (e) => {
        if (!e.dataTransfer.types.includes("text/diatonic-tab")) return;
        e.preventDefault();
        e.dataTransfer.dropEffect = "move";
        tabBtn.classList.add("drag-over");
      };

      tabBtn.ondragleave = () => {
        tabBtn.classList.remove("drag-over");
      };

      tabBtn.ondrop = (e) => {
        e.preventDefault();
        e.stopPropagation();
        tabBtn.classList.remove("drag-over");
        try {
          const raw = e.dataTransfer.getData("text/diatonic-tab");
          if (!raw) return;
          const { tabId: srcTabId } = JSON.parse(raw);
          if (srcTabId && srcTabId !== tab.id) {
            const currentTree = getTree();
            const nextTree = moveTab(currentTree, srcTabId, node.id, idx);
            applyTree(nextTree);
          }
        } catch {}
      };

      tabsEl.append(tabBtn);

      // Mount and show/hide panel body
      mountTab(tab, content).then((inst) => {
        if (!content.contains(inst.body)) content.append(inst.body);
        inst.body.hidden = !isActive;
        inst.body.style.width = "100%";
        inst.body.style.height = "100%";
      });
    });

    tabbar.ondragover = (e) => {
      if (!e.dataTransfer.types.includes("text/diatonic-tab")) return;
      e.preventDefault();
      e.dataTransfer.dropEffect = "move";
    };

    tabbar.ondrop = (e) => {
      if (e.target.closest(".workspace-tab")) return;
      e.preventDefault();
      try {
        const raw = e.dataTransfer.getData("text/diatonic-tab");
        if (!raw) return;
        const { tabId: srcTabId } = JSON.parse(raw);
        if (srcTabId) {
          const currentTree = getTree();
          const nextTree = moveTab(currentTree, srcTabId, node.id, -1);
          applyTree(nextTree);
        }
      } catch {}
    };

    // Tabbar controls: Detach, Split side-by-side, Split stacked, and Add tab
    const activeTab = node.tabs[node.active];
    const detachBtn = h("button", {
      class: "icon-btn sm",
      type: "button",
      title: "Detach to separate window",
      "aria-label": "Detach panel",
      onclick: () => {
        if (!activeTab) return;
        window.open(`/?app=${encodeURIComponent(activeTab.app)}`, "_blank", "width=850,height=650");
        closePanel(activeTab.id);
      },
      html: icon("popout", 14),
    });
    const splitRightBtn = h("button", {
      class: "icon-btn sm",
      type: "button",
      title: "Split side-by-side (⌘; w v)",
      "aria-label": "Split side-by-side",
      onclick: () => splitPanel(activeTab?.id, "row", "launcher"),
      html: icon("split-v", 14),
    });
    const splitDownBtn = h("button", {
      class: "icon-btn sm",
      type: "button",
      title: "Split stacked (⌘; w s)",
      "aria-label": "Split stacked",
      onclick: () => splitPanel(activeTab?.id, "col", "launcher"),
      html: icon("split-h", 14),
    });
    const plusBtn = h("button", {
      class: "icon-btn sm",
      type: "button",
      title: "Add tab in this panel",
      "aria-label": "Add tab in this panel",
      onclick: () => openPanel("launcher", {}, node.id),
      html: icon("plus", 14),
    });
    tabbar.append(tabsEl, detachBtn, splitRightBtn, splitDownBtn, plusBtn);
    tabsEl.querySelector(".workspace-tab.active")?.scrollIntoView?.({ block: "nearest", inline: "nearest" });

    // Edge drop zone indicator overlay on panel content
    const edgeOverlay = h("div", { class: "edge-drop-indicator", hidden: true });
    content.append(edgeOverlay);

    content.ondragover = (e) => {
      if (!e.dataTransfer.types.includes("text/diatonic-tab")) return;
      e.preventDefault();
      e.dataTransfer.dropEffect = "move";
      const rect = content.getBoundingClientRect();
      const x = e.clientX - rect.left;
      const y = e.clientY - rect.top;
      const w = rect.width;
      const h = rect.height;

      edgeOverlay.hidden = false;
      edgeOverlay.className = "edge-drop-indicator";
      if (x < w * 0.2) edgeOverlay.classList.add("edge-left");
      else if (x > w * 0.8) edgeOverlay.classList.add("edge-right");
      else if (y < h * 0.2) edgeOverlay.classList.add("edge-top");
      else if (y > h * 0.8) edgeOverlay.classList.add("edge-bottom");
      else edgeOverlay.hidden = true;
    };

    content.ondragleave = (e) => {
      if (!content.contains(e.relatedTarget)) edgeOverlay.hidden = true;
    };

    content.ondrop = (e) => {
      if (edgeOverlay.hidden) return;
      e.preventDefault();
      edgeOverlay.hidden = true;
      try {
        const raw = e.dataTransfer.getData("text/diatonic-tab");
        if (!raw) return;
        const { tabId: srcTabId } = JSON.parse(raw);
        const rect = content.getBoundingClientRect();
        const x = e.clientX - rect.left;
        const y = e.clientY - rect.top;
        const w = rect.width;
        const h = rect.height;

        let dir = null;
        if (x < w * 0.2 || x > w * 0.8) dir = "row";
        else if (y < h * 0.2 || y > h * 0.8) dir = "col";

        if (dir && srcTabId) {
          const currentTree = getTree();
          const found = findTab(currentTree, srcTabId);
          if (found && activeTab?.id && srcTabId !== activeTab.id) {
            splitPanel(activeTab.id, dir, found.tab.app, found.tab.params);
            closePanel(srcTabId);
          }
        }
      } catch {}
    };

    stackEl.append(tabbar, content);
    return stackEl;
  }

  return h("div");
}

let activeTabId = null;

export function getActiveTabId() {
  if (activeTabId) return activeTabId;
  const tree = getTree();
  const tabs = allTabs(tree);
  return tabs[0]?.id || null;
}

export async function promptAndSplit(dir = "row", targetTabId = null) {
  const dirName = dir === "row" ? "side-by-side" : "stacked";
  const id = await pickApp(`Split ${dirName}: search app to open…`);
  if (!id) return;
  const tabId = targetTabId || getActiveTabId();
  if (tabId) splitPanel(tabId, dir, id);
  else openPanel(id);
}

export async function splitActive(dir = "row", newAppId = null, params = {}) {
  let appId = newAppId;
  if (!appId) {
    const dirName = dir === "row" ? "side-by-side" : "stacked";
    appId = await pickApp(`Split ${dirName}: search app to open…`);
    if (!appId) return;
  }
  const currentId = getActiveTabId();
  if (currentId) splitPanel(currentId, dir, appId, params);
  else openPanel(appId, params);
}

export function closeActive() {
  const currentId = getActiveTabId();
  if (currentId) closePanel(currentId);
}

export function focusNeighbor(direction = "right") {
  const tree = getTree();
  const currentId = getActiveTabId();
  if (!currentId) return;
  const nextId = treeFocusNeighbor(tree, currentId, direction);
  if (nextId) focusTab(nextId);
}

export function focusTab(tabId) {
  const tree = getTree();
  const found = findTab(tree, tabId);
  if (!found) return;
  found.stack.active = found.index;
  activeTabId = tabId;
  state.panelApp = found.tab.app;
  emit("panel");
  emit("screen");
  saveTree(tree);
  renderWorkspace(hostElement, tree);
  const inst = instances.get(tabId);
  inst?.body?.focus?.();
}

/** Get structured context of the open item in the active or linked panel. */
export function getScreenContext(forTabId = null) {
  const tree = getTree();
  const tabs = allTabs(tree);
  if (!tabs.length) return null;

  let targetTab = null;
  if (forTabId) {
    const otherTabs = tabs.filter((t) => t.id !== forTabId && t.app !== "chat");
    targetTab = otherTabs.find((t) => t.id === activeTabId) || otherTabs[0];
  } else {
    const activeTab = tabs.find((t) => t.id === activeTabId);
    targetTab = (activeTab && activeTab.app !== "chat")
      ? activeTab
      : tabs.find((t) => t.app !== "chat") || activeTab;
  }
  if (!targetTab) return null;

  const inst = instances.get(targetTab.id);
  const item = inst?.mod?.current?.(inst.pctx);
  const appMeta = [...state.apps, ...(state.modApps || [])].find((a) => a.id === targetTab.app) || { id: targetTab.app, name: targetTab.app };
  return { app: targetTab.app, kind: "app", title: appMeta.name || targetTab.app, ...(item || {}) };
}

export function renderWorkspace(host, tree = null) {
  if (host) hostElement = host;
  if (!hostElement) hostElement = document.getElementById("workspace-host");
  if (!hostElement) return;

  const t = tree || getTree();
  // Clean up any disposed tabs from the instances Map
  const liveTabIds = new Set(allTabs(t).map((tab) => tab.id));
  for (const [id] of instances) {
    if (!liveTabIds.has(id)) disposeTab(id);
  }

  hostElement.replaceChildren(renderNode(t.root, t));
}

export function openPanel(appId, params = {}, targetStackId = null) {
  state.panelApp = appId;
  emit("panel");
  emit("screen");
  const tree = getTree();
  const tabs = allTabs(tree);
  const existing = appId === "launcher" ? null : tabs.find((t) => t.app === appId);
  if (existing && !targetStackId) {
    focusTab(existing.id);
    const inst = instances.get(existing.id);
    if (params && Object.keys(params).length && inst?.mod?.navigate) {
      try { inst.mod.navigate(params, inst.pctx); } catch {}
    }
    return;
  }
  const nextTree = treeOpenPanel(tree, { app: appId, params }, targetStackId);
  const newTabs = allTabs(nextTree);
  const newlyAdded = newTabs.find((t) => !tabs.some((old) => old.id === t.id));
  if (newlyAdded) activeTabId = newlyAdded.id;
  saveTree(nextTree);
  renderWorkspace(hostElement, nextTree);
}

export async function replaceTab(tabId, newAppId, params = {}) {
  const tree = getTree();
  const found = findTab(tree, tabId);
  if (!found) return;

  disposeTab(tabId);
  found.tab.app = newAppId.trim();
  found.tab.params = params || {};

  activeTabId = tabId;
  state.panelApp = found.tab.app;
  emit("panel");
  emit("screen");

  saveTree(tree);
  renderWorkspace(hostElement, tree);
}

export function closePanel(tabId) {
  const tree = getTree();
  const closingTab = allTabs(tree).find((t) => t.id === tabId);
  if (closingTab && closingTab.app && closingTab.app !== "launcher") {
    closedTabsHistory.push({
      app: closingTab.app,
      params: closingTab.params ? JSON.parse(JSON.stringify(closingTab.params)) : {},
      stackId: closingTab.stackId || null,
    });
    if (closedTabsHistory.length > 30) closedTabsHistory.shift();
  }

  const nextTree = treeClosePanel(tree, tabId);
  disposeTab(tabId);
  const tabs = allTabs(nextTree);
  if (activeTabId === tabId || !tabs.some((t) => t.id === activeTabId)) {
    activeTabId = tabs[0]?.id || null;
    state.panelApp = tabs[0]?.app || null;
    emit("panel");
    emit("screen");
  }
  saveTree(nextTree);
  renderWorkspace(hostElement, nextTree);
}

export function splitPanel(panelId, dir = "row", newAppId = null, params = {}) {
  if (!newAppId || !newAppId.trim()) return;
  const tree = getTree();
  const oldTabs = allTabs(tree);
  const nextTree = treeSplit(tree, panelId, dir, { app: newAppId.trim(), params });
  const newTabs = allTabs(nextTree);
  const newlyAdded = newTabs.find((t) => !oldTabs.some((old) => old.id === t.id));
  if (newlyAdded) {
    activeTabId = newlyAdded.id;
    state.panelApp = newlyAdded.app;
    emit("panel");
    emit("screen");
  }
  saveTree(nextTree);
  renderWorkspace(hostElement, nextTree);
}

const layoutUndoStack = [];
const layoutRedoStack = [];
const closedTabsHistory = [];

export function reopenClosedTab() {
  if (!closedTabsHistory.length) {
    if (typeof document !== "undefined") toast("No closed tabs to reopen", "error");
    return false;
  }
  const entry = closedTabsHistory.pop();
  openPanel(entry.app, entry.params || {}, entry.stackId || null);
  if (typeof document !== "undefined") toast(`Reopened ${entry.app}`);
  return true;
}

export function getClosedTabsHistory() {
  return [...closedTabsHistory];
}

export function applyTree(tree, recordHistory = true) {
  if (!tree) return;
  if (recordHistory && currentTree) {
    layoutUndoStack.push(JSON.parse(JSON.stringify(currentTree)));
    if (layoutUndoStack.length > 30) layoutUndoStack.shift();
    layoutRedoStack.length = 0;
  }
  const nextTabs = allTabs(tree);
  for (const [id, inst] of instances.entries()) {
    if (!nextTabs.some((t) => t.id === id)) {
      inst.pctx?.dispose?.();
      instances.delete(id);
    }
  }
  activeTabId = nextTabs[0]?.id || null;
  state.panelApp = nextTabs[0]?.app || null;
  emit("panel");
  emit("screen");
  saveTree(tree);
  renderWorkspace(hostElement, tree);
}

export function undoLayout() {
  if (!layoutUndoStack.length) {
    toast("Nothing to undo", "error");
    return false;
  }
  const prev = layoutUndoStack.pop();
  if (currentTree) layoutRedoStack.push(JSON.parse(JSON.stringify(currentTree)));
  applyTree(prev, false);
  toast("Undid layout change");
  return true;
}

export function redoLayout() {
  if (!layoutRedoStack.length) {
    toast("Nothing to redo", "error");
    return false;
  }
  const next = layoutRedoStack.pop();
  if (currentTree) layoutUndoStack.push(JSON.parse(JSON.stringify(currentTree)));
  applyTree(next, false);
  toast("Redid layout change");
  return true;
}

export function applyPreset(presetName) {
  const tree = fromPreset(presetName);
  applyTree(tree);
}

export function getOpenTabs() {
  const tree = getTree();
  return allTabs(tree).map((t) => {
    const meta = t.app === "chat"
      ? { id: "chat", name: "Chat", icon: "chat" }
      : t.app === "launcher"
      ? { id: "launcher", name: "New Tab", icon: "plus" }
      : t.app === "tiles" || t.app === "home"
      ? { id: "tiles", name: "Home", icon: "layout" }
      : [...state.apps, ...(state.modApps || [])].find((a) => a.id === t.app) || { id: t.app, name: t.app, icon: "box" };
    return {
      tabId: t.id,
      appId: t.app,
      title: meta.name || t.app,
      icon: meta.icon || "box",
      stackId: t.stackId,
    };
  });
}

const LAYOUT_ACTIVE_KEY = "diatonic.layout.active";
let layoutsList = [];

export function getLayouts() {
  return layoutsList;
}

export function getActiveLayoutId() {
  try {
    return localStorage.getItem(LAYOUT_ACTIVE_KEY) || null;
  } catch {
    return null;
  }
}

export async function loadLayouts() {
  try {
    const res = await api.get("/api/layouts");
    layoutsList = res.layouts || [];
    emit("layouts", layoutsList);
    return layoutsList;
  } catch (e) {
    console.warn("Failed to load layouts:", e);
    return [];
  }
}

export async function saveLayouts(list) {
  try {
    const res = await api.put("/api/layouts", list);
    layoutsList = res.layouts || [];
    emit("layouts", layoutsList);
    return layoutsList;
  } catch (e) {
    toast(e.message, "error");
    throw e;
  }
}

export async function switchLayout(idOrName) {
  const target = String(idOrName || "").toLowerCase().trim();
  const ws = layoutsList.find((w) => w.id.toLowerCase() === target || w.name.toLowerCase() === target);
  if (!ws) {
    toast(`No layout "${idOrName}" found`, "error");
    return false;
  }
  try {
    localStorage.setItem(LAYOUT_ACTIVE_KEY, ws.id);
  } catch {}
  applyTree(ws.tree);
  emit("layout:switched", { layout: ws });
  toast(`Switched to layout: ${ws.name}`);
  return true;
}

export async function saveCurrentLayout(name, description = "") {
  const layoutName = String(name || "").trim() || "Layout";
  const wid = layoutName.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "");
  const tree = getTree();
  const newLayout = {
    id: wid || `ws_${Date.now()}`,
    name: layoutName,
    description: description || "Saved layout",
    tree,
    created_at: Date.now() / 1000,
  };
  const nextList = [...layoutsList.filter((w) => w.id !== newLayout.id), newLayout];
  await saveLayouts(nextList);
  try {
    localStorage.setItem(LAYOUT_ACTIVE_KEY, newLayout.id);
  } catch {}
  emit("layout:switched", { layout: newLayout });
  toast(`Saved layout "${newLayout.name}"`);
  return newLayout;
}

export async function deleteLayout(idOrName) {
  const target = String(idOrName || "").toLowerCase().trim();
  const found = layoutsList.find((w) => w.id.toLowerCase() === target || w.name.toLowerCase() === target);
  if (!found) {
    toast(`No layout "${idOrName}" found`, "error");
    return false;
  }
  const nextList = layoutsList.filter((w) => w.id !== found.id);
  await saveLayouts(nextList);
  if (getActiveLayoutId() === found.id) {
    try { localStorage.removeItem(LAYOUT_ACTIVE_KEY); } catch {}
  }
  toast(`Deleted layout "${found.name}"`);
  return true;
}

export function initWorkspace(host) {
  hostElement = host;
  state.getScreenContext = getScreenContext;

  loadLayouts().then((list) => {
    const activeId = getActiveLayoutId();
    if (activeId) {
      const match = list.find((w) => w.id === activeId);
      if (match?.tree) {
        applyTree(match.tree);
        return;
      }
    }
    const tree = getTree();
    renderWorkspace(hostElement, tree);
  }).catch(() => {
    const tree = getTree();
    renderWorkspace(hostElement, tree);
  });

  on("layouts_changed", loadLayouts);
  on("undo_layout", (ev) => {
    if (ev?.tree) applyTree(ev.tree);
    else undoLayout();
  });
  on("switch_layout", (ev) => {
    if (ev?.layout_id) switchLayout(ev.layout_id);
    else if (ev?.tree) applyTree(ev.tree);
  });
  on("custom_app_changed", async (ev) => {
    try {
      const apps = await api.get("/api/apps");
      if (Array.isArray(apps)) state.apps = apps;
    } catch {}
    emit("apps");
    if (ev?.id) reloadAppTabs(ev.id);
  });

  return {
    openPanel,
    closePanel,
    splitPanel,
    splitActive,
    replaceTab,
    promptAndSplit,
    closeActive,
    focusNeighbor,
    focusTab,
    applyPreset,
    applyTree,
    undoLayout,
    redoLayout,
    reopenClosedTab,
    getClosedTabsHistory,
    getLayouts,
    switchLayout,
    saveCurrentLayout,
    deleteLayout,
    getActiveLayoutId,
    getOpenTabs,
    getActiveTabId,
    getScreenContext,
    render: () => renderWorkspace(hostElement, getTree()),
  };
}
