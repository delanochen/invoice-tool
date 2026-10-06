/* Audit user-visible Chinese literals against every non-Chinese UI dictionary.
 * Run with: node scripts/audit_ui_i18n.js
 */
const fs = require("fs");
const path = require("path");

const root = path.resolve(__dirname, "..");
const { en, nl, de, es } = require(path.join(root, "static", "ui-i18n.js"));
const allDictionaries = { en, nl, de, es };
// English is the operational fallback and the default audit target. Set
// UI_LANGS=en,nl,de,es when reviewing every maintained locale together.
const requestedLanguages = (process.env.UI_LANGS || "en").split(",").map(value => value.trim()).filter(Boolean);
const dictionaries = Object.fromEntries(
  requestedLanguages.map(language => {
    if (!allDictionaries[language]) throw new Error(`Unsupported UI language: ${language}`);
    return [language, allDictionaries[language]];
  })
);
const han = /[\u3400-\u9fff]/;
const ignoredFiles = new Set(["ui-i18n.js", "ui-i18n-supplement.js", "field-i18n.js"]);

function filesUnder(directory, extension) {
  const output = [];
  for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
    const full = path.join(directory, entry.name);
    if (entry.isDirectory()) {
      if (!new Set(["vendor", "_backup"]).has(entry.name)) output.push(...filesUnder(full, extension));
    } else if (entry.name.endsWith(extension) && !ignoredFiles.has(entry.name)) {
      output.push(full);
    }
  }
  return output;
}

function clean(value) {
  return value
    .replace(/\{#[\s\S]*?#\}/g, " ")
    .replace(/\{%[\s\S]*?%\}/g, " ")
    .replace(/\{\{[\s\S]*?\}\}/g, " ")
    .replace(/&(?:nbsp|times|middot|rarr|larr|hellip);/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

function translated(value, dictionary) {
  const trimmed = value.trim();
  if (dictionary[trimmed]) return dictionary[trimmed];
  let result = value;
  const entries = Object.entries(dictionary).sort(([a], [b]) => b.length - a.length);
  for (const [source, target] of entries) {
    if (source.length >= 2 && result.includes(source)) result = result.split(source).join(target);
  }
  return result;
}

function htmlLiterals(source) {
  const withoutComments = source
    .replace(/\{#[\s\S]*?#\}/g, " ")
    .replace(/<!--[\s\S]*?-->/g, " ")
    .replace(/<script\b[\s\S]*?<\/script>/gi, " ")
    .replace(/<style\b[\s\S]*?<\/style>/gi, " ");
  const values = [];
  for (const match of withoutComments.matchAll(/>([^<>]+)</g)) values.push(match[1]);
  for (const match of withoutComments.matchAll(/\b(?:placeholder|title|aria-label)\s*=\s*["']([^"']+)["']/g)) values.push(match[1]);
  // Jinja macro arguments (for example tab_button(..., '工单信息')) become
  // visible text even though they do not occur between HTML tags.
  for (const block of source.matchAll(/\{[{%]([\s\S]*?)[}%]\}/g)) {
    for (const match of block[1].matchAll(/(["'])((?:\\.|(?!\1)[\s\S])*?)\1/g)) values.push(match[2]);
  }
  return values.map(clean).filter(value => value && han.test(value));
}

function jsLiterals(source) {
  const values = [];
  const visibleContext = /(?:textContent|innerHTML|insertAdjacentHTML|alert|confirm|prompt|setAttribute|\.title|\.placeholder|showNotice|showError|flash|message)/;
  for (const match of source.matchAll(/([\w.]+\s*=\s*|[\w.]+\s*\(|,\s*)?(["'`])((?:\\.|(?!\2)[\s\S])*?)\2/g)) {
    const prefix = match[1] || "";
    const value = clean(match[3]);
    if (value && han.test(value) && visibleContext.test(prefix)) {
      values.push(value);
      for (const run of value.match(/[\u3400-\u9fff][\u3400-\u9fff，。？！：；、“”‘’（）《》·…/—\s]*/g) || []) {
        const fragment = clean(run);
        if (fragment) values.push(fragment);
      }
    }
  }
  return values;
}

const findings = [];
for (const file of filesUnder(path.join(root, "templates"), ".html")) {
  const relative = path.relative(root, file).replaceAll("\\", "/");
  if (relative === "templates/field_work.html") continue; // audited by its dedicated field dictionary + shared supplement
  for (const value of htmlLiterals(fs.readFileSync(file, "utf8"))) findings.push({ file: relative, value });
}
for (const file of filesUnder(path.join(root, "static"), ".js")) {
  const relative = path.relative(root, file).replaceAll("\\", "/");
  if (["static/field-work.js", "static/field-i18n.js"].includes(relative)) continue;
  for (const value of jsLiterals(fs.readFileSync(file, "utf8"))) findings.push({ file: relative, value });
}

const failures = [];
for (const finding of findings) {
  const missing = Object.entries(dictionaries)
    .filter(([, dictionary]) => han.test(translated(finding.value, dictionary)))
    .map(([language]) => language);
  if (missing.length) failures.push({ ...finding, missing });
}

const unique = [...new Map(failures.map(item => [`${item.file}\0${item.value}\0${item.missing}`, item])).values()];
if (process.env.AUDIT_JSON) {
  fs.writeFileSync(path.resolve(process.env.AUDIT_JSON), JSON.stringify(unique, null, 2) + "\n", "utf8");
}
if (unique.length) {
  const limit = Math.max(0, Number(process.env.AUDIT_LIMIT || unique.length));
  for (const item of unique.slice(0, limit)) console.log(`${item.file}: [${item.missing.join(",")}] ${item.value}`);
  if (limit < unique.length) console.log(`... ${unique.length - limit} more`);
  const byArea = new Map();
  for (const item of unique) byArea.set(item.file, (byArea.get(item.file) || 0) + 1);
  console.error("Most affected files:");
  for (const [file, count] of [...byArea].sort((a, b) => b[1] - a[1]).slice(0, 20)) console.error(`  ${count}\t${file}`);
  console.error(`\n${unique.length} untranslated UI literal(s).`);
  process.exitCode = 1;
} else {
  console.log(`Checked ${findings.length} Chinese UI literals: ${requestedLanguages.join("/")} contain no residual Chinese characters.`);
}
