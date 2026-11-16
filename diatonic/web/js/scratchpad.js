// Scratchpad: dropdown overlay drawer for quick notes, fast chat, or a quick terminal.
import { h, $ } from "./dom.js";
import { icon } from "./icons.js";

let drawerEl = null;
let activeMode = "notes"; // "notes" | "chat"

const STORAGE_KEY = "diatonic.scratchpad.content";

function getDrawer() {
  if (drawerEl && document.body.contains(drawerEl)) return drawerEl;

  drawerEl = h("div", { class: "scratchpad-drawer", hidden: true });

  const header = h("div", { class: "scratchpad-header" });
  const tabs = h("div", { class: "scratchpad-tabs" });

  const notesTab = h("button", {
    class: `scratchpad-tab ${activeMode === "notes" ? "active" : ""}`,
    type: "button",
    onclick: () => switchMode("notes"),
  }, "Quick Notes");

  const closeBtn = h("button", {
    class: "icon-btn sm",
    type: "button",
    title: "Close (Esc)",
    "aria-label": "Close scratchpad",
    onclick: closeScratchpad,
    html: icon("x", 14),
  });

  tabs.append(notesTab);
  header.append(tabs, h("div", { class: "grow" }), closeBtn);

  const body = h("div", { class: "scratchpad-body" });

  const textarea = h("textarea", {
    class: "scratchpad-textarea",
    placeholder: "Quick thoughts, snippets, temporary scratchpad… (saved automatically)",
  });
  textarea.value = localStorage.getItem(STORAGE_KEY) || "";
  textarea.addEventListener("input", () => {
    localStorage.setItem(STORAGE_KEY, textarea.value);
  });

  body.append(textarea);
  drawerEl.append(header, body);
  document.body.append(drawerEl);

  drawerEl.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      e.stopPropagation();
      closeScratchpad();
    }
  });

  return drawerEl;
}

function switchMode(mode) {
  activeMode = mode;
  const d = getDrawer();
  d.querySelectorAll(".scratchpad-tab").forEach((t) => {
    t.classList.toggle("active", t.textContent.toLowerCase().includes(mode));
  });
}

export function openScratchpad(mode = "notes") {
  const d = getDrawer();
  switchMode(mode);
  d.hidden = false;
  d.classList.add("open");
  const textarea = d.querySelector(".scratchpad-textarea");
  setTimeout(() => textarea?.focus(), 50);
}

export function closeScratchpad() {
  if (!drawerEl) return;
  drawerEl.classList.remove("open");
  drawerEl.hidden = true;
}

export function toggleScratchpad(mode = "notes") {
  const d = getDrawer();
  if (d.hidden || !d.classList.contains("open")) {
    openScratchpad(mode);
  } else {
    closeScratchpad();
  }
}
