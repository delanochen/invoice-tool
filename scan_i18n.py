# -*- coding: utf-8 -*-
"""只读扫描：统计模板中用户可见的中文文本（sizing pass），不修改任何文件。"""
import re, glob, os, sys

repo = r"C:\Users\admin\Documents\Codex\projects\invoice-tool"
tpl_dir = os.path.join(repo, "templates")

def strip_blocks(html):
    html = re.sub(r"{%-?\s*comment\s*-?%}.*?{%-?\s*endcomment\s*-?%}", "", html, flags=re.S)
    html = re.sub(r"{#.*?#}", "", html, flags=re.S)
    html = re.sub(r"<script.*?</script>", "", html, flags=re.S | re.I)
    html = re.sub(r"<style.*?</style>", "", html, flags=re.S | re.I)
    return html

def text_chunks(html):
    html = strip_blocks(html)
    html = re.sub(r"<[^>]+>", "\n", html)
    html = re.sub(r"{{.*?}}", " ", html, flags=re.S)
    html = re.sub(r"{%-?.*?-?%}", " ", html, flags=re.S)
    chunks = [c.strip() for c in html.splitlines() if c.strip()]
    return chunks

total_cn = 0
per_tpl = {}
for path in sorted(glob.glob(os.path.join(tpl_dir, "*.html"))):
    name = os.path.basename(path)
    html = open(path, encoding="utf-8-sig").read()
    cn = [c for c in text_chunks(html) if re.search(r"[\u4e00-\u9fff]", c)]
    if cn:
        per_tpl[name] = len(cn)
        total_cn += len(cn)

print("模板文件数:", len(glob.glob(os.path.join(tpl_dir, "*.html"))))
print("含中文文本的模板数:", len(per_tpl))
print("中文文本片段总数(粗):", total_cn)
print("--- 按数量 top 30 ---")
for k, v in sorted(per_tpl.items(), key=lambda x: -x[1])[:30]:
    print(f"{v:4d}  {k}")
