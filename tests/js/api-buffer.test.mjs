import test from "node:test";
import assert from "node:assert/strict";
import { api, clearBuffer, getBufferEntry, invalidateApp, DEFAULT_BUFFER_TTL } from "../../diatonic/web/js/api.js";

// Save original fetch
const origFetch = globalThis.fetch;

test.beforeEach(() => {
  clearBuffer();
});

test.afterEach(() => {
  globalThis.fetch = origFetch;
  clearBuffer();
});

test("api.get buffers responses within threshold time", async () => {
  let callCount = 0;
  globalThis.fetch = async (url) => {
    callCount++;
    return {
      ok: true,
      status: 200,
      json: async () => ({ value: 42, count: callCount }),
    };
  };

  const res1 = await api.get("/api/sample/data");
  assert.equal(res1.value, 42);
  assert.equal(res1.count, 1);
  assert.equal(callCount, 1);

  // Second call within default threshold (30s) must reuse buffer
  const res2 = await api.get("/api/sample/data");
  assert.equal(res2.value, 42);
  assert.equal(res2.count, 1);
  assert.equal(callCount, 1, "fetch should not have been called a second time");

  // Verify buffer entry exists with timestamp
  const entry = getBufferEntry("/api/sample/data");
  assert.ok(entry);
  assert.equal(typeof entry.time, "number");
  assert.ok(Date.now() - entry.time < 1000);
});

test("api.get deduplicates in-flight concurrent requests", async () => {
  let callCount = 0;
  globalThis.fetch = async () => {
    callCount++;
    await new Promise((r) => setTimeout(r, 20));
    return {
      ok: true,
      status: 200,
      json: async () => ({ shared: true, call: callCount }),
    };
  };

  const [a, b] = await Promise.all([
    api.get("/api/concurrent/data"),
    api.get("/api/concurrent/data"),
  ]);

  assert.equal(callCount, 1, "only 1 network fetch should be dispatched for concurrent calls");
  assert.deepEqual(a, { shared: true, call: 1 });
  assert.deepEqual(b, { shared: true, call: 1 });
});

test("caller mutations do not corrupt buffered data", async () => {
  globalThis.fetch = async () => ({
    ok: true,
    status: 200,
    json: async () => ({ items: ["first"] }),
  });

  const res1 = await api.get("/api/list");
  res1.items.push("corrupted");

  const res2 = await api.get("/api/list");
  assert.deepEqual(res2.items, ["first"], "buffer copy must remain pristine");
});

test("force flag bypasses the buffer", async () => {
  let callCount = 0;
  globalThis.fetch = async () => {
    callCount++;
    return {
      ok: true,
      status: 200,
      json: async () => ({ version: callCount }),
    };
  };

  const res1 = await api.get("/api/version");
  assert.equal(res1.version, 1);

  // Force reload should make fresh network fetch
  const res2 = await api.get("/api/version", { force: true });
  assert.equal(res2.version, 2);
  assert.equal(callCount, 2);
});

test("expired buffer triggers fresh fetch", async () => {
  let callCount = 0;
  globalThis.fetch = async () => {
    callCount++;
    return {
      ok: true,
      status: 200,
      json: async () => ({ tick: callCount }),
    };
  };

  // TTL: 25ms
  const res1 = await api.get("/api/short-lived", { ttl: 25 });
  assert.equal(res1.tick, 1);

  // Wait 40ms to expire
  await new Promise((r) => setTimeout(r, 40));

  const res2 = await api.get("/api/short-lived", { ttl: 25 });
  assert.equal(res2.tick, 2);
  assert.equal(callCount, 2);
});

test("mutation methods automatically invalidate app buffer", async () => {
  let getCalls = 0;
  globalThis.fetch = async (url, opts) => {
    if (opts?.method === "GET") {
      getCalls++;
      return { ok: true, status: 200, json: async () => ({ todos: [getCalls] }) };
    }
    // POST / PATCH / DELETE
    return { ok: true, status: 200, json: async () => ({ success: true }) };
  };

  await api.get("/api/todos/panel");
  assert.equal(getCalls, 1);

  // Same GET is buffered
  await api.get("/api/todos/panel");
  assert.equal(getCalls, 1);

  // Mutation to /api/todos/item
  await api.post("/api/todos/item", { title: "New" });

  // Next GET must not be served from stale buffer
  await api.get("/api/todos/panel");
  assert.equal(getCalls, 2);
});

test("invalidateApp clears all endpoints for that app", async () => {
  let calls = 0;
  globalThis.fetch = async () => {
    calls++;
    return { ok: true, status: 200, json: async () => ({ data: calls }) };
  };

  await api.get("/api/gcal/events");
  await api.get("/api/gcal/calendars");
  assert.equal(calls, 2);

  // Re-query from buffer
  await api.get("/api/gcal/events");
  await api.get("/api/gcal/calendars");
  assert.equal(calls, 2);

  // Invalidate gcal
  invalidateApp("gcal");

  await api.get("/api/gcal/events");
  assert.equal(calls, 3);
});

test("failed requests are not cached", async () => {
  let callCount = 0;
  globalThis.fetch = async () => {
    callCount++;
    if (callCount === 1) {
      return { ok: false, status: 500, json: async () => ({ error: "Server error" }) };
    }
    return { ok: true, status: 200, json: async () => ({ success: true }) };
  };

  await assert.rejects(async () => {
    await api.get("/api/err");
  });

  // Second attempt should re-fetch and succeed
  const res = await api.get("/api/err");
  assert.deepEqual(res, { success: true });
  assert.equal(callCount, 2);
});
