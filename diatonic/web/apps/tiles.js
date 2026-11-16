// Tiles panel: responsive dashboard of live snapshot tiles from enabled apps.
import { icon } from "/js/icons.js";
import { on } from "/js/store.js";

export const wide = true;

export async function render(el, ctx) {
  const { h, api, openPanel, state: panelState, onClose } = ctx;

  el.replaceChildren();

  const wrap = h("div", { class: "tiles-panel-wrap" });
  const toolbar = h("div", { class: "tiles-toolbar" });
  const grid = h("div", { class: "tiles-grid" });

  wrap.append(toolbar, grid);
  el.append(wrap);

  let catalog = [];
  try {
    catalog = await api.get("/api/home");
  } catch {
    catalog = [];
  }

  if (!catalog.length) {
    grid.replaceChildren(
      h("div", { class: "tiles-empty" }, "No enabled apps offer live snapshot tiles.")
    );
    return;
  }

  // Load saved tile order or use default
  const savedOrder = panelState.get("tile_order", []);
  let orderedApps = [];
  const catalogMap = new Map(catalog.map((a) => [a.id, a]));

  for (const id of savedOrder) {
    if (catalogMap.has(id)) {
      orderedApps.push(catalogMap.get(id));
      catalogMap.delete(id);
    }
  }
  for (const a of catalogMap.values()) {
    orderedApps.push(a);
  }

  const tilePainters = new Map();

  // Toolbar
  const title = h("h2", { class: "tiles-title" }, "Home Tiles");
  const countSpan = h("span", { class: "tiles-count muted" }, `${orderedApps.length} apps`);
  const refreshAllBtn = h("button", {
    class: "btn sm",
    type: "button",
    onclick: () => {
      tilePainters.forEach((paint) => paint(true));
    },
    html: `${icon("refresh", 13)} <span>Refresh all</span>`,
  });

  toolbar.append(title, countSpan, h("div", { class: "grow" }), refreshAllBtn);

  // Drag and drop state for tile reordering
  let draggedTileId = null;

  function renderGrid() {
    grid.replaceChildren();
    tilePainters.clear();

    orderedApps.forEach((app, index) => {
      const card = h("section", {
        class: "tile-card",
        draggable: "true",
        "data-tile-id": app.id,
      });

      // Drag events on card
      card.ondragstart = (e) => {
        draggedTileId = app.id;
        card.classList.add("dragging");
        e.dataTransfer.setData("text/diatonic-tile", app.id);
        e.dataTransfer.effectAllowed = "move";
      };

      card.ondragend = () => {
        card.classList.remove("dragging");
        grid.querySelectorAll(".drag-over").forEach((c) => c.classList.remove("drag-over"));
        draggedTileId = null;
      };

      card.ondragover = (e) => {
        if (!draggedTileId || draggedTileId === app.id) return;
        e.preventDefault();
        e.dataTransfer.dropEffect = "move";
        card.classList.add("drag-over");
      };

      card.ondragleave = () => {
        card.classList.remove("drag-over");
      };

      card.ondrop = (e) => {
        e.preventDefault();
        card.classList.remove("drag-over");
        const srcId = e.dataTransfer.getData("text/diatonic-tile") || draggedTileId;
        if (!srcId || srcId === app.id) return;

        const srcIdx = orderedApps.findIndex((a) => a.id === srcId);
        const destIdx = index;
        if (srcIdx !== -1 && destIdx !== -1) {
          const [moved] = orderedApps.splice(srcIdx, 1);
          orderedApps.splice(destIdx, 0, moved);
          panelState.set("tile_order", orderedApps.map((a) => a.id));
          renderGrid();
        }
      };

      // Header
      const header = h("header", { class: "tile-card-header" });
      const iconSpan = h("span", { class: "tile-card-icon", html: icon(app.icon || "box", 16) });
      const nameBtn = h("button", {
        class: "tile-card-name",
        type: "button",
        title: `Open ${app.name}`,
        onclick: () => openPanel(app.id),
      }, app.name);

      const refreshBtn = h("button", {
        class: "icon-btn sm",
        type: "button",
        title: "Refresh",
        "aria-label": "Refresh",
        onclick: () => paint(true),
        html: icon("refresh", 13),
      });

      const openAppBtn = h("button", {
        class: "icon-btn sm",
        type: "button",
        title: `Open ${app.name}`,
        "aria-label": `Open ${app.name}`,
        onclick: () => openPanel(app.id),
        html: icon("maximize", 13),
      });

      header.append(iconSpan, nameBtn, h("div", { class: "grow" }), refreshBtn, openAppBtn);

      const bodyEl = h("div", { class: "tile-card-body" });
      card.append(header, bodyEl);
      grid.append(card);

      const paint = async (fresh = false) => {
        if (!bodyEl.childElementCount) {
          bodyEl.replaceChildren(
            h("div", { class: "skeleton", style: "height: 20px; margin-bottom: 8px;" }),
            h("div", { class: "skeleton", style: "height: 40px;" })
          );
        }

        let d;
        try {
          d = await api.get(`/api/home/${encodeURIComponent(app.id)}?n=6${fresh ? "&fresh=1" : ""}`);
        } catch (err) {
          d = { error: err.message };
        }

        if (d.error) {
          bodyEl.replaceChildren(h("div", { class: "tile-err" }, d.error));
          return;
        }

        const openItem = (it) => {
          if (it.url) window.open(it.url, "_blank", "noopener");
          else openPanel(app.id, it.open || {});
        };

        const children = [];

        // Stats
        if (d.stats?.length) {
          children.push(
            h("div", { class: "tile-stats" }, ...d.stats.map((s) =>
              h("div", { class: "tile-stat-item" },
                h("b", {}, String(s.value)),
                h("span", {}, s.label)
              )
            ))
          );
        }

        // Items
        if (d.items?.length) {
          const itemsList = h("div", { class: "tile-items-list" });
          for (const it of d.items) {
            const row = h("button", {
              type: "button",
              class: `tile-row ${it.tone || ""} ${it.done ? "done" : ""}`,
              onclick: () => openItem(it),
            },
              h("i", { class: "tile-dot" }),
              h("span", { class: "tile-t" }, it.text),
              it.badge ? h("span", { class: "tile-badge" }, it.badge) : null,
              it.sub ? h("span", { class: "tile-sub" }, it.sub) : null
            );
            itemsList.append(row);
          }
          children.push(itemsList);
        } else {
          children.push(h("div", { class: "p-s tile-empty" }, d.empty ?? "Nothing here."));
        }

        if (d.more) {
          children.push(h("div", { class: "p-s tile-more" }, d.more));
        }

        bodyEl.replaceChildren(...children);
      };

      tilePainters.set(app.id, paint);
      paint(false);
    });
  }

  renderGrid();

  // Auto refresh every 2 minutes
  const timer = setInterval(() => {
    if (!el.isConnected) return clearInterval(timer);
    tilePainters.forEach((paint) => paint(false));
  }, 120000);

  // SSE event updates
  const offAppChange = on("app_changed", (ev) => {
    if (ev?.app && tilePainters.has(ev.app)) {
      tilePainters.get(ev.app)(true);
    }
  });

  onClose(() => {
    clearInterval(timer);
    if (typeof offAppChange === "function") offAppChange();
  });
}
