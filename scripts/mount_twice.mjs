// Mounts the same panel twice side by side in a scratch Diatonic and checks the copies don't affect each other.
//
//   scripts/dev_scratch.sh start
//   node scripts/mount_twice.mjs todos telemetry notes        (app ids; no ids = every app with a panel)
//
// For each app: both copies must render something and raise no console errors, and clicking a harmless button in copy A
// must leave copy B's HTML unchanged. It does not replace looking at the panel yourself; it catches the shared-state bugs.
// Needs the scratch server and Chrome from scripts/dev_scratch.sh. Buttons named delete, remove, send, trash, clear, connect, sign in, settings are skipped (they open dialogs or leave the page).
import { open } from "./cdp.mjs";

const PORT = process.env.PORT || "5071", CDP = Number(process.env.CDP_PORT || 9333);
const page = await open(`http://127.0.0.1:${PORT}/`, CDP);
await page.wait(2000);
const state = await (await fetch(`http://127.0.0.1:${PORT}/api/state`)).json();
const ids = process.argv.length > 2 ? process.argv.slice(2) : state.apps.filter((a) => a.panel && a.enabled).map((a) => a.id);
let failed = 0;

for (const id of ids) {
  page.errors.length = 0;
  const r = (await page.ev(`(async () => {
    const wait = (ms) => new Promise((r) => setTimeout(r, ms));
    const { panelContext } = await import("/js/panel.js");
    const mod = await import("/apps/${id}.js");
    const make = () => { const d = document.createElement("div"); d.className = "panel-body"; d.style.cssText = "position:fixed;left:-9999px;width:480px;height:700px;overflow:auto"; document.body.append(d); return d; };
    const A = make(), B = make(), app = { id: "${id}", name: "${id}" };
    await mod.render(A, panelContext(app, A, null));
    await mod.render(B, panelContext(app, B, null));
    await wait(500);
    const before = B.innerHTML, out = { a: A.innerText.trim().length, b: B.innerText.trim().length, clicked: null };
    const skip = /delete|remove|send|trash|clear|archive|spam|connect|sign ?in|log ?in|oauth|authori[sz]e|settings|accounts?/i;
    const btn = [...A.querySelectorAll("button")].find((b) => !skip.test(b.textContent + (b.title || "") + (b.ariaLabel || "")));
    if (btn) { out.clicked = btn.textContent.trim().slice(0, 30) || btn.title; btn.click(); await wait(700); }
    out.bUnchanged = B.innerHTML === before;
    A.remove(); B.remove();
    return out;
  })()`).catch((e) => ({ error: e.message.split("\n")[0] }))) ?? { error: "the page navigated away or crashed while the panel was running" };
  const problems = [];
  if (r.error) problems.push(r.error);
  else {
    if (!r.a || !r.b) problems.push(`rendered nothing (A ${r.a} chars, B ${r.b} chars)`);
    if (!r.bUnchanged) problems.push(`clicking "${r.clicked}" in copy A changed copy B`);
  }
  if (page.errors.length) problems.push(`console: ${page.errors[0].slice(0, 100)}`);
  console.log(`${problems.length ? "FAIL" : "PASS"}  ${id.padEnd(14)} ${problems.join("; ")}${r.clicked ? `  (clicked "${r.clicked}")` : ""}`);
  if (problems.length) failed++;
}
page.close();
console.log(failed ? `\n${failed} of ${ids.length} failed` : `\nall ${ids.length} passed`);
process.exit(failed ? 1 : 0);
