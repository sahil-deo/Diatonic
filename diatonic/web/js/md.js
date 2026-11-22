// Tiny, safe markdown renderer: escapes everything first, then applies a small subset.
import { esc } from "./dom.js";
import { highlight } from "./highlight.js";

let OPTS = {};
const inline = (s) =>
  s.replace(/`([^`\n]+)`/g, "<code>$1</code>")
   .replace(/!\[([^\]]*)\]\(([^\s)]+)\)/g, (m, alt, url) => { const u = OPTS.resolveImage ? OPTS.resolveImage(url) : (/^https?:/.test(url) ? url : null); return u ? `<img src="${u}" alt="${alt}" loading="lazy">` : m; })
   .replace(/\[([^\]]+)\]\((?!https?:)([^\s)#]+)(#[^\s)]*)?\)/g, (m, t, url) => OPTS.fileLink ? `<a href="#" data-file="${OPTS.fileLink(url)}">${t}</a>` : m)
   .replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>")
   .replace(/__([^_\n]+)__/g, "<strong>$1</strong>")
   .replace(/(^|[^*\w])\*([^*\n]+)\*(?!\w)/g, "$1<em>$2</em>")
   .replace(/(^|[^_\w])_([^_\n]+)_(?!\w)/g, "$1<em>$2</em>")
   .replace(/~~([^~\n]+)~~/g, "<del>$1</del>")
   .replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>')
   .replace(/(^|[\s(])(https?:\/\/[^\s<)]+)/g, '$1<a href="$2" target="_blank" rel="noopener noreferrer">$2</a>');

/** opts: {resolveImage(url) -> src|null, fileLink(url) -> absolute path, frontMatter: bool} (used by the Files viewer). */
export function render(src, opts = {}) {
  OPTS = opts;
  const blocks = [];
  if (opts.frontMatter) src = src.replace(/^---\n[\s\S]*?\n---\n?/, "");
  // Fenced code is cut out of the raw text first, so it can be syntax-coloured before anything is escaped.
  const raw = src.replace(/```([\w+#.-]*)[^\n]*\n([\s\S]*?)(```|$)/g, (_, lang, code) => {
    blocks.push(`<pre class="code"><code>${highlight(code.replace(/\n$/, ""), lang.toLowerCase())}</code></pre>`);
    return `\u0000${blocks.length - 1}\u0000`;
  });
  let text = esc(raw);
  const lines = text.split("\n"), out = [];
  let list = null, para = [];
  const flushPara = () => { if (para.length) out.push(`<p>${inline(para.join("<br>"))}</p>`); para = []; };
  const flushList = () => { if (list) { out.push(`</${list}>`); list = null; } };
  for (let i = 0; i < lines.length; i++) {
    const l = lines[i];
    let m;
    if (/^\u0000\d+\u0000$/.test(l.trim())) { flushPara(); flushList(); out.push(l.trim()); }
    else if ((m = l.match(/^(#{1,6})\s+(.*)/))) { flushPara(); flushList(); out.push(`<h${m[1].length}>${inline(m[2])}</h${m[1].length}>`); }
    else if (/^\s*(---|\*\*\*)\s*$/.test(l)) { flushPara(); flushList(); out.push("<hr>"); }
    else if ((m = l.match(/^\s*[-*•]\s+(.*)/))) {
      flushPara(); if (list !== "ul") { flushList(); out.push("<ul>"); list = "ul"; }
      const t = m[1].match(/^\[( |x|X)\]\s+(.*)/);
      out.push(t ? `<li class="task"><input type="checkbox" disabled ${t[1] === " " ? "" : "checked"}> ${inline(t[2])}</li>` : `<li>${inline(m[1])}</li>`);
    }
    else if ((m = l.match(/^\s*\d+[.)]\s+(.*)/))) { flushPara(); if (list !== "ol") { flushList(); out.push("<ol>"); list = "ol"; } out.push(`<li>${inline(m[1])}</li>`); }
    else if ((m = l.match(/^&gt;\s?(.*)/))) { flushPara(); flushList(); out.push(`<blockquote>${inline(m[1])}</blockquote>`); }
    else if (/^\s*\|.*\|\s*$/.test(l) && /^\s*\|[\s:|-]+\|\s*$/.test(lines[i + 1] || "")) {
      flushPara(); flushList();
      const cells = (r) => r.trim().replace(/^\||\|$/g, "").split("|").map((c) => inline(c.trim()));
      const head = cells(l); i += 2; const rows = [];
      while (i < lines.length && /^\s*\|.*\|\s*$/.test(lines[i])) rows.push(cells(lines[i++])); i--;
      out.push(`<table><thead><tr>${head.map((c) => `<th>${c}</th>`).join("")}</tr></thead><tbody>${rows.map((r) => `<tr>${r.map((c) => `<td>${c}</td>`).join("")}</tr>`).join("")}</tbody></table>`);
    }
    else if (!l.trim()) {
      flushPara();
      const next = lines.slice(i + 1).find((x) => x.trim());   // a blank line inside a list doesn't end it
      if (!(list && next && (list === "ul" ? /^\s*[-*•]\s+/ : /^\s*\d+[.)]\s+/).test(next))) flushList();
    }
    else if (list && /^\s{2,}\S/.test(l) && out[out.length - 1]?.startsWith("<li>")) {   // wrapped/indented line of the last item
      out[out.length - 1] = out[out.length - 1].replace(/<\/li>$/, `<br>${inline(l.trim())}</li>`);
    }
    else { flushList(); para.push(l); }
  }
  flushPara(); flushList();
  return out.join("").replace(/\u0000(\d+)\u0000/g, (_, n) => blocks[+n]);
}
