/* Verify the standalone Field Work PWA leaves no Chinese UI text in en/nl/de/es. */
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const root = path.resolve(__dirname, "..");
const source = fs.readFileSync(path.join(root, "static", "field-i18n.js"), "utf8");
const supplement = require(path.join(root, "static", "ui-i18n-supplement.js"));
const template = fs.readFileSync(path.join(root, "templates", "field_work.html"), "utf8")
  .replace(/\{#[\s\S]*?#\}/g, " ")
  .replace(/<!--[\s\S]*?-->/g, " ")
  .replace(/<script\b[\s\S]*?<\/script>/gi, " ")
  .replace(/<style\b[\s\S]*?<\/style>/gi, " ");
const dynamicSource = fs.readFileSync(path.join(root, "static", "field-work.js"), "utf8");
const han = /[\u3400-\u9fff]/;

function clean(value) {
  return value.replace(/\{%[\s\S]*?%\}/g, " ").replace(/\{\{[\s\S]*?\}\}/g, " ").replace(/\s+/g, " ").trim();
}
const literals = [];
for (const match of template.matchAll(/>([^<>]+)</g)) literals.push(clean(match[1]));
for (const match of template.matchAll(/\b(?:placeholder|title|aria-label|alt)\s*=\s*["']([^"']+)["']/g)) literals.push(clean(match[1]));
for (const match of dynamicSource.matchAll(/(["'`])((?:\\.|(?!\1)[\s\S])*?)\1/g)) {
  const value = clean(match[2]);
  if (han.test(value)) {
    literals.push(value);
    for (const run of value.match(/[\u3400-\u9fff][\u3400-\u9fff，。？！：；、“”‘’（）《》·…/—\s]*/g) || []) {
      const fragment = clean(run);
      if (fragment) literals.push(fragment);
    }
  }
}

const failures = [];
for (const language of ["en", "nl", "de", "es"]) {
  const document = {
    documentElement: { lang: language },
    readyState: "loading",
    addEventListener() {},
  };
  const context = { document, window: {}, globalThis: { uiI18nSupplement: supplement } };
  context.globalThis.window = context.window;
  vm.runInNewContext(source, context, { filename: "field-i18n.js" });
  for (const literal of literals.filter(value => value && han.test(value))) {
    const translated = context.window.fieldTranslate(literal);
    if (han.test(translated)) failures.push({ language, literal, translated });
  }
}

const unique = [...new Map(failures.map(item => [`${item.language}\0${item.literal}`, item])).values()];
if (process.env.AUDIT_JSON) {
  fs.writeFileSync(path.resolve(process.env.AUDIT_JSON), JSON.stringify(
    unique.map(item => ({ file: "field-work", value: item.literal, missing: [item.language] })), null, 2
  ) + "\n", "utf8");
}
if (unique.length) {
  unique.slice(0, 100).forEach(item => console.log(`${item.language}: ${item.literal} -> ${item.translated}`));
  console.error(`${unique.length} untranslated Field Work literal(s).`);
  process.exitCode = 1;
} else {
  console.log(`Checked ${literals.filter(value => value && han.test(value)).length} Field Work literals: en/nl/de/es contain no residual Chinese characters.`);
}
