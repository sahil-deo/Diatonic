import { CLIENT_ID } from "./store.js";
async function j(method, url, body) {
  const r = await fetch(url, { method, headers: body ? { "Content-Type": "application/json" } : {}, body: body ? JSON.stringify(body) : undefined });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw Object.assign(new Error(data.error || data.detail || `${r.status} ${r.statusText}`), { status: r.status, data });
  return data;
}

export const api = {
  get: (u) => j("GET", u),
  post: (u, b) => j("POST", u, b || {}),
  put: (u, b) => j("PUT", u, b || {}),
  del: (u) => j("DELETE", u),
  state: () => j("GET", "/api/state"),
  health: () => j("GET", "/api/health"),
  putConfig: (patch) => j("PUT", "/api/config", patch),
  putApp: (id, body) => j("PUT", `/api/apps/${id}/config`, body),
  clearSecret: (id, key) => j("DELETE", `/api/apps/${id}/secrets/${key}`),
  conversations: () => j("GET", "/api/conversations"),
  conversation: (id) => j("GET", `/api/conversations/${id}`),
  deleteConversation: (id) => j("DELETE", `/api/conversations/${id}`),
  approve: (id, approve) => j("POST", `/api/approvals/${id}`, { approve }),
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
