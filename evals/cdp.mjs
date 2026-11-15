// Tiny Chrome DevTools driver: open a page, press keys, read the DOM.
export async function open(url, port = 9333) {
  const t = await (await fetch(`http://localhost:${port}/json/new?${encodeURIComponent(url)}`, { method: "PUT" })).json();
  const ws = new WebSocket(t.webSocketDebuggerUrl);
  await new Promise((r) => (ws.onopen = r));
  let id = 0; const pending = new Map(), errors = [];
  ws.onmessage = (m) => {
    const d = JSON.parse(m.data);
    if (d.id && pending.has(d.id)) { pending.get(d.id)(d); pending.delete(d.id); }
    else if (d.method === "Runtime.exceptionThrown") errors.push(d.params.exceptionDetails.exception?.description || d.params.exceptionDetails.text);
    else if (d.method === "Runtime.consoleAPICalled" && d.params.type === "error") errors.push(d.params.args.map((a) => a.value ?? a.description).join(" "));
  };
  const send = (method, params = {}) => new Promise((res) => { const i = ++id; pending.set(i, res); ws.send(JSON.stringify({ id: i, method, params })); });
  await send("Runtime.enable"); await send("Page.enable");
  const ev = async (expr) => {
    const r = await send("Runtime.evaluate", { expression: expr, awaitPromise: true, returnByValue: true });
    if (r.result?.exceptionDetails) throw new Error(r.result.exceptionDetails.exception?.description || "eval failed");
    return r.result?.result?.value;
  };
  const MOD = { alt: 1, ctrl: 2, meta: 4, shift: 8 };
  const CODES = { ";": "Semicolon", ",": "Comma", "/": "Slash", " ": "Space" };
  const key = async (k, mods = []) => {
    const m = mods.reduce((a, x) => a | MOD[x], 0);
    const named = k.length > 1;
    const code = named ? k : CODES[k] || (/[a-z]/i.test(k) ? "Key" + k.toUpperCase() : /\d/.test(k) ? "Digit" + k : "");
    const shown = mods.includes("shift") && k.length === 1 ? k.toUpperCase() : k;
    const base = { key: shown, code, modifiers: m, windowsVirtualKeyCode: named ? ({ ArrowDown: 40, ArrowUp: 38, PageDown: 34, PageUp: 33, Escape: 27, Enter: 13, Home: 36, End: 35 }[k] || 0) : shown.toUpperCase().charCodeAt(0) };
    const text = !named && !(m & (1 | 2 | 4)) ? { text: shown } : {};
    await send("Input.dispatchKeyEvent", { type: text.text ? "keyDown" : "rawKeyDown", ...base, ...text });
    await send("Input.dispatchKeyEvent", { type: "keyUp", ...base });
  };
  const wait = (ms) => new Promise((r) => setTimeout(r, ms));
  return { send, ev, key, wait, errors, close: () => { ws.close(); fetch(`http://localhost:${port}/json/close/${t.id}`); } };
}
