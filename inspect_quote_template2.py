# -*- coding: utf-8 -*-
"""Inspect title/borders/header/footer styling of the quote template."""
import zipfile
from xml.etree import ElementTree as ET

W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
NS = {'w': W}
PATH = r'C:\Users\admin\Desktop\Prasinos_Power_Quote_Template_v2.docx'


def q(t):
    return '{%s}%s' % (W, t)


def main():
    z = zipfile.ZipFile(PATH)
    doc = ET.fromstring(z.read('word/document.xml'))
    body = doc.find(q('body'))
    children = [c for c in body]

    print('=== TITLE PARA ===')
    p0 = children[0]
    for r in p0.iter(q('r')):
        rPr = r.find(q('rPr'))
        if rPr is None:
            continue
        col = None
        c = rPr.find(q('color'))
        if c is not None:
            col = c.get(q('val'))
        sz = rPr.find(q('sz'))
        szv = int(sz.get(q('val'))) / 2 if sz is not None else None
        b = 'B' if rPr.find(q('b')) is not None else ''
        print('run:', ''.join(t.text or '' for t in r.iter(q('t'))),
              'sz=', szv, b, 'color=', col)

    tbl = children[1]
    tcPr = tbl.find('.//' + q('tcPr'))
    if tcPr is not None:
        b = tcPr.find(q('tcBorders'))
        if b is not None:
            for edge in b:
                print('cell border:', edge.tag.split('}')[1],
                      {k.split('}')[1]: v for k, v in edge.attrib.items()})
    tb = tbl.find(q('tblPr') + '/' + q('tblBorders'))
    if tb is not None:
        for edge in tb:
            print('tblBorders:', edge.tag.split('}')[1],
                  {k.split('}')[1]: v for k, v in edge.attrib.items()})

    print('=== HEADER PARAS ===')
    hdr = ET.fromstring(z.read('word/header1.xml'))
    for p in hdr.findall('.//' + q('p')):
        runs = []
        for r in p.findall(q('r')):
            rPr = r.find(q('rPr'))
            sz = None
            col = None
            bold = False
            if rPr is not None:
                s = rPr.find(q('sz'))
                if s is not None:
                    sz = int(s.get(q('val'))) / 2
                c = rPr.find(q('color'))
                if c is not None:
                    col = c.get(q('val'))
                bold = rPr.find(q('b')) is not None
            runs.append((sz, col, bold))
        text = ' '.join(t.text or '' for t in p.iter(q('t')))
        print('para:', repr(text), runs)
    print('header has drawing:', hdr.find('.//' + q('drawing')) is not None)

    print('=== FOOTER PARAS ===')
    ftr = ET.fromstring(z.read('word/footer1.xml'))
    for p in ftr.findall('.//' + q('p')):
        text = ' '.join(t.text or '' for t in p.iter(q('t')))
        has_fld = p.find('.//' + q('fldChar')) is not None or p.find('.//' + q('instrText')) is not None
        print('para:', repr(text), 'field=', has_fld)


if __name__ == '__main__':
    main()
