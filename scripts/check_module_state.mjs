// Finds state kept at the top level of a panel module, which two copies of the same panel would share.
//
//   node scripts/check_module_state.mjs                      every panel in diatonic/web/apps
//   node scripts/check_module_state.mjs diatonic/web/apps/todos.js
//
// A heuristic: it flags top-level `let`/`var`, `new Map/Set/...`, and objects or arrays with a one or two letter name (S, M, T).
// Constant look-up tables (KIND_ICON = {...}), functions and string constants are fine. If a line really is shared on purpose,
// end it with `// state-ok: <why>`. Exit code 1 if anything is flagged.
import fs from "node:fs";
import path from "node:path";

const dir = new URL("../diatonic/web/apps/", import.meta.url).pathname;
const files = process.argv.length > 2 ? process.argv.slice(2) : fs.readdirSync(dir).filter((f) => f.endsWith(".js")).map((f) => path.join(dir, f));
const RULES = [
  [/^(let|var)\s+(\w+)/, "top-level let/var"],
  [/^const\s+(\w+)\s*=\s*new\s+(Map|Set|WeakMap|WeakSet|Array)\b/, "top-level new Map/Set/Array"],
  [/^const\s+([A-Za-z])\s*=\s*[\[{]/, "one-letter mutable object"],
  [/^const\s+([A-Z][A-Z0-9]?)\s*=\s*[\[{]/, "short-named mutable object"],
  [/^const\s+(\w+)\s*=\s*\[\]\s*;?\s*$/, "top-level empty array"],
];
let flagged = 0;
for (const file of files) {
  const lines = fs.readFileSync(file, "utf8").split("\n");
  lines.forEach((line, i) => {
    if (/\/\/\s*state-ok/.test(line)) return;
    for (const [re, why] of RULES) {
      if (re.test(line)) { console.log(`${path.relative(process.cwd(), file)}:${i + 1}: ${why}: ${line.trim().slice(0, 100)}`); flagged++; break; }
    }
  });
}
console.log(flagged ? `\n${flagged} line(s) of module-level state` : "no module-level state found");
process.exit(flagged ? 1 : 0);
