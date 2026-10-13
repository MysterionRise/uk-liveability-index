// Every catalogue has exactly English's keys, every plural message covers its
// language's plural categories, and every leaf key is used somewhere in the app.
import { readFileSync, readdirSync } from "node:fs";
import path from "node:path";

const root = path.resolve(import.meta.dirname, "..");
const messagesDir = path.join(root, "messages");
const BCP47 = { en: "en-GB", cy: "cy-GB", gd: "gd-GB", ga: "ga-IE" };

function leaves(obj, prefix = "") {
  const out = {};
  for (const [k, v] of Object.entries(obj)) {
    if (k === "_meta") continue;
    const key = prefix ? `${prefix}.${k}` : k;
    if (v && typeof v === "object") Object.assign(out, leaves(v, key));
    else out[key] = v;
  }
  return out;
}

const files = readdirSync(messagesDir).filter((f) => f.endsWith(".json"));
const catalogues = Object.fromEntries(files.map((f) => [f.replace(".json", ""), JSON.parse(readFileSync(path.join(messagesDir, f), "utf8"))]));
const en = leaves(catalogues.en);
const problems = [];

for (const [locale, cat] of Object.entries(catalogues)) {
  if (!cat._meta || !["draft", "reviewed"].includes(cat._meta.status)) problems.push(`${locale}: _meta.status missing`);
  const keys = leaves(cat);
  for (const k of Object.keys(en)) if (!(k in keys)) problems.push(`${locale}: missing ${k}`);
  for (const k of Object.keys(keys)) if (!(k in en)) problems.push(`${locale}: unknown key ${k}`);
  const categories = new Intl.PluralRules(BCP47[locale] ?? locale).resolvedOptions().pluralCategories;
  for (const [k, msg] of Object.entries(keys)) {
    if (typeof msg !== "string" || !/\{\s*\w+\s*,\s*plural\s*,/.test(msg)) continue;
    // Every category the language uses must be spelt out: "other" alone would show
    // the wrong form for one, two or few in Welsh and Gaelic
    for (const c of categories) {
      if (!new RegExp(`\\b${c}\\s*\\{`).test(msg)) problems.push(`${locale}: ${k} lacks plural form "${c}"`);
    }
  }
}

// Usage: a leaf's last segment must appear quoted in the source
const source = [];
function walk(dir) {
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      if (!["node_modules", ".next"].includes(entry.name)) walk(p);
    } else if (/\.(tsx?|mjs)$/.test(entry.name) && !/\.(test|spec)\./.test(entry.name)) source.push(readFileSync(p, "utf8"));
  }
}
for (const d of ["app", "components", "lib"]) walk(path.join(root, d));
const text = source.join("\n");
// t(`kinds.${kind}`) reaches every key under that prefix
const dynamicPrefixes = [...text.matchAll(/t\(\s*`([\w.]*)\$\{/g)].map((m) => m[1]);
for (const k of Object.keys(en)) {
  const last = k.split(".").pop();
  const relative = k.split(".").slice(1).join(".");
  const quoted = text.includes(`"${last}"`) || text.includes(`'${last}'`) || text.includes(`\`${last}\``);
  const dynamic = dynamicPrefixes.some((prefix) => relative.startsWith(prefix));
  if (!quoted && !dynamic) problems.push(`en: ${k} is not used`);
}

if (problems.length) {
  console.error(problems.join("\n"));
  process.exit(1);
}
console.log(`i18n: ${Object.keys(en).length} keys, ${files.length} catalogues OK`);
