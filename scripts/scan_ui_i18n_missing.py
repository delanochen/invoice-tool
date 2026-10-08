#!/usr/bin/env python3
"""Scan for Chinese text that remains untranslated under lang=en.

Reproducible scanner modeling the browser-side translation mechanism:

  * Base-derived pages (extend templates/base.html):
      - DOM text nodes: exact match first; if the node's element or an
        ancestor is inside partialSelector, also longest-first substring
        replacement; otherwise the node must match exactly.
      - Attributes placeholder/title/aria-label always allow substring.
      - document.title ({% block title %}) always allows substring.
  * templates/field_work.html (standalone, uses static/field-i18n.js):
      - Static text nodes: exact match only (no substring).
      - Attributes placeholder/title/aria-label/alt: substring replacement.

Effective English key set (union):
  static/ui-i18n.js en literal + its Object.assign(en,{...}) tail block
  + registrationTranslations + reviewedTerms keys
  + static/ui-i18n-supplement.js "en" block.
Field pages additionally use field-i18n.js own en literal.

Jinja: `{# ... #}` comments are dropped before parsing; `{% ... %}` block
tags split fragments; `{{ ... }}` expressions become a neutral digit; Chinese
string literals *inside* `{{ ... }}` render verbatim and are checked as exact.

Outputs stdout + scripts/scan_ui_i18n_missing_report.json.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = ROOT / "templates"
STATIC = ROOT / "static"

CJK_RE = re.compile(r"[\u4e00-\u9fff]")
KEY_RE = re.compile(r"""["']((?:[^"'\\]|\\.)*[\u4e00-\u9fff](?:[^"'\\]|\\.)*)["']\s*:""")
LIT_RE = re.compile(r"""["']((?:[^"'\\]|\\.)*[\u4e00-\u9fff](?:[^"'\\]|\\.)*)["']""")
JINJA_COMMENT_RE = re.compile(r"\{#.*?#\}", re.S)
JINJA_BLOCK_RE = re.compile(r"\{%.*?%\}", re.S)
JINJA_EXPR_RE = re.compile(r"\{\{.*?\}\}", re.S)

PARTIAL_TAGS = {"label", "button", "option", "summary", "th", "h1", "h2", "h3", "legend", "small"}
PARTIAL_CLASSES = {
    "eyebrow", "muted-line", "empty", "field-error", "status", "flash",
    "map-summary", "map-attribution-note", "translatable-text",
    "erp-badge", "erp-summary-item", "erp-status",
}

# Business backend modules whose Chinese strings reach the browser.
BACKEND_PY = [
    "app.py",
    "customer_report_logic.py", "employee_finance.py", "expense_smart_fill.py",
    "field_work.py", "profitability.py", "rate_engine.py", "service_report_assist.py",
    "settlement_request_limits.py", "settlement_review.py", "staff_reports.py",
    "trip_policy.py", "airbnb_receipt_tool.py", "ai_interpretation.py", "ai_review.py",
]


def extract_keys(region: str) -> set[str]:
    return {m.group(1) for m in KEY_RE.finditer(region)}


def region_between(text: str, start_marker: str, end_marker: str) -> str:
    i = text.find(start_marker)
    if i < 0:
        return ""
    j = text.find(end_marker, i + len(start_marker))
    return text[i:] if j < 0 else text[i:j]


def load_key_sets():
    ui = (STATIC / "ui-i18n.js").read_text(encoding="utf-8")
    sup = (STATIC / "ui-i18n-supplement.js").read_text(encoding="utf-8")
    field = (STATIC / "field-i18n.js").read_text(encoding="utf-8")

    ui_en = region_between(ui, "const en = {", "const nl = {")
    tail_start = ui.find("Object.assign(en, {")
    tail_region = ui[tail_start:] if tail_start >= 0 else ""
    reg = region_between(ui, "const registrationTranslations = {", "const supplement")
    rev = region_between(ui, "const reviewedTerms = {", "Object.assign(en, {")
    sup_en = region_between(sup, '"en": {', '"nl": {')
    field_en = region_between(field, "const en = {", "const es = {")

    base_keys = (
        extract_keys(ui_en) | extract_keys(tail_region)
        | extract_keys(reg) | extract_keys(rev) | extract_keys(sup_en)
    )
    field_keys = extract_keys(sup_en) | extract_keys(field_en)
    return base_keys, field_keys


def has_cjk(s: str) -> bool:
    return bool(CJK_RE.search(s))


def residual_cjk(fragment: str, keys: set[str]) -> str:
    result = fragment
    for key in sorted(keys, key=len, reverse=True):
        if key and key in result:
            result = result.replace(key, " ")
    runs = re.findall(r"[\u4e00-\u9fff，。、；：（）「」“”·\-–—%/]{1,}", result)
    return " | ".join(r for r in runs if CJK_RE.search(r))


def normalize_jinja_text(raw: str) -> list[str]:
    s = JINJA_COMMENT_RE.sub("", raw)
    out = []
    for p in JINJA_BLOCK_RE.split(s):
        p = JINJA_EXPR_RE.sub("0", p).strip()
        if p and has_cjk(p):
            out.append(p)
    return out


def ancestor_matches_partial(el) -> bool:
    node = el
    while node is not None and isinstance(node.tag, str):
        if node.tag.lower() in PARTIAL_TAGS:
            return True
        cls = node.get("class") if hasattr(node, "get") else None
        if cls and any(c in PARTIAL_CLASSES for c in cls.split()):
            return True
        node = node.getparent()
    return False


def consider_text(findings: list, raw: str, el, kind: str, keys: set[str], field_mode: bool):
    for frag in normalize_jinja_text(raw):
        if frag.strip() in keys:
            continue
        if field_mode:
            findings.append(dict(file="?", line=el.sourceline or 0, kind=kind,
                                 element=el.tag, text=frag, residual=frag,
                                 note="field-page exact-only text node missing key"))
            continue
        if not ancestor_matches_partial(el):
            findings.append(dict(file="?", line=el.sourceline or 0, kind=kind,
                                 element=el.tag, text=frag, residual=frag,
                                 note="non-partial text node must exactly match a key"))
            continue
        res = residual_cjk(frag, keys)
        if res:
            findings.append(dict(file="?", line=el.sourceline or 0, kind=kind,
                                 element=el.tag, text=frag, residual=res,
                                 note="partial-substring left residual Chinese"))


def scan_template(path: Path, keys: set[str], field_mode: bool) -> list[dict]:
    from lxml import html as lxml_html
    content = path.read_text(encoding="utf-8")

    # --- Handle {% block title %} separately (renders into <title>, substring) ---
    findings = []
    title_m = re.search(r"\{%\s*block\s+title\s*%\}(.*?)\{%\s*endblock\s*%\}", content, re.S)
    if title_m:
        traw = title_m.group(1)
        for frag in normalize_jinja_text(traw):
            if frag.strip() in keys:
                continue
            res = residual_cjk(frag, keys)
            if res:
                line = content.count("\n", 0, title_m.start()) + 1
                findings.append(dict(file=path.name, line=line, kind="doc-title",
                                     element="title", text=frag, residual=res,
                                     note="document.title substring left residual"))

    # Pre-strip Jinja comments so they never leak into parsed text nodes.
    stripped = JINJA_COMMENT_RE.sub(" ", content)
    # Drop block head (CSS/JS) and block title (already handled).
    stripped = re.sub(r"\{%\s*block\s+head\s*%\}.*?\{%\s*endblock\s*%\}", " ", stripped, flags=re.S)
    stripped = re.sub(r"\{%\s*block\s+title\s*%\}.*?\{%\s*endblock\s*%\}", " ", stripped, flags=re.S)
    # Strip <script>/<style> blocks.
    stripped = re.sub(r"<script\b[^>]*>.*?</script>", " ", stripped, flags=re.S | re.I)
    stripped = re.sub(r"<style\b[^>]*>.*?</style>", " ", stripped, flags=re.S | re.I)

    tree = lxml_html.fromstring(stripped)

    if tree.text:
        consider_text(findings, tree.text, tree, "root-text", keys, field_mode)
    for el in tree.iter():
        if not isinstance(el.tag, str):
            continue
        if el.text:
            consider_text(findings, el.text, el, "text", keys, field_mode)
        if el.tail and el.getparent() is not None:
            consider_text(findings, el.tail, el.getparent(), "tail", keys, field_mode)

    # Attributes
    attr_names = ["placeholder", "title", "aria-label"] + (["alt"] if field_mode else [])
    for el in tree.iter():
        if not isinstance(el.tag, str):
            continue
        for attr in attr_names:
            val = el.get(attr)
            if not val or not has_cjk(val):
                continue
            res = residual_cjk(val, keys)
            if res:
                findings.append(dict(file=path.name, line=el.sourceline or 0,
                                     kind=f"attr:{attr}", element=el.tag,
                                     text=val, residual=res,
                                     note="attribute Chinese not fully covered"))

    # Chinese literals inside {{ ... }} render verbatim.
    for m in re.finditer(r"\{\{(.*?)\}\}", content, flags=re.S):
        expr = m.group(1)
        for lit in LIT_RE.finditer(expr):
            s = lit.group(1)
            if not has_cjk(s) or s.strip() in keys:
                continue
            line = content.count("\n", 0, m.start()) + 1
            findings.append(dict(file=path.name, line=line, kind="jinja-expr-literal",
                                 element="{{expr}}", text=s, residual=s,
                                 note="Chinese literal in {{ }} renders verbatim; add exact key"))
    for f in findings:
        f["file"] = path.name
    return findings


def scan_backend_py(keys: set[str]) -> list[dict]:
    findings = []
    seen = set()
    # Known data values / non-UI strings to exclude (customer names, vehicle brands,
    # internal data identifiers, LLM prompt fragments, etc.)
    EXCLUDE_SUBSTR = (
        "三河同飞", "阳光", "比亚迪", "高阳", "Qwen", "DeepSeek", "Google",
        "AI", "LLM", "prompt", "Prompt", "PROMPT",
    )
    for name in BACKEND_PY:
        pf = ROOT / name
        if not pf.exists():
            continue
        txt = pf.read_text(encoding="utf-8")
        for m in LIT_RE.finditer(txt):
            s = m.group(1)
            if not has_cjk(s):
                continue
            # skip docstrings/comments/multiline string literals (not UI constants)
            if "\n" in s or "\r" in s:
                continue
            # skip obvious data: file paths / prefixed db names
            if "/" in s or "\\" in s or s.startswith("invoice_"):
                continue
            # skip known data values
            if any(ex in s for ex in EXCLUDE_SUBSTR):
                continue
            # do substring simulation; only flag if residual CJK remains
            res = residual_cjk(s, keys)
            if not res:
                continue
            line = txt.count("\n", 0, m.start()) + 1
            k = (name, s)
            if k in seen:
                continue
            seen.add(k)
            findings.append(dict(file=name, line=line, kind="py-literal",
                                 element="py", text=s, residual=res,
                                 note="backend Chinese string with residual after substring"))
    # invoice_tool package
    for pf in (ROOT / "invoice_tool").rglob("*.py"):
        txt = pf.read_text(encoding="utf-8")
        for m in LIT_RE.finditer(txt):
            s = m.group(1)
            if not has_cjk(s):
                continue
            if "\n" in s or "\r" in s:
                continue
            if "/" in s or "\\" in s:
                continue
            if any(ex in s for ex in EXCLUDE_SUBSTR):
                continue
            res = residual_cjk(s, keys)
            if not res:
                continue
            line = txt.count("\n", 0, m.start()) + 1
            k = (str(pf.relative_to(ROOT)), s)
            if k in seen:
                continue
            seen.add(k)
            findings.append(dict(file=str(pf.relative_to(ROOT)), line=line, kind="py-literal",
                                 element="py", text=s, residual=res,
                                 note="backend Chinese string with residual after substring"))
    return findings


def scan_js_dynamic(keys: set[str]) -> list[dict]:
    findings = []
    for js in STATIC.glob("*.js"):
        if js.name in {"ui-i18n.js", "ui-i18n-supplement.js", "field-i18n.js"}:
            continue
        txt = js.read_text(encoding="utf-8")
        for fn in ("uiTranslate", "uiConfirm", "fieldTranslate"):
            for m in re.finditer(fn + r"\(\s*([\"'])", txt):
                quote = m.group(1)
                start = m.end()
                end = txt.find(quote, start)
                if end < 0:
                    continue
                s = txt[start:end]
                if not has_cjk(s):
                    continue
                res = residual_cjk(s, keys)
                if res:
                    line = txt.count("\n", 0, m.start()) + 1
                    findings.append(dict(file=js.name, line=line, kind=f"js:{fn}",
                                         element="js", text=s, residual=res,
                                         note="JS dynamic Chinese not fully covered"))
    return findings


def main():
    base_keys, field_keys = load_key_sets()
    all_keys = base_keys | field_keys
    print(f"[keys] base en effective: {len(base_keys)}  field en effective: {len(field_keys)}  union: {len(all_keys)}")

    all_findings = []
    for t in sorted(TEMPLATES.glob("*.html")):
        head = t.read_text(encoding="utf-8")[:400]
        extends_base = '{% extends "base.html" %}' in head or "{% extends 'base.html' %}" in head
        is_field = (t.name == "field_work.html")
        if not extends_base and not is_field:
            full = t.read_text(encoding="utf-8")
            if "ui-i18n.js" not in full and "field-i18n.js" not in full:
                continue
        keys = field_keys if is_field else base_keys
        all_findings.extend(scan_template(t, keys, field_mode=is_field))

    all_findings.extend(scan_backend_py(all_keys))
    all_findings.extend(scan_js_dynamic(all_keys))

    uniq = []
    seen = set()
    for f in all_findings:
        k = (f["file"], f["line"], f["kind"], f["text"])
        if k in seen:
            continue
        seen.add(k)
        uniq.append(f)

    report = ROOT / "scripts" / "scan_ui_i18n_missing_report.json"
    report.write_text(json.dumps(uniq, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[findings] total: {len(uniq)} -> {report}")
    by_file = {}
    for f in uniq:
        by_file.setdefault(f["file"], []).append(f)
    for fname in sorted(by_file):
        items = by_file[fname]
        print(f"\n=== {fname} ({len(items)}) ===")
        for f in items[:80]:
            print(f"  L{f['line']:>5} [{f['kind']}] <{f['element']}> {f['text']!r} -> {f['residual']!r}")
        if len(items) > 80:
            print(f"  ... and {len(items)-80} more")


if __name__ == "__main__":
    main()
