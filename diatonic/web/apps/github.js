// GitHub client panel: browse repos, manage PRs, branches, issues, diffs, and saved repositories.
import { icon } from "/js/icons.js";
import { render as md } from "/js/md.js";
import { emit } from "/js/store.js";

const BASE = "/api/apps/github";
export const wide = true;

const instances = new WeakMap(); // state-ok: keyed by this panel's ctx

export const current = (ctx) => {
  const inst = instances.get(ctx);
  if (!inst) return null;
  const s = inst.getState();
  if (s.view === "pr" && s.activePR) {
    return { kind: "pull_request", ref: `${s.repoSlug}#${s.activePR.number}`, title: s.activePR.title };
  }
  if (s.view === "issue" && s.activeIssue) {
    return { kind: "issue", ref: `${s.repoSlug}#${s.activeIssue.number}`, title: s.activeIssue.title };
  }
  if (s.view === "repo" && s.repoSlug) {
    return { kind: "repository", ref: s.repoSlug, title: s.repoSlug };
  }
  return null;
};

/** Called by the agent or command bar: open a repo, pull request, or issue. */
export async function navigate(p, ctx) {
  const inst = instances.get(ctx);
  if (!inst) return;
  if (p.repo) {
    await inst.openRepo(p.repo);
    if (p.pr) await inst.openPR(p.repo, Number(p.pr));
    else if (p.issue) await inst.openIssue(p.repo, Number(p.issue));
    else if (p.tab) inst.switchTab(p.tab);
  } else if (p.query !== undefined) {
    await inst.filterRepos(p.query);
  }
}

function timeAgo(dateStr) {
  if (!dateStr) return "";
  const d = new Date(dateStr);
  const sec = Math.floor((Date.now() - d.getTime()) / 1000);
  if (sec < 60) return "just now";
  const min = Math.floor(sec / 60);
  if (min < 60) return `${min}m ago`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr}h ago`;
  const day = Math.floor(hr / 24);
  if (day < 30) return `${day}d ago`;
  return d.toLocaleDateString([], { month: "short", day: "numeric", year: d.getFullYear() !== new Date().getFullYear() ? "numeric" : undefined });
}

export async function render(el, ctx) {
  const { api, h, toast, prompt } = ctx;

  const state = {
    view: "repos", // "repos" | "repo" | "pr" | "issue"
    repoSlug: null,
    repoData: null,
    tab: "prs", // "prs" | "branches" | "issues" | "readme"
    prFilter: "open",
    issueFilter: "open",
    activePR: null,
    activeIssue: null,
    prSubTab: "conversation", // "conversation" | "diff"
    diffText: null,
    searchQuery: "",
    branchSearch: "",
    creatingPR: false,
    creatingBranch: false,
    creatingIssue: false,
    loading: false,
    reposList: [],
    panelData: null,
    sidebarOpen: ctx.state.get("sidebar_open", true),
  };

  const inst = {
    getState: () => state,
    openRepo,
    openPR,
    openIssue,
    switchTab,
    filterRepos: async (q) => {
      state.searchQuery = q;
      if (state.view === "repos") await renderReposView();
    },
  };
  instances.set(ctx, inst);

  // Shell Layout: Collapsible Sidebar + Main Content Area
  el.replaceChildren();
  const shell = h("div", { class: "gh-shell" });
  const sidebarEl = h("div", { class: `gh-sidebar ${state.sidebarOpen ? "" : "collapsed"}` });
  const mainEl = h("div", { class: "gh-main" });
  shell.append(sidebarEl, mainEl);
  el.append(shell);

  ctx.list({ items: () => [...el.querySelectorAll(".gh-row, .gh-saved-row, .p-item")] });

  function getSavedRepos() {
    try {
      const list = ctx.state.get("saved_repos", null);
      if (Array.isArray(list)) return list;
      const fromLocal = JSON.parse(localStorage.getItem("diatonic:github:saved_repos") || "[]");
      return Array.isArray(fromLocal) ? fromLocal : [];
    } catch { return []; }
  }

  function setSavedRepos(list) {
    try { localStorage.setItem("diatonic:github:saved_repos", JSON.stringify(list)); } catch {}
    ctx.state.set("saved_repos", list);
  }

  function isRepoSaved(name) {
    return getSavedRepos().includes(name);
  }

  function toggleSaveRepo(name) {
    let list = getSavedRepos();
    if (list.includes(name)) {
      list = list.filter((r) => r !== name);
      toast?.(`Removed ${name} from saved`);
    } else {
      list.push(name);
      toast?.(`Saved ${name}`);
    }
    setSavedRepos(list);
    paintSidebar();

    // Update any star buttons in the main view
    const btns = el.querySelectorAll(`[data-star-repo="${name}"]`);
    btns.forEach((b) => {
      const on = list.includes(name);
      b.classList.toggle("on", on);
      b.title = on ? "Remove from saved" : "Save repository";
      if (b.dataset.label) b.textContent = on ? "Saved" : "Save repo";
    });
  }

  function toggleSidebar(forceState) {
    state.sidebarOpen = typeof forceState === "boolean" ? forceState : !state.sidebarOpen;
    ctx.state.set("sidebar_open", state.sidebarOpen);
    sidebarEl.classList.toggle("collapsed", !state.sidebarOpen);
    const toggleBtns = el.querySelectorAll(".gh-sidebar-toggle");
    toggleBtns.forEach((b) => b.classList.toggle("active", state.sidebarOpen));
  }

  function paintSidebar() {
    sidebarEl.replaceChildren();
    const saved = getSavedRepos();

    const head = h("div", { class: "gh-sidebar-head" },
      h("span", { style: "display:inline-flex;align-items:center;gap:6px" },
        h("span", { html: icon("star", 12), style: "color:var(--text)" }),
        "Saved Repos"),
      h("button", {
        class: "icon-btn sm",
        title: "Collapse sidebar",
        "aria-label": "Collapse sidebar",
        onclick: () => toggleSidebar(false),
        html: icon("chevleft", 14),
      })
    );

    const listEl = h("div", { class: "gh-sidebar-list" });

    if (!saved.length) {
      listEl.append(
        h("div", { class: "gh-sidebar-empty" }, "No saved repositories yet. Click the star on any repo to save it here.")
      );
    } else {
      for (const repoSlug of saved) {
        const isCurrent = state.repoSlug === repoSlug;
        const [owner, name] = repoSlug.split("/");
        const delBtn = h("button", {
          class: "icon-btn sm gh-saved-del",
          title: `Remove ${repoSlug}`,
          "aria-label": `Remove ${repoSlug}`,
          onclick: (ev) => {
            ev.stopPropagation();
            toggleSaveRepo(repoSlug);
          },
          html: icon("x", 12),
        });

        const row = h("button", {
          class: `gh-saved-row ${isCurrent ? "active" : ""}`,
          onclick: () => openRepo(repoSlug),
        },
          h("span", { html: icon("folder", 13), style: "color:var(--faint);flex:none" }),
          h("span", { class: "gh-saved-name", title: repoSlug }, name || repoSlug),
          delBtn
        );
        listEl.append(row);
      }
    }

    sidebarEl.append(head, listEl);
  }

  // Initial load
  paintSidebar();
  await showRepos();

  async function showRepos() {
    state.view = "repos";
    state.repoSlug = null;
    state.repoData = null;
    state.activePR = null;
    state.activeIssue = null;
    emit("screen");
    paintSidebar();
    await renderReposView();
  }

  async function renderReposView() {
    mainEl.replaceChildren();
    state.loading = true;

    const wrap = h("div", { class: "gh-wrap" });
    mainEl.append(wrap);

    // Fetch repos and panel dashboard data
    let reposRes, panelRes;
    try {
      [reposRes, panelRes] = await Promise.all([
        api.get(`${BASE}/repos?limit=50`),
        api.get(`${BASE}/panel`),
      ]);
    } catch (err) {
      const is404 = String(err?.message || "").includes("404") || err?.status === 404;
      const subtitle = is404
        ? "Server restart needed: new backend routes were added in Python. Please restart Diatonic in your terminal (./diatonic.sh) and refresh."
        : (err.message || "Add a personal access token in Settings → Apps → GitHub or log in with gh CLI.");

      wrap.append(
        h("div", { class: "p-empty" },
          h("div", { style: "margin-bottom:8px;font-weight:600" }, is404 ? "Server restart required" : "GitHub not connected"),
          h("div", { class: "p-s", style: "max-width:380px;margin:0 auto 16px;line-height:1.5" }, subtitle),
          h("button", {
            class: "btn sm",
            onclick: () => is404 ? location.reload() : prompt("Configure GitHub connection"),
          }, is404 ? "Refresh page" : "Configure GitHub"))
      );
      return;
    }

    if (panelRes?.error && !reposRes?.repos) {
      wrap.append(
        h("div", { class: "p-empty" },
          h("div", { style: "margin-bottom:8px;font-weight:600" }, "GitHub error"),
          h("div", { class: "p-s", style: "max-width:380px;margin:0 auto 16px;line-height:1.5" }, panelRes.error),
          h("button", { class: "btn sm", onclick: () => prompt("Configure GitHub connection") }, "Configure GitHub"))
      );
      return;
    }

    state.reposList = reposRes?.repos || [];
    state.panelData = panelRes || { issues: [], prs: [] };

    // Header & Search
    const searchInput = h("input", {
      type: "search",
      placeholder: "Search repositories…",
      value: state.searchQuery,
      "aria-label": "Search repositories",
    });
    let searchTimer;
    searchInput.addEventListener("input", () => {
      clearTimeout(searchTimer);
      searchTimer = setTimeout(() => {
        state.searchQuery = searchInput.value.trim().toLowerCase();
        paintRepoCards();
      }, 200);
    });

    wrap.append(
      h("div", { class: "gh-header" },
        h("div", { class: "gh-breadcrumb" },
          h("button", {
            class: `gh-sidebar-toggle ${state.sidebarOpen ? "active" : ""}`,
            title: "Toggle saved repositories",
            "aria-label": "Toggle saved repositories",
            onclick: () => toggleSidebar(),
            html: icon("menu", 14),
          }),
          h("span", { class: "current" }, "GitHub Repositories")),
        h("div", { class: "gh-actions" },
          h("button", { class: "icon-btn sm", title: "Refresh", "aria-label": "Refresh", onclick: () => renderReposView(), html: icon("refresh", 13) }))
      ),
      h("div", { class: "quick-add" }, searchInput)
    );

    const cardsContainer = h("div", { class: "gh-row-list" });
    wrap.append(cardsContainer);

    function paintRepoCards() {
      cardsContainer.replaceChildren();
      const q = state.searchQuery.toLowerCase();
      const filtered = state.reposList.filter((r) => !q || r.name.toLowerCase().includes(q) || (r.description && r.description.toLowerCase().includes(q)));

      // Assigned to you & PRs section (if matching or no query)
      if (!q && (state.panelData.issues?.length || state.panelData.prs?.length)) {
        if (state.panelData.issues?.length) {
          cardsContainer.append(h("div", { class: "p-section", style: "margin:4px 0" }, `Assigned to you (${state.panelData.issues.length})`));
          for (const i of state.panelData.issues) {
            cardsContainer.append(
              h("div", {
                class: "gh-row",
                onclick: () => i.repo ? openIssue(i.repo, i.number) : prompt(`Summarize issue ${i.url}`),
                role: "button",
                tabindex: "0",
              },
                h("span", { class: `gh-badge ${i.state}`, style: "flex:none" }, "Issue"),
                h("div", { class: "gh-row-name" },
                  h("span", { class: "gh-row-title" }, i.title)),
                h("div", { class: "gh-row-desc" },
                  `${i.repo || ""}#${i.number}`,
                  i.labels?.length ? ` · ${i.labels.slice(0, 2).join(", ")}` : ""),
                h("div", { class: "gh-row-meta" },
                  h("span", {}, `${i.comments} comments`)))
            );
          }
        }
        if (state.panelData.prs?.length) {
          cardsContainer.append(h("div", { class: "p-section", style: "margin:8px 0 4px" }, `Pull requests involving you (${state.panelData.prs.length})`));
          for (const p of state.panelData.prs) {
            cardsContainer.append(
              h("div", {
                class: "gh-row",
                onclick: () => p.repo ? openPR(p.repo, p.number) : prompt(`Review pull request ${p.url}`),
                role: "button",
                tabindex: "0",
              },
                h("span", { class: "gh-badge open", style: "flex:none" }, "PR"),
                h("div", { class: "gh-row-name" },
                  h("span", { class: "gh-row-title" }, p.title)),
                h("div", { class: "gh-row-desc" }, `${p.repo || ""}#${p.number}`),
                h("div", { class: "gh-row-meta" },
                  h("span", {}, `${p.comments} comments`)))
            );
          }
        }
      }

      cardsContainer.append(h("div", { class: "p-section", style: "margin:8px 0 4px" }, `Repositories (${filtered.length})`));
      if (!filtered.length) {
        cardsContainer.append(h("div", { class: "p-empty" }, q ? "No repositories match your search." : "No repositories found."));
        return;
      }

      for (const r of filtered) {
        const isSaved = isRepoSaved(r.name);
        const starBtn = h("button", {
          type: "button",
          class: `gh-star-btn ${isSaved ? "on" : ""}`,
          "data-star-repo": r.name,
          title: isSaved ? "Remove from saved" : "Save repository",
          "aria-label": isSaved ? `Remove ${r.name} from saved` : `Save ${r.name}`,
          onclick: (ev) => {
            ev.stopPropagation();
            toggleSaveRepo(r.name);
          },
          html: icon("star", 13),
        });

        cardsContainer.append(
          h("div", {
            class: "gh-row",
            onclick: () => openRepo(r.name),
            role: "button",
            tabindex: "0",
          },
            starBtn,
            h("div", { class: "gh-row-name" },
              h("span", { html: icon("folder", 14), style: "color:var(--faint);flex:none" }),
              h("span", { class: "gh-row-title" }, r.name),
              h("span", { class: "gh-badge priv" }, r.private ? "Private" : "Public")),
            h("div", { class: "gh-row-desc" }, r.description || ""),
            h("div", { class: "gh-row-meta" },
              r.stars ? h("span", {}, h("span", { html: icon("star", 11) }), String(r.stars)) : null,
              r.open_issues ? h("span", {}, `${r.open_issues} issue${r.open_issues === 1 ? "" : "s"}`) : null,
              h("span", {}, timeAgo(r.updated_at))))
        );
      }
    }

    paintRepoCards();
  }

  async function openRepo(repoSlug) {
    state.view = "repo";
    state.repoSlug = repoSlug;
    state.activePR = null;
    state.activeIssue = null;
    state.creatingPR = false;
    state.creatingBranch = false;
    state.creatingIssue = false;
    emit("screen");
    paintSidebar();

    mainEl.replaceChildren();
    const wrap = h("div", { class: "gh-wrap" });
    mainEl.append(wrap);

    const [owner, name] = repoSlug.split("/");
    wrap.append(h("div", { class: "p-empty" }, "Loading repository…"));

    let repo;
    try {
      repo = await api.get(`${BASE}/repo/${owner}/${name}`);
      state.repoData = repo;
    } catch (err) {
      wrap.replaceChildren(
        h("div", { class: "p-empty" },
          h("div", { class: "p-s", style: "margin-bottom:12px" }, `Error loading ${repoSlug}: ${err.message}`),
          h("button", { class: "btn sm", onclick: () => showRepos() }, "← Back to repositories"))
      );
      return;
    }

    renderRepoView(repo);
  }

  function renderRepoView(repo) {
    mainEl.replaceChildren();
    const wrap = h("div", { class: "gh-wrap" });
    mainEl.append(wrap);

    const isSaved = isRepoSaved(state.repoSlug);

    // Top Breadcrumb & Actions
    wrap.append(
      h("div", { class: "gh-header" },
        h("div", { class: "gh-breadcrumb" },
          h("button", {
            class: `gh-sidebar-toggle ${state.sidebarOpen ? "active" : ""}`,
            title: "Toggle saved repositories",
            "aria-label": "Toggle saved repositories",
            onclick: () => toggleSidebar(),
            html: icon("menu", 14),
          }),
          h("button", { onclick: () => showRepos() }, "Repositories"),
          h("span", { class: "sep" }, "/"),
          h("span", { class: "current" }, state.repoSlug)),
        h("div", { class: "gh-actions" },
          h("button", {
            class: `btn sm ${isSaved ? "active" : ""}`,
            "data-star-repo": state.repoSlug,
            "data-label": "1",
            onclick: () => toggleSaveRepo(state.repoSlug),
          },
            h("span", { html: icon("star", 13), style: isSaved ? "color:var(--text)" : "" }),
            isSaved ? "Saved" : "Save repo"),
          h("button", { class: "btn sm", title: "Ask the agent about this repo", onclick: () => prompt(`Work with repository ${state.repoSlug}`) }, "Ask agent"),
          repo.url ? h("a", { class: "icon-btn sm", href: repo.url, target: "_blank", rel: "noopener noreferrer", title: "Open on GitHub", "aria-label": "Open on GitHub", html: icon("external", 14) }) : null)
      ),
      // Repo hero card
      h("div", { class: "gh-repo-hero" },
        h("div", { class: "gh-repo-title" },
          h("span", {}, state.repoSlug),
          h("span", { class: "gh-badge priv" }, repo.private ? "Private" : "Public"),
          repo.stars ? h("span", { class: "gh-badge tag" }, h("span", { html: icon("star", 12) }), ` ${repo.stars}`) : null),
        repo.description ? h("div", { class: "gh-repo-desc" }, repo.description) : null,
        h("div", { class: "gh-repo-meta" },
          h("span", {}, h("span", { html: icon("git-branch", 12) }), ` ${repo.default_branch || "main"}`),
          h("span", {}, `Updated ${timeAgo(repo.pushed_at)}`)))
    );

    // Tabs
    const tabs = [
      { id: "prs", label: "Pull requests" },
      { id: "branches", label: "Branches" },
      { id: "issues", label: "Issues" },
      { id: "readme", label: "Overview / README" },
    ];

    const tabContent = h("div", { class: "gh-tab-content" });

    const tabsBar = h("div", { class: "gh-tabs" },
      ...tabs.map((t) => h("button", {
        class: `gh-tab ${state.tab === t.id ? "active" : ""}`,
        onclick: () => switchTab(t.id),
      }, t.label))
    );

    wrap.append(tabsBar, tabContent);
    renderActiveTab(tabContent);
  }

  function switchTab(tabId) {
    state.tab = tabId;
    state.creatingPR = false;
    state.creatingBranch = false;
    state.creatingIssue = false;
    const tabBtns = el.querySelectorAll(".gh-tab");
    tabBtns.forEach((b, idx) => {
      const ids = ["prs", "branches", "issues", "readme"];
      b.classList.toggle("active", ids[idx] === tabId);
    });
    const tabContent = el.querySelector(".gh-tab-content");
    if (tabContent) renderActiveTab(tabContent);
  }

  function renderActiveTab(container) {
    container.replaceChildren();
    if (state.tab === "prs") renderPRsTab(container);
    else if (state.tab === "branches") renderBranchesTab(container);
    else if (state.tab === "issues") renderIssuesTab(container);
    else if (state.tab === "readme") renderReadmeTab(container);
  }

  // ---- PULL REQUESTS TAB --------------------------------------------
  async function renderPRsTab(container) {
    const [owner, name] = state.repoSlug.split("/");
    container.replaceChildren(h("div", { class: "p-empty" }, "Loading pull requests…"));

    if (state.creatingPR) {
      renderNewPRForm(container);
      return;
    }

    let pulls = [];
    try {
      const res = await api.get(`${BASE}/repo/${owner}/${name}/pulls?state=${state.prFilter}`);
      pulls = res.pulls || [];
    } catch (err) {
      container.replaceChildren(h("div", { class: "p-empty" }, `Failed to load pull requests: ${err.message}`));
      return;
    }

    container.replaceChildren();

    // Toolbar: Filter pills + New PR button
    const filterPills = h("div", { class: "gh-pills" },
      ...["open", "closed", "all"].map((st) => h("button", {
        class: `gh-pill ${state.prFilter === st ? "active" : ""}`,
        onclick: () => { state.prFilter = st; renderPRsTab(container); },
      }, st[0].toUpperCase() + st.slice(1)))
    );

    const newPRBtn = h("button", {
      class: "btn primary sm",
      onclick: () => { state.creatingPR = true; renderNewPRForm(container); },
    }, h("span", { html: icon("plus", 13) }), " New pull request");

    container.append(
      h("div", { class: "gh-bar" }, filterPills, newPRBtn)
    );

    if (!pulls.length) {
      container.append(h("div", { class: "p-empty" }, `No ${state.prFilter} pull requests.`));
      return;
    }

    const rowList = h("div", { class: "gh-row-list" });
    container.append(rowList);

    for (const pr of pulls) {
      const prBadge = pr.merged ? "merged" : pr.state;
      const label = pr.merged ? "Merged" : (pr.draft ? "Draft" : pr.state);
      rowList.append(
        h("div", {
          class: "gh-row",
          onclick: () => openPR(state.repoSlug, pr.number),
          role: "button",
          tabindex: "0",
        },
          h("span", { class: `gh-badge ${prBadge}`, style: "flex:none" }, label),
          h("div", { class: "gh-row-name" },
            h("span", { class: "gh-row-title" }, `#${pr.number} ${pr.title}`)),
          h("div", { class: "gh-row-desc" }, `by ${pr.author} · ${pr.branch}`),
          h("div", { class: "gh-row-meta" },
            h("span", {}, timeAgo(pr.updated_at))))
      );
    }
  }

  async function renderNewPRForm(container) {
    const [owner, name] = state.repoSlug.split("/");
    container.replaceChildren(h("div", { class: "p-empty" }, "Loading branches…"));

    let branches = [];
    try {
      const bRes = await api.get(`${BASE}/repo/${owner}/${name}/branches`);
      branches = bRes.branches || [];
    } catch {
      branches = [{ name: "main" }];
    }

    container.replaceChildren();

    const titleInput = h("input", { type: "text", placeholder: "Pull request title", "aria-label": "Title" });
    const headSelect = h("select", { "aria-label": "Head branch" },
      ...branches.map((b) => h("option", { value: b.name }, b.name)));
    const baseSelect = h("select", { "aria-label": "Base branch" },
      ...branches.map((b) => h("option", { value: b.name, selected: b.name === (state.repoData?.default_branch || "main") }, b.name)));
    const bodyInput = h("textarea", { placeholder: "Describe your pull request (Markdown supported)…" });
    const draftCheck = h("input", { type: "checkbox", id: "gh-draft-check" });

    const errorBox = h("div", { class: "p-s", style: "color:var(--danger)" });

    const submitBtn = h("button", { class: "btn primary sm", onclick: create }, "Create pull request");
    const cancelBtn = h("button", { class: "btn sm", onclick: () => { state.creatingPR = false; renderPRsTab(container); } }, "Cancel");

    async function create() {
      const title = titleInput.value.trim();
      if (!title) { errorBox.textContent = "Title is required."; return; }
      submitBtn.disabled = true;
      submitBtn.textContent = "Creating…";
      try {
        const res = await api.post(`${BASE}/repo/${owner}/${name}/pulls`, {
          title,
          head: headSelect.value,
          base: baseSelect.value,
          body: bodyInput.value,
          draft: draftCheck.checked,
        });
        toast?.(`Created PR #${res.number}`);
        state.creatingPR = false;
        await openPR(state.repoSlug, res.number);
      } catch (err) {
        errorBox.textContent = err.message || "Failed to create pull request.";
        submitBtn.disabled = false;
        submitBtn.textContent = "Create pull request";
      }
    }

    container.append(
      h("div", { class: "gh-card-title", style: "margin-bottom:8px" }, "New Pull Request"),
      h("div", { class: "gh-form" },
        h("div", { class: "gh-form-row" },
          h("label", {}, "Base:"),
          baseSelect,
          h("label", { style: "margin-left:8px" }, "← Head:"),
          headSelect),
        titleInput,
        bodyInput,
        h("label", { style: "display:inline-flex;align-items:center;gap:6px;font-size:12.5px;cursor:pointer" },
          draftCheck, "Create as draft"),
        errorBox,
        h("div", { style: "display:flex;gap:8px;margin-top:6px" }, submitBtn, cancelBtn))
    );
  }

  // ---- PR DETAIL VIEW -----------------------------------------------
  async function openPR(repoSlug, number) {
    state.view = "pr";
    state.repoSlug = repoSlug;
    state.prSubTab = "conversation";
    state.diffText = null;
    emit("screen");
    paintSidebar();

    mainEl.replaceChildren();
    const wrap = h("div", { class: "gh-wrap" });
    mainEl.append(wrap);

    const [owner, name] = repoSlug.split("/");
    wrap.append(h("div", { class: "p-empty" }, `Loading PR #${number}…`));

    let pr;
    try {
      pr = await api.get(`${BASE}/repo/${owner}/${name}/pulls/${number}`);
      state.activePR = pr;
    } catch (err) {
      wrap.replaceChildren(
        h("div", { class: "p-empty" },
          h("div", { class: "p-s", style: "margin-bottom:12px" }, `Error loading PR #${number}: ${err.message}`),
          h("button", { class: "btn sm", onclick: () => openRepo(repoSlug) }, "← Back to repo"))
      );
      return;
    }

    renderPRDetailView(wrap, pr);
  }

  function renderPRDetailView(wrap, pr) {
    wrap.replaceChildren();
    const [owner, name] = state.repoSlug.split("/");

    const prStateBadge = pr.merged ? "merged" : pr.state;
    const badgeLabel = pr.merged ? "Merged" : (pr.draft ? "Draft" : (pr.state === "open" ? "Open" : "Closed"));

    // Header & Breadcrumb
    wrap.append(
      h("div", { class: "gh-header" },
        h("div", { class: "gh-breadcrumb" },
          h("button", {
            class: `gh-sidebar-toggle ${state.sidebarOpen ? "active" : ""}`,
            title: "Toggle saved repositories",
            "aria-label": "Toggle saved repositories",
            onclick: () => toggleSidebar(),
            html: icon("menu", 14),
          }),
          h("button", { onclick: () => openRepo(state.repoSlug) }, state.repoSlug),
          h("span", { class: "sep" }, "/"),
          h("span", { class: "current" }, `#${pr.number}`)),
        h("div", { class: "gh-actions" },
          h("button", { class: "btn sm", onclick: () => prompt(`Review pull request ${state.repoSlug}#${pr.number}`) }, "Review with agent"),
          pr.url ? h("a", { class: "icon-btn sm", href: pr.url, target: "_blank", rel: "noopener noreferrer", title: "Open on GitHub", "aria-label": "Open on GitHub", html: icon("external", 14) }) : null)
      ),
      // PR Hero Title Card
      h("div", { class: "gh-repo-hero" },
        h("div", { style: "display:flex;align-items:flex-start;justify-content:space-between;gap:8px" },
          h("div", { style: "font-size:15px;font-weight:600" }, pr.title),
          h("span", { class: `gh-badge ${prStateBadge}` }, badgeLabel)),
        h("div", { class: "gh-repo-meta" },
          h("span", {}, `by ${pr.author}`),
          h("span", {}, `${pr.base} ← ${pr.head}`),
          h("span", {}, `+${pr.additions} -${pr.deletions}`)))
    );

    // Merge & Close Action Bar
    const actionBtns = [];
    if (pr.state === "open" && !pr.merged) {
      const mergeMethod = h("select", { style: "width:auto;padding:3px 6px;font-size:12px;border:1px solid var(--border);border-radius:calc(6px * var(--rs, 1));background:var(--surface)" },
        h("option", { value: "squash" }, "Squash and merge"),
        h("option", { value: "merge" }, "Create merge commit"),
        h("option", { value: "rebase" }, "Rebase and merge"));

      const mergeBtn = h("button", { class: "btn primary sm", onclick: async () => {
        if (!confirm(`Merge PR #${pr.number} into ${pr.base}?`)) return;
        mergeBtn.disabled = true;
        try {
          await api.post(`${BASE}/repo/${owner}/${name}/pulls/${pr.number}/merge`, { method: mergeMethod.value });
          toast?.(`Merged PR #${pr.number}`);
          await openPR(state.repoSlug, pr.number);
        } catch (err) {
          toast?.(`Merge failed: ${err.message}`);
          mergeBtn.disabled = false;
        }
      } }, "Merge pull request");

      const closeBtn = h("button", { class: "btn sm danger", onclick: async () => {
        if (!confirm(`Close PR #${pr.number}?`)) return;
        closeBtn.disabled = true;
        try {
          await api.post(`${BASE}/repo/${owner}/${name}/pulls/${pr.number}/close`, { state: "closed" });
          toast?.(`Closed PR #${pr.number}`);
          await openPR(state.repoSlug, pr.number);
        } catch (err) {
          toast?.(`Close failed: ${err.message}`);
          closeBtn.disabled = false;
        }
      } }, "Close PR");

      actionBtns.push(mergeMethod, mergeBtn, closeBtn);
    } else if (pr.state === "closed" && !pr.merged) {
      const reopenBtn = h("button", { class: "btn sm", onclick: async () => {
        reopenBtn.disabled = true;
        try {
          await api.post(`${BASE}/repo/${owner}/${name}/pulls/${pr.number}/close`, { state: "open" });
          toast?.(`Reopened PR #${pr.number}`);
          await openPR(state.repoSlug, pr.number);
        } catch (err) {
          toast?.(`Reopen failed: ${err.message}`);
          reopenBtn.disabled = false;
        }
      } }, "Reopen PR");
      actionBtns.push(reopenBtn);
    }

    if (actionBtns.length) {
      wrap.append(h("div", { style: "display:flex;gap:8px;align-items:center;margin:6px 0" }, ...actionBtns));
    }

    // Subtabs: Conversation & Files Changed / Diff
    const subtabsBar = h("div", { class: "gh-tabs" },
      h("button", {
        class: `gh-tab ${state.prSubTab === "conversation" ? "active" : ""}`,
        onclick: () => { state.prSubTab = "conversation"; renderPRSubTab(subtabContainer, pr); },
      }, `Conversation (${(pr.comments?.length || 0) + 1})`),
      h("button", {
        class: `gh-tab ${state.prSubTab === "diff" ? "active" : ""}`,
        onclick: () => { state.prSubTab = "diff"; renderPRSubTab(subtabContainer, pr); },
      }, `Files changed (${pr.files?.length || 0})`)
    );

    const subtabContainer = h("div", { class: "gh-subtab-content" });
    wrap.append(subtabsBar, subtabContainer);
    renderPRSubTab(subtabContainer, pr);
  }

  async function renderPRSubTab(container, pr) {
    container.replaceChildren();
    const [owner, name] = state.repoSlug.split("/");

    if (state.prSubTab === "conversation") {
      // PR description body
      container.append(
        h("div", { class: "gh-card" },
          h("div", { class: "gh-card-head" },
            h("span", {}, `${pr.author} opened this PR`),
            h("span", {}, pr.draft ? "Draft" : "")),
          h("div", { class: "md", html: pr.body ? md(pr.body) : "<em>No description provided.</em>" }))
      );

      // Comments
      if (pr.comments?.length) {
        container.append(h("div", { class: "p-section" }, `Comments (${pr.comments.length})`));
        for (const c of pr.comments) {
          container.append(
            h("div", { class: "gh-card" },
              h("div", { class: "gh-card-head" },
                h("span", { style: "font-weight:600;color:var(--text)" }, c.author),
                h("span", {}, timeAgo(c.created_at))),
              h("div", { class: "md", html: md(c.body || "") }))
          );
        }
      }

      // Add comment box
      const commentInput = h("textarea", { placeholder: "Leave a comment (Markdown supported)…" });
      const commentBtn = h("button", { class: "btn primary sm", onclick: async () => {
        const body = commentInput.value.trim();
        if (!body) return;
        commentBtn.disabled = true;
        commentBtn.textContent = "Commenting…";
        try {
          await api.post(`${BASE}/repo/${owner}/${name}/pulls/${pr.number}/comment`, { body });
          toast?.("Comment posted");
          await openPR(state.repoSlug, pr.number);
        } catch (err) {
          toast?.(`Comment failed: ${err.message}`);
          commentBtn.disabled = false;
          commentBtn.textContent = "Comment";
        }
      } }, "Comment");

      container.append(
        h("div", { class: "gh-card gh-comment-box" },
          h("div", { class: "gh-card-title", style: "margin-bottom:8px" }, "Add a comment"),
          h("div", { class: "gh-comment-form" },
            commentInput,
            h("div", { style: "display:flex;justify-content:flex-end" }, commentBtn)))
      );
    } else {
      // Diff View
      if (pr.files?.length) {
        const filesList = h("div", { class: "gh-files-list" },
          ...pr.files.map((f) => h("div", { class: "gh-file-row" },
            h("span", {}, f.name),
            h("span", { class: "gh-file-stats" },
              f["+"] ? h("span", { class: "add" }, `+${f["+"]}`) : null,
              f["-"] ? h("span", { class: "del" }, `-${f["-"]}`) : null)))
        );
        container.append(filesList);
      }

      if (!state.diffText) {
        container.append(h("div", { class: "p-empty" }, "Loading diff…"));
        try {
          const diffRes = await api.get(`${BASE}/repo/${owner}/${name}/pulls/${pr.number}/diff`);
          state.diffText = diffRes.diff || "";
        } catch (err) {
          container.replaceChildren(h("div", { class: "p-empty" }, `Failed to load diff: ${err.message}`));
          return;
        }
      }

      container.replaceChildren();
      if (pr.files?.length) {
        container.append(
          h("div", { class: "gh-files-list" },
            ...pr.files.map((f) => h("div", { class: "gh-file-row" },
              h("span", {}, f.name),
              h("span", { class: "gh-file-stats" },
                f["+"] ? h("span", { class: "add" }, `+${f["+"]}`) : null,
                f["-"] ? h("span", { class: "del" }, `-${f["-"]}`) : null))))
        );
      }

      if (!state.diffText.trim()) {
        container.append(h("div", { class: "p-empty" }, "No changes in this diff."));
        return;
      }

      const diffBox = h("div", { class: "gh-diff-box" });
      const lines = state.diffText.split("\n");
      for (const line of lines) {
        let cls = "gh-diff-line";
        if (line.startsWith("diff --git")) cls += " gh-diff-file";
        else if (line.startsWith("@@")) cls += " gh-diff-hunk";
        else if (line.startsWith("+") && !line.startsWith("+++")) cls += " gh-diff-add";
        else if (line.startsWith("-") && !line.startsWith("---")) cls += " gh-diff-del";
        diffBox.append(h("div", { class: cls }, line || " "));
      }
      container.append(diffBox);
    }
  }

  // ---- BRANCHES TAB -------------------------------------------------
  async function renderBranchesTab(container) {
    const [owner, name] = state.repoSlug.split("/");
    container.replaceChildren(h("div", { class: "p-empty" }, "Loading branches…"));

    if (state.creatingBranch) {
      renderNewBranchForm(container);
      return;
    }

    let branches = [];
    try {
      const res = await api.get(`${BASE}/repo/${owner}/${name}/branches`);
      branches = res.branches || [];
    } catch (err) {
      container.replaceChildren(h("div", { class: "p-empty" }, `Failed to load branches: ${err.message}`));
      return;
    }

    container.replaceChildren();

    const branchSearch = h("input", {
      type: "search",
      placeholder: "Filter branches…",
      value: state.branchSearch,
      "aria-label": "Filter branches",
      style: "max-width:240px",
    });
    branchSearch.addEventListener("input", () => {
      state.branchSearch = branchSearch.value.trim().toLowerCase();
      paintBranches();
    });

    const newBranchBtn = h("button", {
      class: "btn primary sm",
      onclick: () => { state.creatingBranch = true; renderNewBranchForm(container, branches); },
    }, h("span", { html: icon("plus", 13) }), " New branch");

    container.append(h("div", { class: "gh-bar" }, branchSearch, newBranchBtn));

    const listWrap = h("div", { class: "gh-row-list" });
    container.append(listWrap);

    function paintBranches() {
      listWrap.replaceChildren();
      const q = state.branchSearch.toLowerCase();
      const filtered = branches.filter((b) => !q || b.name.toLowerCase().includes(q));

      if (!filtered.length) {
        listWrap.append(h("div", { class: "p-empty" }, "No branches match."));
        return;
      }

      const defBranch = state.repoData?.default_branch || "main";

      for (const b of filtered) {
        const isDefault = b.name === defBranch;
        const delBtn = !isDefault ? h("button", {
          class: "icon-btn sm",
          title: `Delete branch ${b.name}`,
          "aria-label": `Delete branch ${b.name}`,
          onclick: async (ev) => {
            ev.stopPropagation();
            if (!confirm(`Delete branch “${b.name}”?`)) return;
            try {
              await api.del(`${BASE}/repo/${owner}/${name}/branches/${encodeURIComponent(b.name)}`);
              toast?.(`Deleted branch ${b.name}`);
              renderBranchesTab(container);
            } catch (err) {
              toast?.(`Delete failed: ${err.message}`);
            }
          },
          html: icon("trash", 13),
        }) : null;

        listWrap.append(
          h("div", { class: "gh-row", style: "cursor:default" },
            h("span", { html: icon("git-branch", 14), style: "color:var(--faint);flex:none" }),
            h("div", { class: "gh-row-name" },
              h("span", { class: "gh-row-title" }, b.name),
              isDefault ? h("span", { class: "gh-badge tag" }, "default") : null,
              b.protected ? h("span", { class: "gh-badge priv" }, "protected") : null),
            h("div", { class: "grow" }),
            h("div", { class: "gh-row-meta" },
              h("button", {
                class: "btn sm",
                onclick: () => {
                  state.tab = "prs";
                  state.creatingPR = true;
                  switchTab("prs");
                },
              }, "New PR"),
              delBtn))
        );
      }
    }

    paintBranches();
  }

  async function renderNewBranchForm(container, existingBranches = []) {
    const [owner, name] = state.repoSlug.split("/");
    container.replaceChildren();

    const nameInput = h("input", { type: "text", placeholder: "New branch name", "aria-label": "Branch name" });
    const fromSelect = h("select", { "aria-label": "Base branch" },
      ...existingBranches.map((b) => h("option", { value: b.name, selected: b.name === (state.repoData?.default_branch || "main") }, b.name)));

    const errorBox = h("div", { class: "p-s", style: "color:var(--danger)" });

    const submitBtn = h("button", { class: "btn primary sm", onclick: create }, "Create branch");
    const cancelBtn = h("button", { class: "btn sm", onclick: () => { state.creatingBranch = false; renderBranchesTab(container); } }, "Cancel");

    async function create() {
      const branchName = nameInput.value.trim();
      if (!branchName) { errorBox.textContent = "Branch name is required."; return; }
      submitBtn.disabled = true;
      submitBtn.textContent = "Creating…";
      try {
        await api.post(`${BASE}/repo/${owner}/${name}/branches`, {
          branch: branchName,
          from_branch: fromSelect.value,
        });
        toast?.(`Created branch ${branchName}`);
        state.creatingBranch = false;
        renderBranchesTab(container);
      } catch (err) {
        errorBox.textContent = err.message || "Failed to create branch.";
        submitBtn.disabled = false;
        submitBtn.textContent = "Create branch";
      }
    }

    container.append(
      h("div", { class: "gh-card-title", style: "margin-bottom:8px" }, "New Branch"),
      h("div", { class: "gh-form" },
        h("div", { class: "gh-form-row" },
          h("label", {}, "Base:"),
          fromSelect),
        nameInput,
        errorBox,
        h("div", { style: "display:flex;gap:8px;margin-top:6px" }, submitBtn, cancelBtn))
    );
  }

  // ---- ISSUES TAB ---------------------------------------------------
  async function renderIssuesTab(container) {
    const [owner, name] = state.repoSlug.split("/");
    container.replaceChildren(h("div", { class: "p-empty" }, "Loading issues…"));

    if (state.creatingIssue) {
      renderNewIssueForm(container);
      return;
    }

    let issues = [];
    try {
      const res = await api.get(`${BASE}/repo/${owner}/${name}/issues?state=${state.issueFilter}`);
      issues = res.issues || [];
    } catch (err) {
      container.replaceChildren(h("div", { class: "p-empty" }, `Failed to load issues: ${err.message}`));
      return;
    }

    container.replaceChildren();

    // Toolbar: Filter pills + New Issue button
    const filterPills = h("div", { class: "gh-pills" },
      ...["open", "closed", "all"].map((st) => h("button", {
        class: `gh-pill ${state.issueFilter === st ? "active" : ""}`,
        onclick: () => { state.issueFilter = st; renderIssuesTab(container); },
      }, st[0].toUpperCase() + st.slice(1)))
    );

    const newIssueBtn = h("button", {
      class: "btn primary sm",
      onclick: () => { state.creatingIssue = true; renderNewIssueForm(container); },
    }, h("span", { html: icon("plus", 13) }), " New issue");

    container.append(
      h("div", { class: "gh-bar" }, filterPills, newIssueBtn)
    );

    if (!issues.length) {
      container.append(h("div", { class: "p-empty" }, `No ${state.issueFilter} issues.`));
      return;
    }

    const rowList = h("div", { class: "gh-row-list" });
    container.append(rowList);

    for (const issue of issues) {
      rowList.append(
        h("div", {
          class: "gh-row",
          onclick: () => openIssue(state.repoSlug, issue.number),
          role: "button",
          tabindex: "0",
        },
          h("span", { class: `gh-badge ${issue.state}`, style: "flex:none" }, issue.state),
          h("div", { class: "gh-row-name" },
            h("span", { class: "gh-row-title" }, `#${issue.number} ${issue.title}`)),
          h("div", { class: "gh-row-desc" },
            `by ${issue.author}`,
            issue.labels?.length ? ` · ${issue.labels.join(", ")}` : ""),
          h("div", { class: "gh-row-meta" },
            h("span", {}, `${issue.comments} comments`),
            h("span", {}, timeAgo(issue.updated_at))))
      );
    }
  }

  async function renderNewIssueForm(container) {
    const [owner, name] = state.repoSlug.split("/");
    container.replaceChildren();

    const titleInput = h("input", { type: "text", placeholder: "Issue title", "aria-label": "Title" });
    const labelsInput = h("input", { type: "text", placeholder: "Labels (comma separated, e.g. bug, enhancement)" });
    const bodyInput = h("textarea", { placeholder: "Describe the issue (Markdown supported)…" });

    const errorBox = h("div", { class: "p-s", style: "color:var(--danger)" });

    const submitBtn = h("button", { class: "btn primary sm", onclick: create }, "Create issue");
    const cancelBtn = h("button", { class: "btn sm", onclick: () => { state.creatingIssue = false; renderIssuesTab(container); } }, "Cancel");

    async function create() {
      const title = titleInput.value.trim();
      if (!title) { errorBox.textContent = "Title is required."; return; }
      submitBtn.disabled = true;
      submitBtn.textContent = "Creating…";
      const labels = labelsInput.value.split(",").map((s) => s.trim()).filter(Boolean);
      try {
        const res = await api.post(`${BASE}/repo/${owner}/${name}/issues`, {
          title,
          body: bodyInput.value,
          labels,
        });
        toast?.(`Created issue #${res.created?.split("#")[1] || ""}`);
        state.creatingIssue = false;
        renderIssuesTab(container);
      } catch (err) {
        errorBox.textContent = err.message || "Failed to create issue.";
        submitBtn.disabled = false;
        submitBtn.textContent = "Create issue";
      }
    }

    container.append(
      h("div", { class: "gh-card-title", style: "margin-bottom:8px" }, "New Issue"),
      h("div", { class: "gh-form" },
        titleInput,
        labelsInput,
        bodyInput,
        errorBox,
        h("div", { style: "display:flex;gap:8px;margin-top:6px" }, submitBtn, cancelBtn))
    );
  }

  // ---- ISSUE DETAIL VIEW --------------------------------------------
  async function openIssue(repoSlug, number) {
    state.view = "issue";
    state.repoSlug = repoSlug;
    emit("screen");
    paintSidebar();

    mainEl.replaceChildren();
    const wrap = h("div", { class: "gh-wrap" });
    mainEl.append(wrap);

    const [owner, name] = repoSlug.split("/");
    wrap.append(h("div", { class: "p-empty" }, `Loading issue #${number}…`));

    let issue;
    try {
      issue = await api.get(`${BASE}/repo/${owner}/${name}/issues/${number}`);
      state.activeIssue = issue;
    } catch (err) {
      wrap.replaceChildren(
        h("div", { class: "p-empty" },
          h("div", { class: "p-s", style: "margin-bottom:12px" }, `Error loading issue #${number}: ${err.message}`),
          h("button", { class: "btn sm", onclick: () => openRepo(repoSlug) }, "← Back to repo"))
      );
      return;
    }

    renderIssueDetailView(wrap, issue);
  }

  function renderIssueDetailView(wrap, issue) {
    wrap.replaceChildren();
    const [owner, name] = state.repoSlug.split("/");

    // Header & Breadcrumb
    wrap.append(
      h("div", { class: "gh-header" },
        h("div", { class: "gh-breadcrumb" },
          h("button", {
            class: `gh-sidebar-toggle ${state.sidebarOpen ? "active" : ""}`,
            title: "Toggle saved repositories",
            "aria-label": "Toggle saved repositories",
            onclick: () => toggleSidebar(),
            html: icon("menu", 14),
          }),
          h("button", { onclick: () => openRepo(state.repoSlug) }, state.repoSlug),
          h("span", { class: "sep" }, "/"),
          h("span", { class: "current" }, `#${issue.number}`)),
        h("div", { class: "gh-actions" },
          h("button", { class: "btn sm", onclick: () => prompt(`Summarize issue ${state.repoSlug}#${issue.number}`) }, "Ask agent"),
          issue.url ? h("a", { class: "icon-btn sm", href: issue.url, target: "_blank", rel: "noopener noreferrer", title: "Open on GitHub", "aria-label": "Open on GitHub", html: icon("external", 14) }) : null)
      ),
      // Issue Hero
      h("div", { class: "gh-repo-hero" },
        h("div", { style: "display:flex;align-items:flex-start;justify-content:space-between;gap:8px" },
          h("div", { style: "font-size:15px;font-weight:600" }, issue.title),
          h("span", { class: `gh-badge ${issue.state}` }, issue.state)),
        h("div", { class: "gh-repo-meta" },
          h("span", {}, `by ${issue.author}`),
          issue.labels?.length ? h("span", {}, issue.labels.map((l) => `#${l}`).join(" ")) : null,
          h("span", {}, `updated ${timeAgo(issue.updated_at)}`)))
    );

    // Close / Reopen toolbar
    const closeBtn = h("button", {
      class: `btn sm ${issue.state === "open" ? "danger" : ""}`,
      onclick: async () => {
        const nextState = issue.state === "open" ? "closed" : "open";
        closeBtn.disabled = true;
        try {
          await api.post(`${BASE}/repo/${owner}/${name}/issues/${issue.number}/close`, { state: nextState });
          toast?.(nextState === "closed" ? `Closed issue #${issue.number}` : `Reopened issue #${issue.number}`);
          await openIssue(state.repoSlug, issue.number);
        } catch (err) {
          toast?.(`Action failed: ${err.message}`);
          closeBtn.disabled = false;
        }
      },
    }, issue.state === "open" ? "Close issue" : "Reopen issue");

    wrap.append(h("div", { style: "display:flex;gap:8px;margin:6px 0" }, closeBtn));

    // Issue Description Card
    wrap.append(
      h("div", { class: "gh-card" },
        h("div", { class: "gh-card-head" },
          h("span", {}, `${issue.author} opened this issue`),
          h("span", {}, issue.state)),
        h("div", { class: "md", html: issue.body ? md(issue.body) : "<em>No description provided.</em>" }))
    );

    // Comments
    if (issue.comments?.length) {
      wrap.append(h("div", { class: "p-section" }, `Comments (${issue.comments.length})`));
      for (const c of issue.comments) {
        wrap.append(
          h("div", { class: "gh-card" },
            h("div", { class: "gh-card-head" },
              h("span", { style: "font-weight:600;color:var(--text)" }, c.author),
              h("span", {}, timeAgo(c.created_at))),
            h("div", { class: "md", html: md(c.body || "") }))
        );
      }
    }

    // Add comment box
    const commentInput = h("textarea", { placeholder: "Leave a comment (Markdown supported)…" });
    const commentBtn = h("button", { class: "btn primary sm", onclick: async () => {
      const body = commentInput.value.trim();
      if (!body) return;
      commentBtn.disabled = true;
      commentBtn.textContent = "Commenting…";
      try {
        await api.post(`${BASE}/repo/${owner}/${name}/issues/${issue.number}/comment`, { body });
        toast?.("Comment posted");
        await openIssue(state.repoSlug, issue.number);
      } catch (err) {
        toast?.(`Comment failed: ${err.message}`);
        commentBtn.disabled = false;
        commentBtn.textContent = "Comment";
      }
    } }, "Comment");

    wrap.append(
      h("div", { class: "gh-card gh-comment-box" },
        h("div", { class: "gh-card-title", style: "margin-bottom:8px" }, "Add a comment"),
        h("div", { class: "gh-comment-form" },
          commentInput,
          h("div", { style: "display:flex;justify-content:flex-end" }, commentBtn)))
    );
  }

  // ---- OVERVIEW / README TAB ----------------------------------------
  async function renderReadmeTab(container) {
    const [owner, name] = state.repoSlug.split("/");
    container.replaceChildren(h("div", { class: "p-empty" }, "Loading README…"));

    let readmeText = "";
    try {
      const res = await api.get(`${BASE}/repo/${owner}/${name}/readme`);
      readmeText = res.readme || "";
    } catch {
      readmeText = "";
    }

    container.replaceChildren();

    if (!readmeText.trim()) {
      container.append(h("div", { class: "p-empty" }, "No README found for this repository."));
      return;
    }

    container.append(
      h("div", { class: "gh-card" },
        h("div", { class: "gh-card-head" },
          h("span", { style: "font-weight:600" }, "README.md")),
        h("div", { class: "md", html: md(readmeText) }))
    );
  }
}
