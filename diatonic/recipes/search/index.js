export async function render(el, ctx) {
  const { ui, h } = ctx;
  const results = h("div");
  const show = (pages) => results.replaceChildren(ui.list({ empty: "No pages found.", items: pages.map((p) => ({
    title: p.title, subtitle: p.description || "",
    right: h("a", { href: `https://en.wikipedia.org/wiki/${encodeURIComponent(p.key)}`, target: "_blank", rel: "noopener" }, "Open") })) }));
  const search = async (q) => {
    results.replaceChildren(ui.empty({ title: "Searching" }));
    try {
      const r = await ctx.fetch(`https://en.wikipedia.org/w/rest.php/v1/search/page?limit=8&q=${encodeURIComponent(q)}`);
      if (!r.ok) throw new Error(`The site answered ${r.status}.`);
      show(r.json().pages || []);
    } catch (e) {
      results.replaceChildren(ui.empty({ title: "The search did not work", text: e.message }));
      throw e;
    }
  };
  const form = ui.form({
    submit: "Search", fields: [{ name: "q", label: "Search", placeholder: "What do you want to read about?" }],
    onSubmit: (v) => ctx.ui.run("Search", () => { if (!v.q.trim()) throw new Error("Type something to search for."); return search(v.q.trim()); }),
  });
  el.append(ui.card({ title: "Wikipedia", body: form }), results);
  results.append(ui.empty({ title: "Search for something", text: "Results appear here." }));
}
