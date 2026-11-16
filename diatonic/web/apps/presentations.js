// Presentations: Markdown slide deck manager, editor, and fullscreen slideshow presenter.
import { render as md } from "/js/md.js";
import { icon } from "/js/icons.js";
import { emit, state } from "/js/store.js";

const BASE = "/api/apps/presentations";
export const wide = true;

const instances = new WeakMap(); // state-ok: keyed by this panel's ctx
export const current = (ctx) => {
  const open = instances.get(ctx)?.getOpen?.();
  return open ? { kind: "presentation", ref: open, title: open } : null;
};

/** Called by agent or navigation: open deck by slug/title, or present a file. */
export async function navigate(p, ctx) {
  const nav = instances.get(ctx)?.nav;
  if (!nav) return;
  if (p.file || p.path) return nav.presentFile(p.file || p.path);
  if (p.item || p.deck) return nav.edit(p.item || p.deck, p.present);
  return nav.list(p.query || "");
}

export async function render(el, ctx) {
  const { api, h, toast } = ctx;
  let open = null;
  const nav = { list, edit, present, presentFile };
  instances.set(ctx, { nav, getOpen: () => open });

  if (state.openPresentation) {
    const slug = state.openPresentation;
    state.openPresentation = null;
    await edit(slug);
    return;
  }

  await list("");

  /* ---------------------------------------------------------------- Deck Library */
  async function list(q) {
    el.replaceChildren(); delete el.dataset.dirty; open = null; emit("screen");
    const d = await api.get(`${BASE}/decks${q ? `?q=${encodeURIComponent(q)}` : ""}`).catch((e) => {
      toast("Couldn't load presentations: " + e.message, "error");
      return { folder: "", decks: [] };
    });

    const search = h("input", { type: "search", placeholder: "Search presentations…", value: q, "aria-label": "Search presentations" });
    let timer;
    search.addEventListener("input", () => {
      clearTimeout(timer);
      timer = setTimeout(() => list(search.value.trim()).then(() => el.querySelector("input[type=search]")?.focus()), 250);
    });

    const head = h("div", { class: "quick-add" },
      search,
      h("button", { class: "btn primary sm", onclick: () => edit(null) }, "+ New deck")
    );

    const countInfo = h("div", { class: "p-s" }, `${d.decks.length} presentation${d.decks.length === 1 ? "" : "s"} in ${d.folder}`);

    const cards = d.decks.map((deck) => {
      const slideBadge = h("span", { class: "pres-badge" }, `${deck.slides_count} slide${deck.slides_count === 1 ? "" : "s"}`);
      const dateStr = new Date(deck.modified * 1000).toLocaleDateString([], { month: "short", day: "numeric", year: "numeric" });
      
      return h("div", { class: "pres-card" },
        h("div", { class: "pres-card-header" },
          h("div", { class: "pres-card-title", onclick: () => edit(deck.slug) }, deck.title),
          slideBadge
        ),
        h("div", { class: "pres-card-preview", onclick: () => edit(deck.slug) }, deck.preview || "(empty presentation)"),
        h("div", { class: "pres-card-footer" },
          h("span", { class: "p-s muted" }, dateStr),
          h("div", { class: "pres-card-actions" },
            h("button", { class: "btn sm primary", onclick: (e) => { e.stopPropagation(); present(deck.slug); } },
              h("span", { html: icon("presentation", 14) }), "Present"
            ),
            h("button", { class: "btn sm", onclick: (e) => { e.stopPropagation(); edit(deck.slug); } }, "Edit"),
            h("button", { class: "icon-btn sm del", title: "Delete presentation", onclick: async (e) => {
              e.stopPropagation();
              if (confirm(`Move “${deck.title}” to trash?`)) {
                await api.del(`${BASE}/decks/${encodeURIComponent(deck.slug)}`);
                toast(`Deleted “${deck.title}”`);
                list(q);
              }
            } }, h("span", { html: icon("trash", 14) }))
          )
        )
      );
    });

    const empty = !d.decks.length ? h("div", { class: "p-empty" },
      q ? "No presentations match your search." : "No presentations yet. Click “+ New deck” or ask the agent to create one."
    ) : null;

    const kids = [head, countInfo, h("div", { class: "pres-grid" }, ...cards)];
    if (empty) kids.push(empty);
    el.append(...kids);
  }

  /* ---------------------------------------------------------------- Editor */
  async function edit(slug, autoPresent = false) {
    el.replaceChildren(); delete el.dataset.dirty;
    let deck;
    if (slug) {
      try { deck = await api.get(`${BASE}/decks/${encodeURIComponent(slug)}`); }
      catch (e) { toast(e.message, "error"); return list(""); }
    } else {
      deck = {
        title: "",
        slug: "",
        raw: "# My Presentation\n\nWelcome to Markdown slides!\n\n---\n\n## Slide 2\n\n- Write standard markdown\n- Use `---` between slides\n- Press **Present** to run full-screen\n\n<!-- note: Remember to introduce the team -->\n\n---\n\n## Code Example\n\n```python\ndef hello_diatonic():\n    return \"Hello, world!\"\n```",
        slides: [],
      };
    }

    open = deck.title || "Untitled";
    emit("screen");

    let curSlug = deck.slug;
    let saveTimer;
    let previewSlideIdx = 0;

    const titleInput = h("input", { type: "text", class: "note-title pres-title-input", placeholder: "Presentation Title", value: deck.title });
    const contentTextarea = h("textarea", { class: "pres-editor-textarea", placeholder: "Write markdown slides separated by ---…", spellcheck: "true" }, deck.raw);
    const status = h("span", { class: "p-s" }, curSlug ? "Saved" : "");
    const slidePreview = h("div", { class: "pres-mini-slide md" });
    const slideCounter = h("span", { class: "p-s" }, "");

    function parseEditorSlides() {
      const raw = contentTextarea.value;
      const parts = raw.replace(/^---\n[\s\S]*?\n---\n?/, "").split(/\n\s*---\s*\n/);
      return parts.map((s, i) => ({ index: i + 1, content: s.trim() })).filter((s) => s.content.length > 0);
    }

    function updatePreview() {
      const slides = parseEditorSlides();
      if (!slides.length) {
        slidePreview.innerHTML = "<p class='muted'>Empty deck</p>";
        slideCounter.textContent = "0 slides";
        return;
      }
      if (previewSlideIdx >= slides.length) previewSlideIdx = slides.length - 1;
      if (previewSlideIdx < 0) previewSlideIdx = 0;

      const cur = slides[previewSlideIdx];
      // strip speaker notes from preview
      const clean = cur.content.replace(/<!--\s*notes?:\s*[\s\S]*?-->/gi, "").replace(/\n+(?:Notes?|Speaker Notes?):\s*[\s\S]*\Z/gi, "");
      slidePreview.innerHTML = md(clean);
      slideCounter.textContent = `Slide ${previewSlideIdx + 1} of ${slides.length}`;
    }

    const save = async () => {
      clearTimeout(saveTimer);
      const title = titleInput.value.trim() || "Untitled";
      const content = contentTextarea.value;
      status.textContent = "Saving…";
      try {
        const payload = { title, content };
        const res = curSlug
          ? await api.put(`${BASE}/decks/${encodeURIComponent(curSlug)}`, payload)
          : await api.post(`${BASE}/decks`, payload);
        curSlug = res.slug;
        open = res.title || title;
        delete el.dataset.dirty;
        status.textContent = "Saved";
      } catch (e) {
        status.textContent = "Error saving: " + e.message;
      }
    };

    const markDirty = () => {
      el.dataset.dirty = "1";
      status.textContent = "Editing…";
      clearTimeout(saveTimer);
      saveTimer = setTimeout(save, 900);
      updatePreview();
    };

    titleInput.addEventListener("input", markDirty);
    contentTextarea.addEventListener("input", markDirty);

    el.addEventListener("keydown", (e) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "s") {
        e.preventDefault();
        save();
      }
    });

    // Helper buttons
    const insertText = (snippet) => {
      const ta = contentTextarea;
      const start = ta.selectionStart, end = ta.selectionEnd;
      ta.value = ta.value.substring(0, start) + snippet + ta.value.substring(end);
      ta.selectionStart = ta.selectionEnd = start + snippet.length;
      ta.focus();
      markDirty();
    };

    const addSlideBtn = h("button", { class: "btn sm", type: "button", onclick: () => insertText("\n\n---\n\n## New Slide\n\n- Point 1\n- Point 2\n") }, "+ Slide");
    const addCodeBtn = h("button", { class: "btn sm", type: "button", onclick: () => insertText("\n```python\n# code here\n```\n") }, "+ Code");
    const addNotesBtn = h("button", { class: "btn sm", type: "button", onclick: () => insertText("\n<!-- note: Speaker note here -->\n") }, "+ Note");

    const prevSlideBtn = h("button", { class: "btn sm", type: "button", onclick: () => { previewSlideIdx--; updatePreview(); } }, "‹");
    const nextSlideBtn = h("button", { class: "btn sm", type: "button", onclick: () => { previewSlideIdx++; updatePreview(); } }, "›");

    const presentBtn = h("button", { class: "btn sm primary", type: "button", onclick: async () => {
      await save();
      present(curSlug);
    } }, h("span", { html: icon("presentation", 14) }), "Present Deck");

    const navBar = h("div", { class: "pres-editor-bar" },
      h("button", { class: "btn sm", type: "button", onclick: async () => { await save(); list(""); } }, "← Presentations"),
      h("div", { class: "grow" }),
      status,
      presentBtn,
      curSlug && h("button", { class: "btn sm danger", type: "button", onclick: async () => {
        if (confirm("Move this presentation to trash?")) {
          await api.del(`${BASE}/decks/${encodeURIComponent(curSlug)}`);
          list("");
        }
      } }, "Delete")
    );

    const toolbar = h("div", { class: "pres-toolbar" },
      h("span", { class: "p-s muted" }, "Insert:"),
      addSlideBtn, addCodeBtn, addNotesBtn,
      h("div", { class: "grow" }),
      h("span", { class: "p-s muted" }, "Preview:"),
      prevSlideBtn, slideCounter, nextSlideBtn
    );

    const editorBody = h("div", { class: "pres-editor-split" },
      h("div", { class: "pres-editor-col" }, contentTextarea),
      h("div", { class: "pres-preview-col" }, slidePreview)
    );

    el.replaceChildren(navBar, titleInput, toolbar, editorBody);
    updatePreview();

    if (autoPresent && curSlug) {
      present(curSlug);
    }
  }

  /* ---------------------------------------------------------------- Slideshow Presenter */
  async function present(slug) {
    let deck;
    try {
      deck = await api.get(`${BASE}/decks/${encodeURIComponent(slug)}`);
    } catch (e) {
      toast("Couldn't open presentation: " + e.message, "error");
      return;
    }
    runSlideshow(deck.title, deck.slides);
  }

  async function presentFile(filePath) {
    try {
      const res = await api.post(`${BASE}/parse`, { path: filePath });
      runSlideshow(res.title, res.slides);
    } catch (e) {
      toast("Couldn't present file: " + e.message, "error");
    }
  }

  function runSlideshow(title, slides) {
    if (!slides || !slides.length) {
      toast("This presentation has no slides.", "warn");
      return;
    }

    let currentIdx = 0;
    let showNotes = false;

    // Overlay
    const overlay = h("div", { class: "pres-overlay" });
    const progressBar = h("div", { class: "pres-progress-bar" });
    const stage = h("div", { class: "pres-stage" });
    const slideBox = h("div", { class: "pres-slide md" });
    const notesDrawer = h("div", { class: "pres-notes-drawer", hidden: true });

    // HUD controls
    const hud = h("div", { class: "pres-hud" });
    const hudTitle = h("div", { class: "pres-hud-title" }, title);
    const hudCounter = h("div", { class: "pres-hud-counter" });
    const prevBtn = h("button", { class: "icon-btn sm", title: "Previous (←, PgUp)", onclick: (e) => { e.stopPropagation(); prevSlide(); } },
      h("span", { html: icon("chevleft", 16) })
    );
    const nextBtn = h("button", { class: "icon-btn sm", title: "Next (→, Space, PgDn)", onclick: (e) => { e.stopPropagation(); nextSlide(); } },
      h("span", { html: icon("chevright", 16) })
    );
    const notesToggle = h("button", { class: "btn sm", title: "Toggle speaker notes (N)", onclick: (e) => { e.stopPropagation(); toggleNotes(); } }, "Notes");
    const fsToggle = h("button", { class: "icon-btn sm", title: "Toggle full screen (F)", onclick: (e) => { e.stopPropagation(); toggleFullscreen(); } },
      h("span", { html: icon("maximize", 16) })
    );
    const exitBtn = h("button", { class: "icon-btn sm", title: "Exit presentation (Esc)", onclick: (e) => { e.stopPropagation(); close(); } },
      h("span", { html: icon("x", 16) })
    );

    hud.append(prevBtn, nextBtn, hudCounter, h("div", { class: "pres-hud-sep" }), hudTitle, h("div", { class: "grow" }), notesToggle, fsToggle, exitBtn);
    stage.append(slideBox, notesDrawer);
    overlay.append(progressBar, stage, hud);
    document.body.append(overlay);

    // Try native fullscreen
    try {
      if (document.fullscreenEnabled && !document.fullscreenElement) {
        overlay.requestFullscreen?.().catch(() => {});
      }
    } catch {}

    function renderCurrentSlide() {
      const s = slides[currentIdx];
      slideBox.innerHTML = md(s.content);
      hudCounter.textContent = `${currentIdx + 1} / ${slides.length}`;
      
      const pct = ((currentIdx + 1) / slides.length) * 100;
      progressBar.style.width = `${pct}%`;

      if (s.notes) {
        notesToggle.classList.add("has-notes");
        notesDrawer.innerHTML = `<strong>Speaker Notes:</strong><br>${esc(s.notes).replace(/\n/g, "<br>")}`;
      } else {
        notesToggle.classList.remove("has-notes");
        notesDrawer.innerHTML = "<span class='muted'>No notes for this slide.</span>";
      }
      notesDrawer.hidden = !showNotes;
    }

    function nextSlide() {
      if (currentIdx < slides.length - 1) {
        currentIdx++;
        renderCurrentSlide();
      }
    }

    function prevSlide() {
      if (currentIdx > 0) {
        currentIdx--;
        renderCurrentSlide();
      }
    }

    function toggleNotes() {
      showNotes = !showNotes;
      notesDrawer.hidden = !showNotes;
      notesToggle.classList.toggle("active", showNotes);
    }

    function toggleFullscreen() {
      if (!document.fullscreenElement) {
        overlay.requestFullscreen?.().catch(() => {});
      } else {
        document.exitFullscreen?.().catch(() => {});
      }
    }

    function handleKeyDown(e) {
      if (e.key === "ArrowRight" || e.key === " " || e.key === "PageDown" || e.key === "Enter" || e.key.toLowerCase() === "l") {
        e.preventDefault();
        nextSlide();
      } else if (e.key === "ArrowLeft" || e.key === "PageUp" || e.key === "Backspace" || e.key.toLowerCase() === "h") {
        e.preventDefault();
        prevSlide();
      } else if (e.key === "Home") {
        e.preventDefault();
        currentIdx = 0;
        renderCurrentSlide();
      } else if (e.key === "End") {
        e.preventDefault();
        currentIdx = slides.length - 1;
        renderCurrentSlide();
      } else if (e.key === "Escape") {
        e.preventDefault();
        close();
      } else if (e.key.toLowerCase() === "f") {
        e.preventDefault();
        toggleFullscreen();
      } else if (e.key.toLowerCase() === "n") {
        e.preventDefault();
        toggleNotes();
      }
    }

    // Click left third for prev, right two-thirds for next
    overlay.addEventListener("click", (e) => {
      if (e.target.closest(".pres-hud") || e.target.closest("a") || e.target.closest("button") || e.target.closest("pre") || e.target.closest(".pres-notes-drawer")) {
        return;
      }
      const x = e.clientX;
      const w = window.innerWidth;
      if (x < w * 0.25) prevSlide();
      else nextSlide();
    });

    window.addEventListener("keydown", handleKeyDown);

    function close() {
      window.removeEventListener("keydown", handleKeyDown);
      if (document.fullscreenElement) {
        document.exitFullscreen?.().catch(() => {});
      }
      overlay.remove();
    }

    renderCurrentSlide();
  }
}

function esc(s) {
  return String(s || "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}
