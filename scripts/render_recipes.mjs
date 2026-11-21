// Installs each recipe in diatonic/recipes/ as an app on the scratch server, uses it, and checks it renders in dark and light with no console errors.
// Usage: node scripts/render_recipes.mjs [shots-folder]   (needs a fresh scratch server: scripts/dev_scratch.sh stop, then start; a second run on the same server can hang on tabs left by the first)
import fs from "node:fs";
import path from "node:path";
import { open } from "./cdp.mjs";

const root = new URL("../diatonic/recipes/", import.meta.url).pathname, shots = process.argv[2];
const p = await open("http://localhost:5071/");
await p.wait(2500);
const H = `{"X-Diatonic":"1","Content-Type":"application/json"}`;
const call = (m, u, b) => p.ev(`fetch("${u}",{method:"${m}",headers:${H},body:${b ? JSON.stringify(JSON.stringify(b)) : "undefined"}}).then(r=>r.json())`);
/** Answer the permission cards that appear (they come a moment after the call): wait up to 4 s for the first, then until none is left. */
const allow = async () => { let clicked = 0; for (let i = 0; i < 8; i++) { await p.wait(500); const hit = await p.ev(`(() => { const b = document.querySelector(".app-asks .btn.primary"); if (b) b.click(); return !!b; })()`); if (hit) clicked++; else if (clicked) break; } };
const shot = async (name) => { if (!shots) return; fs.mkdirSync(shots, { recursive: true }); const r = await p.send("Page.captureScreenshot", { format: "png" }); fs.writeFileSync(path.join(shots, name + ".png"), Buffer.from(r.result.data, "base64")); };
const theme = (t) => p.ev(`document.documentElement.dataset.theme = "${t}"`);
const problems = [];
const check = (ok, what) => { console.log(ok ? "ok  " : "FAIL", what); if (!ok) problems.push(what); };

for (const id of fs.readdirSync(root).filter((d) => fs.statSync(root + d).isDirectory())) {
  await call("DELETE", `/api/custom-apps/rc-${id}`);
  await call("POST", "/api/custom-apps", { id: `rc-${id}`, name: id });
  const manifest = { ...JSON.parse(fs.readFileSync(`${root}${id}/manifest.json`, "utf8")), id: `rc-${id}` };
  const put = (file, content) => call("PUT", `/api/custom-apps/rc-${id}/file`, { path: file, content });
  check(!(await put("manifest.json", JSON.stringify(manifest))).error, `${id}: manifest accepted`);
  check(!(await put("index.js", fs.readFileSync(`${root}${id}/index.js`, "utf8"))).error, `${id}: index.js accepted`);
  if (id === "tools") await call("POST", "/api/apps/todos/create", { title: "recipe check" });
  await p.ev(`import("/js/panel.js").then(m => m.openPanel("rc-${id}"))`);
  await allow(); await p.wait(1500);   // an app that uses timers asks as soon as it opens
  check((await p.ev(`document.querySelectorAll(".ui-card, .ui-listapp, .ui-section").length`)) > 0, `${id}: renders kit components`);

  if (id === "timer") {
    await p.ev(`(() => { const f = document.querySelector(".ui-form"); f.querySelector("[name=label]").value = "Tea"; f.querySelector("[name=amount]").value = "90"; f.querySelector("[name=unit]").value = "sec"; f.requestSubmit(); })()`);
    await allow(); await p.wait(1200);
    check((await p.ev(`document.querySelectorAll(".ui-list .ui-row").length`)) === 1, "timer: one timer listed");
    await p.ev(`[...document.querySelectorAll(".ui-list .btn")].find(b => b.textContent === "Pause").click()`); await p.wait(800);
    check((await p.ev(`document.querySelector(".ui-list .ui-badge").textContent`)) === "paused", "timer: pause works");
    await p.ev(`[...document.querySelectorAll(".ui-list .btn")].find(b => b.textContent === "Resume").click()`); await p.wait(800);
    check((await p.ev(`document.querySelector(".ui-list .ui-badge").textContent`)) === "running", "timer: resume works");
  }
  if (id === "tracker") {
    await p.ev(`(() => { const f = document.querySelector(".ui-form"); f.querySelector("[name=subject]").value = "Maths"; f.querySelector("[name=lines]").value = "Limits\\nSeries\\nIntegrals"; f.requestSubmit(); })()`);
    await p.wait(1200);
    check((await p.ev(`document.querySelectorAll(".ui-listapp .ui-row").length`)) === 3, "tracker: three topics added in one go");
    await p.ev(`document.querySelector(".ui-listapp .ui-row input[type=checkbox]").click()`); await p.wait(800);
    check((await p.ev(`document.querySelector(".ui-stat-value").textContent`)) === "1 / 3", "tracker: marking a topic updates the count");
    await p.ev(`document.querySelector(".ui-listapp .ui-row").click()`); await p.wait(300);
    check((await p.ev(`!document.querySelector(".ui-la-detail").hidden`)), "tracker: detail pane opens");
  }
  if (id === "tools") {
    check((await p.ev(`document.querySelectorAll(".ui-listapp .ui-row").length`)) >= 1, "tools: the other app's to-dos are listed");
    await p.ev(`document.querySelector(".ui-listapp .ui-row").click()`); await p.wait(300);
    await p.ev(`[...document.querySelectorAll(".ui-la-detail .btn")].find(b => b.textContent === "Done").click()`);
    await allow(); await p.wait(1000);
    check((await p.ev(`[...document.querySelectorAll(".ui-listapp .ui-row")].some(r => r.textContent.includes("recipe check"))`)) === false, "tools: Done changes the other app (after the card is allowed)");
  }
  if (id === "search") {
    await p.ev(`(() => { const f = document.querySelector(".ui-form"); f.querySelector("[name=q]").value = "piano"; f.requestSubmit(); })()`);
    await allow(); await p.wait(3500);
    const rows = await p.ev(`document.querySelectorAll(".ui-list .ui-row").length`);
    const msg = await p.ev(`document.querySelector(".ui-empty-title")?.textContent + " | " + (document.querySelector(".ui-empty .ui-sub")?.textContent || "")`);
    check(rows > 0 || /did not work/.test(msg), `search: results (${rows}) or a readable failure (${msg})`);
  }
  for (const t of ["dark", "light"]) { await theme(t); await p.wait(250); await shot(`${id}-${t}`); }
  await theme("dark");
  await call("DELETE", `/api/custom-apps/rc-${id}`);
}
// the gallery, every component
await p.ev(`import("/js/panel.js").then(m => m.openPanel("components"))`); await p.wait(1500);
const n = await p.ev(`document.querySelectorAll(".panel-body .ui-card").length`);
check(n >= 15, `gallery: ${n} component cards`);
check(!(await p.ev(`!!document.querySelector(".panel-body .err")`)), "gallery: every example rendered");
for (const t of ["dark", "light"]) { await theme(t); await p.wait(250); await shot(`gallery-${t}`); }
check(p.errors.length === 0, `no console errors ${JSON.stringify(p.errors).slice(0, 300)}`);
p.close();
process.exit(problems.length ? 1 : 0);
