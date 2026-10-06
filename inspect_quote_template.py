# -*- coding: utf-8 -*-
"""Inspect Prasinos Power Quote Template v2 docx layout details."""
import zipfile
from xml.etree import ElementTree as ET

PATH = r'C:\Users\admin\Desktop\Prasinos_Power_Quote_Template_v2.docx'
W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
NS = {'w': W}


def qn(tag):
    return '{%s}%s' % (W, tag)


def col_widths(tbl):
    grid = tbl.find(qn('tblGrid'), NS)
    if grid is None:
        return None
    return [int(c.get(qn('w'))) for c in grid.findall(qn('gridCol'))]


def cell_summary(cell):
    texts = ''.join(t.text or '' for t in cell.iter(qn('t'))).strip()
    tcw = cell.find('.//' + qn('tcW'))
    w = int(tcw.get(qn('w'))) if tcw is not None else None
    # font size / bold / shading
    sz = ''
    b = ''
    shd = ''
    tcPr = cell.find(qn('tcPr'))
    if tcPr is not None:
        s = tcPr.find(qn('shd'))
        if s is not None:
            shd = '#' + (s.get(qn('fill')) or '')
    for r in cell.iter(qn('r')):
        rPr = r.find(qn('rPr'))
        if rPr is not None:
            if rPr.find(qn('b')) is not None:
                b = 'B'
            szt = rPr.find(qn('sz'))
            if szt is not None and not sz:
                sz = str(int(szt.get(qn('val'))) / 2)
    return '%s%s sz=%s fill=%s: %s' % (w, b, sz, shd, texts[:26])


def main():
    z = zipfile.ZipFile(PATH)
    doc = ET.fromstring(z.read('word/document.xml'))
    body = doc.find(qn('body'))
    tbls = [c for c in body if c.tag.split('}')[1] == 'tbl']
    for i in [0, 2, 3, 4, 5, 6, 7, 8]:
        tbl = tbls[i]
        print('=== TABLE[%d] cols=%s rows=%d ===' % (
            i, col_widths(tbl), len(tbl.findall(qn('tr')))))
        for r, tr in enumerate(tbl.findall(qn('tr'))):
            cells = tr.findall(qn('tc'))
            print('  R%d: %s' % (r, ' | '.join(cell_summary(c) for c in cells)))
    # header / footer
    print('\n=== HEADER ===')
    hdr = ET.fromstring(z.read('word/header1.xml'))
    print(''.join(t.text or '' for t in hdr.iter(qn('t'))).strip()[:200])
    print('\n=== FOOTER ===')
    ftr = ET.fromstring(z.read('word/footer1.xml'))
    print(''.join(t.text or '' for t in ftr.iter(qn('t'))).strip()[:200])


if __name__ == '__main__':
    main()
