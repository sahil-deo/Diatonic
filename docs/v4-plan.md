# v4 plan: apps that run in a sandbox

Status: proposed 2026-11-21. Nothing in this file is built yet. Tick items here and in `todo.md` only when they are done and checked.

v4 builds on [v3](v3-plan.md); v3 does not depend on v4 in any way and is complete without it: v3 keeps an approval card on every app write and makes `ctx.fetch` safe with domain approval, private-address blocking and size and time limits. v4 replaces "the user reads the code" with "the user approves what the app may do".

## Why

Apps the agent writes are JavaScript that runs inside the main page. They can reach the DOM, every `/api/...` route and localStorage, exactly as the page itself can. The only guard is the approval card on each write (the sandbox frame in `web/js/verify.js` is used only for the unapproved test run). That is acceptable while apps are small. It stops being acceptable once `ctx` can reach the network, run write tools and keep timers (v3 Phase 2), and it does not work at all for apps that come from someone else.

## Terms

- **Sandbox:** each app runs in an isolated iframe (its own origin, no access to the main page, no direct network). It can draw its own UI and compute. Nothing else.
- **Broker:** the only way out of the sandbox. The app calls `ctx.fetch`, `ctx.store`, `ctx.notify`, `ctx.tool` and so on; each call is a message to the main page, which forwards it to the server. The server checks it against the app's manifest `permissions` and what the user approved, and refuses anything else.

## What it buys

- The agent can write and change an app without an approval card per file write.
- The user approves capabilities once ("this app may reach youtube.com and send notifications"), not code each time.
- A bug or a hostile app can only do what its approved permissions allow.
- Apps from other people can be imported with a clear list of what they ask for.

## Steps (to refine when v4 starts)

1. **Decide the frame model.** Same-origin `srcdoc` with a restrictive CSP, or a separate origin served by the server (a second port or a `sandbox` attribute without `allow-same-origin`). Check that `localStorage`, fonts and the theme tokens still reach the app.
2. **The broker protocol.** A small message format (`id`, `method`, `args`, result or error), one handler per `ctx` method on the page side, and the same permission check on the server. No method is reachable that is not in the catalog (v3 Phase 1).
3. **Run `ctx` across the boundary.** Every v3 `ctx` method works through the broker with the same signature, so an app does not change. `ctx.onClose`, `ctx.on` and timers need a clean-up path when the frame goes away.
4. **UI kit inside the frame.** The v3 components (`web/js/ui/`) and the shared CSS tokens load in the frame, so light and dark and the harness look carry over.
5. **Permissions UI.** The manifest `permissions` list shown once on first run and again when it grows; a settings view to see and revoke them per app.
6. **Move the existing apps over**, one at a time, with `scripts/mount_twice.mjs` and the module-state check passing for each. Panels that need the real DOM (`shell`, `files`) stay built in; the sandbox is for agent-written apps.
7. **Drop the per-write approval card for sandboxed apps** once their permissions are approved. Writes that leave the sandbox (a tool that changes data, a new domain) still ask.
8. **Docs and tests:** `docs/platform.md` and `docs/panels.md` describe the broker and the permission list; a test per refused call (undeclared domain, undeclared tool, a frame that tries to reach `parent`, `fetch` or `localStorage` directly).

## Open questions

1. Frame origin: `srcdoc` with `sandbox` and CSP, or a separate origin from the server.
2. Whether built-in panels ever move into frames (the plan says no).
3. Import of apps from other people: format, signing, and what the first-run prompt shows.
4. Whether the verify frame (`web/js/verify.js`) and the runtime frame become one thing.

## Out of scope

Changing the guardrails (approvals, untrusted-content rule, cloud privacy prompts, Host/Origin checks, path checks, terminal command classes). The sandbox adds a layer; it removes none.
