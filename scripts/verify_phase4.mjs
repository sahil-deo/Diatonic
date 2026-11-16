// Verify Phase 4 file apps, hot reload, and custom tools in headless Chrome
import { open } from "./cdp.mjs";

const PORT = 5071;
const BASE = `http://localhost:${PORT}`;

async function main() {
  console.log("1. Creating custom file app via API...");
  const createRes = await fetch(`${BASE}/api/custom-apps`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Diatonic": "1" },
    body: JSON.stringify({
      id: "counter-widget",
      name: "Live Counter",
      description: "Interactive counter for Phase 4 verification",
      icon: "clock",
    }),
  });
  if (!createRes.ok) throw new Error(`Create app failed: ${await createRes.text()}`);

  console.log("2. Writing custom code into index.js...");
  const indexJs = `
export async function render(el, ctx) {
  el.replaceChildren();
  const count = ctx.state.get("c", 0);
  const title = ctx.h("h2", { class: "counter-title" }, "Counter Value: " + count);
  const btn = ctx.h("button", {
    class: "btn counter-btn",
    onclick: async () => {
      ctx.state.set("c", count + 1);
      await ctx.reload();
    }
  }, "Increment");
  el.append(ctx.h("div", { class: "counter-box" }, title, btn));
}
`;
  const styleCss = `
.counter-box { padding: 24px; border: 2px dashed var(--accent, #7c9cff); }
.counter-title { color: var(--accent, #7c9cff); margin-bottom: 12px; }
`;
  await fetch(`${BASE}/api/custom-apps/counter-widget/file`, {
    method: "PUT",
    headers: { "Content-Type": "application/json", "X-Diatonic": "1" },
    body: JSON.stringify({ path: "index.js", content: indexJs }),
  });
  await fetch(`${BASE}/api/custom-apps/counter-widget/file`, {
    method: "PUT",
    headers: { "Content-Type": "application/json", "X-Diatonic": "1" },
    body: JSON.stringify({ path: "style.css", content: styleCss }),
  });

  console.log("3. Opening page in scratch Chrome...");
  const page = await open(BASE, 9333);
  await page.wait(1500);

  console.log("4. Opening custom app panel in workspace...");
  const openRes = await page.ev(`
    (async () => {
      const { openPanel } = await import("./js/panel.js");
      await openPanel("counter-widget");
      return true;
    })()
  `);
  console.log("Open panel result:", openRes);
  await page.wait(1000);

  console.log("5. Checking DOM elements and styling...");
  const domCheck = await page.ev(`
    (() => {
      const title = document.querySelector(".counter-title");
      const btn = document.querySelector(".counter-btn");
      const link = document.querySelector('link[data-custom-app="counter-widget"]');
      return {
        titleText: title?.textContent || null,
        hasBtn: !!btn,
        hasLink: !!link,
      };
    })()
  `);
  console.log("DOM check:", domCheck);
  if (!domCheck.titleText || !domCheck.titleText.includes("Counter Value: 0")) {
    throw new Error(`Expected title 'Counter Value: 0', got '${domCheck.titleText}'`);
  }
  if (!domCheck.hasBtn || !domCheck.hasLink) {
    throw new Error("Missing counter button or stylesheet link");
  }

  console.log("6. Testing in-panel interaction...");
  const clickRes = await page.ev(`
    (async () => {
      const btn = document.querySelector(".counter-btn");
      btn.click();
      return true;
    })()
  `);
  await page.wait(500);
  const countAfterClick = await page.ev(`document.querySelector(".counter-title")?.textContent`);
  console.log("Title after click:", countAfterClick);
  if (!countAfterClick?.includes("Counter Value: 1")) {
    throw new Error(`Expected 'Counter Value: 1', got '${countAfterClick}'`);
  }

  console.log("7. Testing hot reload via patch...");
  const patchRes = await fetch(`${BASE}/api/custom-apps/counter-widget/patch`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Diatonic": "1" },
    body: JSON.stringify({
      path: "index.js",
      old: '"Counter Value: " + count',
      new: '"Hot-Reloaded Count: " + count',
    }),
  });
  if (!patchRes.ok) throw new Error(`Patch failed: ${await patchRes.text()}`);
  await page.wait(1000);

  const titleAfterHotReload = await page.ev(`document.querySelector(".counter-title")?.textContent`);
  console.log("Title after hot reload:", titleAfterHotReload);
  if (!titleAfterHotReload?.includes("Hot-Reloaded Count: 1")) {
    throw new Error(`Hot reload failed to update title: '${titleAfterHotReload}'`);
  }

  console.log("8. Checking console errors...");
  console.log("Browser console errors:", page.errors);
  if (page.errors.length > 0) {
    throw new Error(`Browser console errors detected: ${page.errors.join(", ")}`);
  }

  console.log("9. Cleaning up test app...");
  await page.close();
  await fetch(`${BASE}/api/custom-apps/counter-widget`, {
    method: "DELETE",
    headers: { "X-Diatonic": "1" },
  });

  console.log("Phase 4 CDP verification passed successfully with 0 errors!");
}

main().catch((err) => {
  console.error("Verification failed:", err);
  process.exit(1);
});
