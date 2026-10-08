# -*- coding: utf-8 -*-
"""Triage backend py-literals: simulate substring replacement with effective en
keys and list only those that still leave residual Chinese."""
import json, re, subprocess

def extract_keys(region):
    return set(m.group(1) for m in re.finditer(r"""["']((?:[^"'\\]|\\.)*[\u4e00-\u9fff](?:[^"'\\]|\\.)*)["']\s*:""", region))

def region_between(text, start_marker, end_marker):
    i = text.find(start_marker)
    if i < 0:
        return ""
    j = text.find(end_marker, i + len(start_marker))
    return text[i:] if j < 0 else text[i:j]

ui = open('static/ui-i18n.js', encoding='utf-8').read()
sup = open('static/ui-i18n-supplement.js', encoding='utf-8').read()
ui_en = region_between(ui, 'const en = {', 'const nl = {')
tail_start = ui.find('Object.assign(en, {')
tail_region = ui[tail_start:] if tail_start >= 0 else ''
reg = region_between(ui, 'const registrationTranslations = {', 'const supplement')
rev = region_between(ui, 'const reviewedTerms = {', 'Object.assign(en, {')
sup_en = region_between(sup, '"en": {', '"nl": {')
keys = extract_keys(ui_en) | extract_keys(tail_region) | extract_keys(reg) | extract_keys(rev) | extract_keys(sup_en)
print('effective en keys:', len(keys))

CJK = re.compile(r'[\u4e00-\u9fff]')
def residual_cjk(fragment):
    result = fragment
    for key in sorted(keys, key=len, reverse=True):
        if key and key in result:
            result = result.replace(key, ' ')
    runs = re.findall(r'[\u4e00-\u9fff，。、；：（）「」“”·\-–—%/]{1,}', result)
    return ' | '.join(r for r in runs if CJK.search(r))

rep = json.load(open('scripts/scan_ui_i18n_missing_report.json', encoding='utf-8'))
py = [f for f in rep if f['kind'] == 'py-literal']
print('py-literal findings:', len(py))

bad = []
for f in py:
    res = residual_cjk(f['text'])
    if res:
        bad.append((f['file'], f['line'], f['text'], res))
print('=== py-literals with residual after substring simulation:', len(bad))
for fname, line, text, res in bad:
    print('%s:%s %r -> %r' % (fname, line, text, res))

print()
print('=== 特定串检查 ===')
for s in ['；重新编辑前须先删除对应发票', '）', '同一员工的「已批准 / 待付款」付款单才能合并；一张支票只有一名收款人，跨员工请分批开。 发放后状态变为「已付款」，报销来源同步置为已发放，对账时按批次整批核销。']:
    print('%r -> %r' % (s, residual_cjk(s)))

para = ('同一员工的「已批准 / 待付款」付款单才能合并；一张支票只有一名收款人，跨员工请分批开。\n'
        '      发放后状态变为「已付款」，报销来源同步置为已发放，对账时按批次整批核销。')
print('段落(含\\n): residual=%r' % residual_cjk(para))
travel = ('复用智能日报的 Google 路线 / 静态地图能力：① 起点、终点、中间停靠点生成路线地图佐证；② 已知终点、总距离与大概方位时，在推算出的起点附近随机找一家宾馆并验证实际驾车距离。\n'
          '      所有 Google 调用在服务端完成，API Key 不会暴露给浏览器。')
print('travel(含\\n): residual=%r' % residual_cjk(travel))

print()
print('=== 模板原始文本检查 ===')
for fn, pat in [('templates/employee_ledger.html', '同一员工'), ('templates/travel_tools.html', '复用智能日报')]:
    lines = open(fn, encoding='utf-8').read().splitlines()
    for i, line in enumerate(lines, 1):
        if pat in line:
            print('%s:%d raw=%r' % (fn, i, line.strip()))
            print('  residual=%r' % residual_cjk(line))
            break
