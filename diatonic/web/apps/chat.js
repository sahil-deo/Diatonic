// Chat panel: there is one chat in the app, and this panel shows it inside a workspace panel.

export const wide = false;

export async function render(el, ctx) {
  const mainEl = document.getElementById("main");
  if (!mainEl) return;
  mainEl.classList.remove("hidden");
  mainEl.style.cssText = "display: flex !important; flex-direction: column; width: 100%; height: 100%; min-width: 0; min-height: 0; position: relative; background: transparent;";
  el.replaceChildren(mainEl);

  ctx.onClose(() => {
    mainEl.classList.add("hidden");
    mainEl.style.cssText = "";
    const shell = document.getElementById("shell");
    const panel = document.getElementById("panel");
    if (shell && panel) shell.insertBefore(mainEl, panel);
  });
}
