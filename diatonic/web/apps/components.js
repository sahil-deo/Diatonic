// Components: every piece of the UI kit (js/ui/) with its props and a live example, for checking by eye and for choosing what to build with.
import { components } from "/js/ui/index.js";

export const wide = true;

export async function render(el, ctx) {
  const { h } = ctx;
  const decl = await fetch("/js/ui/components.json").then((r) => r.json());
  const filter = h("input", { type: "search", placeholder: "Filter components", "aria-label": "Filter components", oninput: () => paint() });
  const out = h("div");
  const paint = () => {
    const q = filter.value.trim().toLowerCase();
    out.replaceChildren(...Object.entries(decl).filter(([name, d]) => !q || name.includes(q) || d.doc.toLowerCase().includes(q)).map(([name, d]) => {
      let live;
      try { live = components[name](d.example); } catch (e) { live = h("div", { class: "err" }, `${name} failed to render: ${e.message}`); }
      return ctx.ui.card({
        title: `ctx.ui.${name}`, subtitle: d.doc,
        body: [h("div", { class: "comp-live" }, live),
          ctx.ui.table({ columns: [{ key: "name", label: "Prop" }, { key: "type", label: "Type" }, { key: "doc", label: "What it does" }],
            rows: d.props.map((p) => ({ name: p.name + (p.required ? " *" : ""), type: p.type, doc: p.doc })) }),
          h("pre", { class: "comp-code" }, `ctx.ui.${name}(${JSON.stringify(d.example)})`)],
      });
    }));
    if (!out.children.length) out.append(ctx.ui.empty({ title: "No component matches", text: "Clear the filter to see all of them." }));
  };
  el.append(h("div", { class: "ui-la-bar" }, filter), out);
  paint();
}
