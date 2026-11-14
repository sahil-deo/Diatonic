// A small, dependency-free syntax highlighter. tokenize() -> [{c, t}], lines() splits tokens per line (so multi-line
// comments/strings keep their colour on every line), highlight() -> HTML. Pure functions: no DOM needed.
export const esc = (s) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
const set = (s) => new Set(s.split(" "));
const MAX = 400_000;   // beyond this we skip colouring rather than freeze the page

const NUM = String.raw`0[xX][\da-fA-F_]+|0[bB][01_]+|\d[\d_]*(?:\.\d+)?(?:[eE][+-]?\d+)?[fFlLuU]*`;
const DQ = String.raw`"(?:[^"\\\n]|\\.)*"?`, SQ = String.raw`'(?:[^'\\\n]|\\.)*'?`, BT = String.raw`` + "`(?:[^`\\\\]|\\\\.)*`?";

/** Build a tokenizer for C-like languages from a few parts. */
function clike({ line = [], block = [], strs = [DQ, SQ], kw, lit = "", meta = "", ci = false, decorator = false, vars = false }) {
  const parts = [];
  if (block.length) parts.push(`(?<c>${block.map(([a, b]) => `${a}[\\s\\S]*?(?:${b}|$)`).join("|")}${line.length ? "|" : ""}${line.map((l) => `${l}[^\\n]*`).join("|")})`);
  else if (line.length) parts.push(`(?<c>${line.map((l) => `${l}[^\\n]*`).join("|")})`);
  if (meta) parts.push(`(?<m>${meta})`);
  parts.push(`(?<s>${strs.join("|")})`);
  if (decorator) parts.push(String.raw`(?<m>@[A-Za-z_][\w.]*)`);
  if (vars) parts.push(String.raw`(?<v>\$\{?[A-Za-z_]\w*\}?|\$[0-9@#?*$!-])`);
  parts.push(`(?<n>\\b(?:${NUM})\\b)`, String.raw`(?<w>[A-Za-z_$][\w$]*)`);
  const re = new RegExp(parts.join("|"), "g" + (ci ? "i" : ""));
  const K = set(kw), L = set(lit);
  return (code) => {
    const out = []; let last = 0, m;
    re.lastIndex = 0;
    while ((m = re.exec(code))) {
      if (m.index > last) out.push({ c: null, t: code.slice(last, m.index) });
      const g = m.groups; let c = null;
      if (g.c !== undefined) c = "c"; else if (g.m !== undefined) c = "m"; else if (g.s !== undefined) c = "s";
      else if (g.v !== undefined) c = "v"; else if (g.n !== undefined) c = "n";
      else if (g.w !== undefined) {
        const w = ci ? g.w.toLowerCase() : g.w;
        c = L.has(w) ? "n" : K.has(w) ? "k" : code[re.lastIndex] === "(" ? "f" : /^[A-Z][A-Za-z0-9_]*[a-z]/.test(g.w) ? "t" : null;
      }
      out.push({ c, t: m[0] }); last = re.lastIndex;
      if (m[0] === "") re.lastIndex++;
    }
    if (last < code.length) out.push({ c: null, t: code.slice(last) });
    return out;
  };
}

const JS_KW = "var let const function return if else for while do switch case break continue new delete typeof instanceof in of class extends super import export from as default try catch finally throw async await yield static get set void this with debugger";
const TS_KW = JS_KW + " interface type enum implements public private protected readonly abstract declare namespace keyof infer is";
const C_KW = "auto break case char const continue default do double else enum extern float for goto if inline int long register restrict return short signed sizeof static struct switch typedef union unsigned void volatile while";
const CPP_KW = C_KW + " class namespace template typename public private protected virtual override final new delete this using try catch throw operator constexpr nullptr bool explicit friend mutable noexcept static_cast dynamic_cast const_cast reinterpret_cast";

const SPECS = {
  python: clike({ line: ["#"], block: [[`"""`, `"""`], [`'''`, `'''`]], kw: "def class return if elif else for while in not and or is import from as with try except finally raise pass break continue lambda yield async await global nonlocal del assert match case", lit: "None True False self cls", decorator: true }),
  javascript: clike({ line: ["//"], block: [["/\\*", "\\*/"]], strs: [DQ, SQ, BT], kw: JS_KW, lit: "true false null undefined NaN Infinity" }),
  typescript: clike({ line: ["//"], block: [["/\\*", "\\*/"]], strs: [DQ, SQ, BT], kw: TS_KW, lit: "true false null undefined NaN Infinity", decorator: true }),
  go: clike({ line: ["//"], block: [["/\\*", "\\*/"]], strs: [DQ, BT, SQ], kw: "break case chan const continue default defer else fallthrough for func go goto if import interface map package range return select struct switch type var", lit: "true false nil iota" }),
  rust: clike({ line: ["//"], block: [["/\\*", "\\*/"]], kw: "as async await break const continue crate dyn else enum extern fn for if impl in let loop match mod move mut pub ref return self Self static struct super trait type unsafe use where while", lit: "true false None Some Ok Err", meta: String.raw`#!?\[[^\]\n]*\]` }),
  java: clike({ line: ["//"], block: [["/\\*", "\\*/"]], kw: "abstract assert boolean break byte case catch char class const continue default do double else enum extends final finally float for goto if implements import instanceof int interface long native new package private protected public return short static strictfp super switch synchronized this throw throws transient try void volatile while var record", lit: "true false null", decorator: true }),
  kotlin: clike({ line: ["//"], block: [["/\\*", "\\*/"]], kw: "as break class continue do else for fun if in interface is object package return super this throw try typealias val var when while import override open private public protected internal data sealed companion init constructor suspend", lit: "true false null", decorator: true }),
  swift: clike({ line: ["//"], block: [["/\\*", "\\*/"]], kw: "associatedtype class deinit enum extension fileprivate func import init inout internal let open operator private protocol public static struct subscript typealias var break case continue default defer do else fallthrough for guard if in repeat return switch where while as catch is throw throws try async await", lit: "true false nil self Self", decorator: true }),
  c: clike({ line: ["//"], block: [["/\\*", "\\*/"]], kw: C_KW, lit: "NULL true false", meta: String.raw`^[ \t]*#[ \t]*\w+` }),
  cpp: clike({ line: ["//"], block: [["/\\*", "\\*/"]], kw: CPP_KW, lit: "NULL nullptr true false", meta: String.raw`^[ \t]*#[ \t]*\w+` }),
  csharp: clike({ line: ["//"], block: [["/\\*", "\\*/"]], kw: "abstract as base bool break byte case catch char checked class const continue decimal default delegate do double else enum event explicit extern finally fixed float for foreach goto if implicit in int interface internal is lock long namespace new object operator out override params private protected public readonly ref return sbyte sealed short sizeof static string struct switch this throw try typeof uint ulong unchecked unsafe ushort using var virtual void volatile while async await record", lit: "true false null" }),
  ruby: clike({ line: ["#"], block: [["^=begin", "^=end"]], kw: "alias and begin break case class def defined do else elsif end ensure for if in module next not or redo rescue retry return self super then undef unless until when while yield require require_relative attr_accessor attr_reader attr_writer puts", lit: "true false nil" }),
  php: clike({ line: ["//", "#"], block: [["/\\*", "\\*/"]], kw: "abstract and array as break case catch class clone const continue declare default do echo else elseif empty enddeclare endfor endforeach endif endswitch endwhile extends final finally fn for foreach function global if implements include include_once instanceof interface isset list namespace new or print private protected public require require_once return static switch throw trait try unset use var while yield", lit: "true false null TRUE FALSE NULL", vars: true }),
  shell: clike({ line: ["#"], strs: [DQ, SQ], vars: true, kw: "if then else elif fi for while until do done case esac in function select time return exit break continue export local readonly declare unset source alias cd echo printf read set shift test trap eval exec", lit: "true false" }),
  sql: clike({ line: ["--"], block: [["/\\*", "\\*/"]], ci: true, kw: "select from where and or not in is null like between join inner left right outer full cross on as group by order having limit offset insert into values update set delete create table alter drop index view primary key foreign references default unique distinct union all case when then else end exists asc desc with returning begin commit rollback", lit: "true false null" }),
  lua: clike({ line: ["--"], block: [["--\\[\\[", "\\]\\]"]], kw: "and break do else elseif end for function goto if in local not or repeat return then until while", lit: "true false nil" }),
  r: clike({ line: ["#"], kw: "if else repeat while function for next break in library return", lit: "TRUE FALSE NULL NA Inf NaN" }),
};

/* ---- languages with their own shape ---- */
function simple(re, classes) {
  return (code) => {
    const out = []; let last = 0, m; re.lastIndex = 0;
    while ((m = re.exec(code))) {
      if (m.index > last) out.push({ c: null, t: code.slice(last, m.index) });
      const c = classes.find((k) => m.groups[k] !== undefined) || null;
      out.push({ c: c === "key" ? "p" : c === "tag" ? "t" : c === "attr" ? "p" : c === "sec" ? "k" : c === "at" ? "m" : c === "lit" ? "n" : c, t: m[0] }); last = re.lastIndex;
      if (m[0] === "") re.lastIndex++;
    }
    if (last < code.length) out.push({ c: null, t: code.slice(last) });
    return out;
  };
}
SPECS.json = simple(new RegExp(String.raw`(?<key>"(?:[^"\\\n]|\\.)*"(?=\s*:))|(?<s>"(?:[^"\\\n]|\\.)*"?)|(?<lit>\b(?:true|false|null)\b)|(?<n>-?\b\d[\d.eE+-]*\b)|(?<c>//[^\n]*|/\*[\s\S]*?\*/)`, "g"), ["key", "s", "lit", "n", "c"]);
SPECS.yaml = simple(new RegExp(String.raw`(?<c>#[^\n]*)|(?<key>^[ \t-]*[\w."'/-]+(?=:(?:\s|$)))|(?<s>"(?:[^"\\\n]|\\.)*"?|'[^'\n]*'?)|(?<lit>\b(?:true|false|null|yes|no|on|off)\b)|(?<n>-?\b\d[\d.]*\b)|(?<m>[&*][\w-]+)`, "gm"), ["c", "key", "s", "lit", "n", "m"]);
SPECS.toml = simple(new RegExp(String.raw`(?<c>[#;][^\n]*)|(?<sec>^\s*\[[^\]\n]+\])|(?<key>^[ \t]*[\w."-]+(?=\s*=))|(?<s>"""[\s\S]*?"""|"(?:[^"\\\n]|\\.)*"?|'[^'\n]*'?)|(?<lit>\b(?:true|false)\b)|(?<n>-?\b\d[\d._:-]*\b)`, "gm"), ["c", "sec", "key", "s", "lit", "n"]);
SPECS.css = simple(new RegExp(String.raw`(?<c>/\*[\s\S]*?(?:\*/|$))|(?<s>"(?:[^"\\\n]|\\.)*"?|'(?:[^'\\\n]|\\.)*'?)|(?<at>@[\w-]+)|(?<key>[\w-]+(?=\s*:))|(?<n>#[\da-fA-F]{3,8}\b|-?\b\d[\d.]*(?:px|em|rem|%|vh|vw|s|ms|deg)?\b)|(?<f>[\w-]+(?=\())|(?<k>!important)`, "g"), ["c", "s", "at", "key", "n", "f", "k"]);
SPECS.html = SPECS.xml = simple(new RegExp(String.raw`(?<c><!--[\s\S]*?(?:-->|$))|(?<tag></?[A-Za-z][\w:-]*|<![A-Za-z]+|/?>)|(?<s>"[^"]*"?|'[^']*'?)|(?<attr>[\w:@.-]+(?==))`, "g"), ["c", "tag", "s", "attr"]);

SPECS.diff = (code) => {
  const out = [];
  code.split("\n").forEach((ln, i, a) => {
    const c = ln.startsWith("+++") || ln.startsWith("---") ? "dh" : ln.startsWith("@@") ? "dm" : ln.startsWith("+") ? "da" : ln.startsWith("-") ? "dd" : null;
    out.push({ c, t: ln + (i < a.length - 1 ? "\n" : "") });
  });
  return out;
};

export const LANG_ALIASES = { js: "javascript", jsx: "javascript", mjs: "javascript", ts: "typescript", tsx: "typescript", py: "python", sh: "shell", bash: "shell", zsh: "shell", console: "shell",
  yml: "yaml", rb: "ruby", rs: "rust", kt: "kotlin", "c++": "cpp", cc: "cpp", cs: "csharp", golang: "go", jsonc: "json", htm: "html", svg: "xml", patch: "diff", scss: "css", less: "css", ini: "toml" };

export function tokenize(code, lang) {
  const spec = SPECS[LANG_ALIASES[lang] || lang];
  if (!spec || code.length > MAX) return [{ c: null, t: code }];
  try { return spec(code); } catch { return [{ c: null, t: code }]; }
}

/** One HTML string per line, with spans closed and reopened at each line break. */
export function lines(code, lang) {
  const out = [""];
  for (const { c, t } of tokenize(code, lang)) {
    t.split("\n").forEach((part, i) => {
      if (i > 0) out.push("");
      if (part) out[out.length - 1] += c ? `<span class="tk-${c}">${esc(part)}</span>` : esc(part);
    });
  }
  return out;
}

export const highlight = (code, lang) => lines(code, lang).join("\n");
export const hasLanguage = (lang) => !!SPECS[LANG_ALIASES[lang] || lang];
