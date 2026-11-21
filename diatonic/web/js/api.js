import { CLIENT_ID } from "./store.js";

export const DEFAULT_BUFFER_TTL = 30000;

const buffer = new Map();

function clone(data) {
  if (data === undefined || data === null) return data;
  if (typeof structuredClone === "function") {
    try { return structuredClone(data); } catch {}
  }
  try { return JSON.parse(JSON.stringify(data)); } catch { return data; }
}

function invalidateMatching(urlOrPattern) {
  if (!urlOrPattern || typeof urlOrPattern !== "string") return;
  buffer.delete(urlOrPattern);
  const m = urlOrPattern.match(/^(\/api\/(?:apps\/[^\/]+|[^\/]+))/);
  const prefix = m ? m[1] : urlOrPattern;
  for (const k of [...buffer.keys()]) {
    if (k.startsWith(prefix) || (prefix.startsWith("/api/apps/") && k.startsWith(prefix.replace("/api/apps/", "/api/")))) {
      buffer.delete(k);
    }
  }
  if (urlOrPattern.startsWith("/api/panel/tools/")) {
    const parts = urlOrPattern.split("/");
    if (parts[4]) invalidateApp(parts[4]);
  }
}

export function invalidateApp(appId) {
  if (!appId) return;
  const target = (appId === "tiles" || appId === "launcher") ? "home" : appId;
  const prefixes = [
    `/api/${target}`,
    `/api/apps/${target}`,
  ];
  for (const k of [...buffer.keys()]) {
    if (prefixes.some((p) => k.startsWith(p))) {
      buffer.delete(k);
    }
  }
}

export function clearBuffer() {
  buffer.clear();
}

export function getBufferEntry(url) {
  return buffer.get(url);
}

async function j(method, url, body, extra) {
  // X-Diatonic: proves the request comes from this page (a cross-site page can't send it); the terminal requires it.
  const r = await fetch(url, { method, headers: { "X-Diatonic": "1", ...(body ? { "Content-Type": "application/json" } : {}), ...extra }, body: body ? JSON.stringify(body) : undefined });
  const data = await r.json().catch(() => ({}));
  if (method !== "GET" && r.ok) invalidateMatching(url);
  if (!r.ok) throw Object.assign(new Error(data.error || data.detail || `${r.status} ${r.statusText}`), { status: r.status, data });
  return data;
}

async function bufferedGet(url, opts = {}) {
  const force = Boolean(opts?.force);
  const ttl = typeof opts?.ttl === "number" ? opts.ttl : DEFAULT_BUFFER_TTL;
  const now = Date.now();

  if (!force && ttl > 0 && buffer.has(url)) {
    const entry = buffer.get(url);
    if (entry.promise) return entry.promise.then(clone);
    if (now - entry.time < ttl) {
      return clone(entry.data);
    }
  }

  const promise = j("GET", url).then((data) => {
    buffer.set(url, { data, time: Date.now() });
    return clone(data);
  }).catch((err) => {
    buffer.delete(url);
    throw err;
  });

  buffer.set(url, { promise, time: now });
  return promise;
}

export const api = {
  BUFFER_TTL: DEFAULT_BUFFER_TTL,
  get: (u, opts) => bufferedGet(u, opts),
  post: (u, b, extra) => j("POST", u, b || {}, extra),
  put: (u, b) => j("PUT", u, b || {}),
  patch: (u, b) => j("PATCH", u, b || {}),
  del: (u) => j("DELETE", u),
  invalidate: (urlOrPattern) => invalidateMatching(urlOrPattern),
  invalidateApp: (appId) => invalidateApp(appId),
  clearBuffer: () => clearBuffer(),
  getBufferEntry: (url) => getBufferEntry(url),
  state: () => j("GET", "/api/state"),
  health: () => j("GET", "/api/health"),
  putConfig: (patch) => j("PUT", "/api/config", patch),
  putApp: (id, body) => j("PUT", `/api/apps/${id}/config`, body),
  clearSecret: (id, key) => j("DELETE", `/api/apps/${id}/secrets/${key}`),
  conversations: () => j("GET", "/api/conversations"),
  conversation: (id) => j("GET", `/api/conversations/${id}`),
  deleteConversation: (id) => j("DELETE", `/api/conversations/${id}`),
  approve: (id, approve, scope) => j("POST", `/api/approvals/${id}`, { approve, scope }),
  setupVoice: () => j("POST", "/api/voice/setup"),
  async transcribe(int16) {
    const r = await fetch("/api/voice/transcribe", { method: "POST", body: int16, headers: { "Content-Type": "application/octet-stream" } });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw Object.assign(new Error(d.error || "Transcription failed"), { status: r.status });
    return d.text || "";
  },
  /** Returns a WAV Blob, or null if the server asks us to fall back to the system voice. */
  async tts(text) {
    const r = await fetch("/api/voice/tts", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text }) });
    return r.ok ? r.blob() : null;
  },
};

/** Start a run in the background; events arrive on the global stream. */
api.chat = (body) => j("POST", "/api/chat", { ...body, client: CLIENT_ID });
api.focus = () => fetch(`/api/clients/${CLIENT_ID}/focus`, { method: "POST" }).catch(() => {});
api.cancel = (cid) => j("POST", `/api/conversations/${cid}/cancel`);

/** Long-lived stream of every run's events. Resolves when the connection drops. */
export async function streamEvents(onEvent, signal) {
  const r = await fetch(`/api/events?client=${CLIENT_ID}`, { signal });
  if (!r.ok) throw new Error(`Event stream failed (${r.status})`);
  const reader = r.body.getReader(), dec = new TextDecoder();
  let buf = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) return;
    buf += dec.decode(value, { stream: true });
    let i;
    while ((i = buf.indexOf("\n\n")) >= 0) {
      const chunk = buf.slice(0, i); buf = buf.slice(i + 2);
      const line = chunk.split("\n").find((l) => l.startsWith("data: "));
      if (line) onEvent(JSON.parse(line.slice(6)));
    }
  }
}
