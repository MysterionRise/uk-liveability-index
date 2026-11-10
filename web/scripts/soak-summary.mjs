// Reads Playwright's JSON report from a repeated run (e2e-soak.yml) and prints, in
// Markdown, how many times each case failed: `node scripts/soak-summary.mjs soak.json 5`.
import fs from "node:fs";

const [file, repeat = "?"] = process.argv.slice(2);
const report = JSON.parse(fs.readFileSync(file, "utf8"));

const cases = new Map(); // "file:line title" -> { runs, failed, errors }
const walk = (suite) => {
  for (const spec of suite.specs ?? []) {
    const key = `${spec.file}:${spec.line} ${spec.title}`;
    const entry = cases.get(key) ?? { runs: 0, failed: 0, errors: new Set() };
    for (const test of spec.tests ?? []) {
      // The last result is the one that counts (earlier ones are retries; there are none)
      const result = test.results?.at(-1);
      if (!result || result.status === "skipped") continue;
      entry.runs += 1;
      if (result.status !== "passed") {
        entry.failed += 1;
        const message = (result.error?.message ?? result.status).replace(/\u001b\[[0-9;]*m/g, "");
        entry.errors.add(message.split("\n").slice(0, 4).join(" ").slice(0, 240));
      }
    }
    cases.set(key, entry);
  }
  for (const child of suite.suites ?? []) walk(child);
};
for (const suite of report.suites ?? []) walk(suite);

const all = [...cases.entries()].filter(([, c]) => c.runs > 0);
const failing = all.filter(([, c]) => c.failed > 0).sort((a, b) => b[1].failed - a[1].failed);
const runs = all.reduce((n, [, c]) => n + c.runs, 0);

console.log(`Browser suite soak: ${all.length} cases, ${repeat} runs each (${runs} runs in all).\n`);
if (!failing.length) {
  console.log("Every run passed.");
} else {
  console.log("| Case | Failed | Runs | First error |\n|---|---|---|---|");
  for (const [key, c] of failing) {
    const error = [...c.errors][0].replace(/\|/g, "\\|");
    console.log(`| \`${key}\` | ${c.failed} | ${c.runs} | ${error} |`);
  }
}
