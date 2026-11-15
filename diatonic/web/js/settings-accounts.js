// Settings → Accounts: the Google OAuth app, and any number of connected Google accounts.
import { h, toast } from "./dom.js";
import { api } from "./api.js";
import { state, emit } from "./store.js";
import { field, text } from "./form.js";
import { rerenderSettings } from "./settings.js";

let data = null;
const GMAIL = [["", "No Gmail"], ["gmail:read", "Gmail: read only"], ["gmail:manage", "Gmail: read and manage (archive, label, trash)"], ["gmail:send", "Gmail: read, manage and send"]];

function connectUrl(cal, gmail, hint) {
  const services = [cal ? "calendar" : "", gmail].filter(Boolean).join(",");
  return `/api/google/auth/start?services=${encodeURIComponent(services)}${hint ? `&login_hint=${encodeURIComponent(hint)}` : ""}`;
}

export function accountsPane() {
  const body = h("div", {}, h("div", { class: "p-empty" }, "Loading…"));
  const load = () => api.get("/api/google/state").then((d) => { data = d; draw(); }).catch((e) => body.replaceChildren(h("div", { class: "err" }, e.message)));
  load();

  async function act(fn, msg) { try { data = await fn(); draw(); state.apps = await api.get("/api/apps"); emit("apps"); if (msg) toast(msg); } catch (e) { toast(e.message, "error"); } }

  function draw() {
    const id = h("input", { type: "text", placeholder: data.client_set ? `Saved (${data.client_id_hint}). Type to replace` : "…apps.googleusercontent.com", autocomplete: "off" });
    const sec = h("input", { type: "password", placeholder: data.client_set ? "•••••••• saved. Type to replace" : "GOCSPX-…", autocomplete: "off" });
    const cal = h("input", { type: "checkbox", checked: true }), gm = h("select", {}, ...GMAIL.map(([v, l]) => h("option", { value: v, selected: v === "gmail:manage" }, l)));
    const accts = data.accounts;

    const row = (a) => {
      const label = h("input", { type: "text", value: a.label, placeholder: a.name || a.email.split("@")[0], class: "acct-label", "aria-label": `Label for ${a.email}`, maxlength: 30,
        onchange: (e) => act(() => api.post(`/api/google/accounts/${encodeURIComponent(a.email)}`, { label: e.target.value })) });
      const dot = h("input", { type: "color", value: a.color, class: "acct-color", title: "Colour", onchange: (e) => act(() => api.post(`/api/google/accounts/${encodeURIComponent(a.email)}`, { color: e.target.value })) });
      const chips = [a.services.calendar && h("span", { class: "pill" }, "Calendar"), a.services.gmail && h("span", { class: "pill" }, `Gmail · ${a.services.gmail}`),
        a.needs_reauth && h("span", { class: "pill st-error" }, "needs reconnecting")];
      return h("div", { class: `acct-row ${a.enabled ? "" : "dim"}` },
        dot, h("div", { class: "grow" }, h("div", { class: "acct-top" }, h("strong", {}, a.name || a.email), a.default && h("span", { class: "pill st-ok" }, "default for writes")),
          h("div", { class: "p-s" }, a.email), h("div", { class: "chips", style: "margin-top:4px" }, ...chips.filter(Boolean))),
        h("div", { class: "acct-side" }, label,
          h("div", { class: "row", style: "gap:6px;margin-top:6px;flex:none" },
            !a.default && h("button", { class: "btn sm", title: "Used for creating events and sending mail when you don't say which account", onclick: () => act(() => api.put("/api/google/default", { email: a.email }), "Default account set") }, "Make default"),
            h("a", { class: "btn sm", style: "text-decoration:none", href: connectUrl(a.services.calendar, a.services.gmail ? `gmail:${a.services.gmail}` : "", a.email), title: "Reconnect, or grant more access" }, a.needs_reauth ? "Reconnect" : "Change access"),
            h("span", { class: "switch", title: a.enabled ? "Included" : "Ignored" }, h("input", { type: "checkbox", checked: a.enabled, "aria-label": `Use ${a.email}`, onchange: (e) => act(() => api.post(`/api/google/accounts/${encodeURIComponent(a.email)}`, { enabled: e.target.checked })) })),
            h("button", { class: "icon-btn sm", title: "Remove", "aria-label": `Remove ${a.email}`, onclick: () => { if (confirm(`Remove ${a.email}? Its saved login is deleted and Google is told to revoke it.`)) act(() => api.del(`/api/google/accounts/${encodeURIComponent(a.email)}`), "Removed"); }, html: "✕" }))));
    };

    body.replaceChildren(...[
      data.fake && h("div", { class: "notice warn" }, "Demo mode: Google calls go to a local fake (DIATONIC_GOOGLE_FAKE). Nothing touches real accounts."),
      h("div", { class: "p-section" }, "1. Your Google OAuth app"),
      h("div", { class: "notice" }, data.client_set ? "A client ID and secret are saved. You only need them once for every account." : "Create an OAuth client once; it is used for every account you connect.",
        h("details", { style: "margin-top:8px" }, h("summary", {}, "How to get one"),
          h("ol", { class: "steps" },
            h("li", {}, "Google Cloud Console → create a project → enable the ", h("b", {}, "Google Calendar API"), " and the ", h("b", {}, "Gmail API"), "."),
            h("li", {}, "APIs & Services → OAuth consent screen: set it up, add every Google account you'll connect as a test user."),
            h("li", {}, "Credentials → Create credentials → OAuth client ID → ", h("b", {}, "Desktop app"), " (no redirect URI needed). With a ", h("b", {}, "Web application"), " client, the redirect URI below must be listed under “Authorized redirect URIs”."),
            h("li", {}, "Paste the client ID and secret below."),
            h("li", {}, h("b", {}, "Important: "), "while the consent screen is in “Testing”, Google expires logins after 7 days. Set Publishing status to “In production” (it will warn that the app is unverified; that's fine for personal use) so accounts stay connected.")))),
      h("div", { class: "field", style: "margin-top:10px" }, h("label", {}, "Redirect URI sent to Google"),
        h("select", { onchange: (e) => act(() => api.put("/api/google/redirect", { path: e.target.value }), "Redirect URI changed") },
          ...data.redirect_choices.map((c) => h("option", { value: c.path, selected: c.path === data.redirect_path }, `${c.uri}${c.path.includes("/apps/gcal") ? "  (original Calendar setup)" : ""}`))),
        h("div", { class: "help" }, "Seeing “redirect_uri_mismatch”? A Web-application OAuth client only accepts URIs you registered. Pick the one that's registered, or add this one in Google Cloud Console (Desktop-app clients accept any localhost URI). Changes in Google can take a few minutes.")),
      h("div", { class: "row", style: "margin-top:10px" }, field("Client ID", id), field("Client secret", sec),
        h("button", { class: "btn primary", onclick: () => (id.value || sec.value) && act(() => api.put("/api/google/client", { client_id: id.value, client_secret: sec.value }), "Saved") }, "Save")),

      h("div", { class: "p-section", style: "margin-top:20px" }, `2. Connected accounts (${accts.length})`),
      ...(accts.length ? accts.map(row) : [h("div", { class: "p-empty" }, "No accounts yet.")]),
      accts.length > 1 && h("div", { class: "help" }, "With several accounts, reads (calendar, inbox) combine all of them. Writes (creating an event, sending mail) go to the account you name, else the default, else the agent asks you."),

      h("div", { class: "p-section", style: "margin-top:20px" }, "3. Add an account"),
      h("div", { class: "app-card" },
        h("label", { class: "chk", style: "margin:0 0 8px" }, cal, "Google Calendar"), field("Gmail access", gm,
          "Read-only can't change anything. “Send” lets the agent propose emails; each one still needs your approval, and you can reconnect with less access any time."),
        h("div", { class: "row" }, h("button", { class: "btn primary", disabled: !data.client_set, title: data.client_set ? "" : "Save your OAuth client first",
          onclick: () => { if (!cal.checked && !gm.value) return toast("Choose Calendar and/or Gmail.", "error"); location.href = connectUrl(cal.checked, gm.value); } }, "Continue with Google"),
          h("span", { class: "p-s" }, "You'll pick the Google account on the next screen. Add the same screen again for each extra account.")))].filter(Boolean));   // (replaceChildren would print `false` as text)
  }
  return [body];
}
