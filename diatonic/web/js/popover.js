// A small floating panel anchored to a button: closes on an outside click or Esc, and again when its button is clicked a second time.
import { h } from "./dom.js";

let current = null;

export function closePopover() { current?.close(); }

/** Opens `content` above `anchor`. Returns { el, close, place } or null when it toggled an open one shut. */
export function openPopover(anchor, content, { align = "center", below = false } = {}) {
  if (current && current.anchor === anchor) { current.close(); return null; }
  current?.close();
  const el = h("div", { class: "pop", role: "dialog" }, content);
  document.body.append(el);
  const place = () => {
    const a = anchor.getBoundingClientRect(), w = el.offsetWidth;
    let left = align === "right" ? a.right - w : align === "left" ? a.left : a.left + a.width / 2 - w / 2;
    left = Math.max(10, Math.min(left, innerWidth - w - 10));
    el.style.left = `${left}px`;
    if (below) { el.style.top = `${Math.min(a.bottom + 8, innerHeight - el.offsetHeight - 10)}px`; el.style.bottom = "auto"; }
    else el.style.bottom = `${innerHeight - a.top + 8}px`;
  };
  const onDown = (e) => { if (!el.contains(e.target) && !anchor.contains(e.target)) close(); };
  const onKey = (e) => { if (e.key === "Escape") { e.stopPropagation(); close(); anchor.focus?.(); } };
  function close() {
    document.removeEventListener("pointerdown", onDown, true);
    document.removeEventListener("keydown", onKey, true);
    el.remove();
    if (current?.el === el) current = null;
  }
  document.addEventListener("pointerdown", onDown, true);
  document.addEventListener("keydown", onKey, true);
  place();
  current = { anchor, el, close, place };
  return current;
}
