// Build eval: sends each prompt in build_prompts.json to the real model through the real page, approves what it proposes, then
// opens what was built and applies the checks. Prints a pass/fail table; exit code 1 if anything failed.
//
//   ./diatonic.sh --port 5071 &            (a scratch DIATONIC_HOME is wise: this builds and deletes apps)
//   Chrome --headless=new --remote-debugging-port=9333 --user-data-dir=/tmp/eval-chrome
//   node evals/run_build_eval.mjs [--port 5071] [--chrome 9333] [--only pomodoro,counter] [--timeout 150]
import fs from "node:fs";
import { open } from "./cdp.mjs";

const arg = (n, d) => { const i = process.argv.indexOf(`--${n}`); return i > 0 ? process.argv[i + 1] : d; };
const PORT = arg("port", "5071"), CHROME = Number(arg("chrome", "9333")), TIMEOUT = Number(arg("timeout", "150")) * 1000;
const only = arg("only", "")?.split(",").filter(Boolean);
const spec = JSON.parse(fs.readFileSync(new URL("./build_prompts.json", import.meta.url)));
const prompts = spec.prompts.filter((p) => !only.length || only.includes(p.id));
const base = `http://localhost:${PORT}`;
const get = (u) => fetch(base + u).then((r) => r.json());

const page = await open(base + "/", CHROME);
await page.wait(2500);
const ev = page.ev;

async function sendAndWait(prompt) {
  await ev(`(async () => { const c = await import("/js/chat.js"); c.newChat(); await c.send(${JSON.stringify(prompt)}); })()`);
  const t0 = Date.now();
  for (;;) {
    await page.wait(400);
    await ev(`document.querySelectorAll(".approval .btn.primary").forEach((b) => b.click())`);      // approve whatever it proposes
    const busy = await ev(`import("/js/chat.js").then((c) => c.busy())`);
    if (!busy && Date.now() - t0 > 1500) return { ms: Date.now() - t0 };
    if (Date.now() - t0 > TIMEOUT) { await ev(`import("/js/chat.js").then((c) => c.stop())`); return { ms: Date.now() - t0, timeout: true }; }
  }
}

const results = [];
for (const p of prompts) {
  const before = new Set((await get("/api/mods")).map((m) => m.name)), appsBefore = new Set((await get("/api/state")).apps.map((a) => a.id));
  const r = { id: p.id, kind: p.kind, fail: [] };
  page.errors.length = 0;
  if (p.seed) await fetch(base + p.seed.post, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(p.seed.body) });   // data the panel should show
  if (p.setup) await sendAndWait(spec.prompts.find((x) => x.id === p.setup).prompt);   // an edit starts from something already built
  const run = await sendAndWait(p.prompt);
  r.ms = run.ms;
  if (run.timeout) r.fail.push("timed out");
  const mods = (await get("/api/mods")).filter((m) => !before.has(m.name));
  const newApps = (await get("/api/state")).apps.filter((a) => !appsBefore.has(a.id));
  const convs = await get("/api/conversations");
  const msgs = convs[0] ? (await get(`/api/conversations/${convs[0].id}`)).messages : [];
  r.said = (msgs.filter((m) => m.role === "assistant").pop()?.content || "").slice(0, 100).replace(/\s+/g, " ");
  const c = p.checks || {};
  if (p.kind === "mod") {
    if (!mods.length) r.fail.push("nothing was added");
    else await ev(`import("/js/mods.js").then((m) => m.loadMods())`);
  } else {
    const target = p.kind === "panel" || p.kind === "edit" ? mods.find((m) => m.kind === "panel") : null;
    if (p.kind === "edit" && target && !(mods.filter((m) => m.kind === "panel").length === 1 && target.version >= 2)) r.fail.push("did not change the existing panel in place");
    const appId = target ? `mod-${target.name}` : newApps.find((a) => a.panel)?.id || mods[0] && `mod-${mods[0].name}`;
    if (!appId) r.fail.push("nothing was built");
    else {
      await ev(`import("/js/panel.js").then((m) => m.openPanel(${JSON.stringify(appId)}))`);
      await page.wait(1200);
      // What a person can read in the panel: its text plus placeholders and labels (an empty text box still says what it is for).
      const readable = () => ev(`(() => { const b = document.querySelector("#panel-body"); return b ? b.innerText + " " + [...b.querySelectorAll("[placeholder],[aria-label],[title]")].map((e) => [e.placeholder, e.ariaLabel, e.title].filter(Boolean).join(" ")).join(" ") : ""; })()`);
      const body = await readable();
      const built = await ev(`document.querySelector("#panel-body")?.children.length || 0`);
      if (c.renders && (!built || /Couldn't load/.test(body))) r.fail.push(`did not render: ${body.slice(0, 80)}`);
      for (const t of c.text || []) if (!body.toLowerCase().includes(t.toLowerCase())) r.fail.push(`missing text "${t}"`);
      for (const s of c.selectors || []) if (!(await ev(`!!document.querySelector("#panel-body ${s}")`))) r.fail.push(`missing ${s}`);
      for (const step of c.interact || []) {
        const sel = step.click || step.type;
        if (step.click) await ev(`document.querySelector("#panel-body ${step.click}")?.click()`);
        if (step.type) await ev(`(() => { const i = document.querySelector("#panel-body ${step.type}"); if (i) { i.value = ${JSON.stringify(step.value)}; i.dispatchEvent(new Event("input", {bubbles: true})); } })()`);
        await page.wait(1300);
        const now = await readable();
        if (step.expect_text && !now.includes(step.expect_text)) r.fail.push(`after ${step.click ? "click" : "typing"} ${sel}: expected "${step.expect_text}"`);
      }
    }
  }
  if (page.errors.length) r.fail.push(`console: ${page.errors[0].slice(0, 80)}`);
  r.ok = !r.fail.length;
  results.push(r);
  console.log(`${r.ok ? "PASS" : "FAIL"}  ${p.id.padEnd(18)} ${String(Math.round(r.ms / 1000)).padStart(3)}s  ${r.fail.join("; ")}${r.ok ? "" : `   (said: ${r.said})`}`);
  await ev(`import("/js/panel.js").then((m) => m.closePanel())`);
  for (const m of mods) await fetch(`${base}/api/mods/${encodeURIComponent(m.name)}`, { method: "DELETE" });   // leave nothing behind
}
const passed = results.filter((r) => r.ok).length;
console.log(`\n${passed}/${results.length} passed`);
page.close();
process.exit(passed === results.length ? 0 : 1);
