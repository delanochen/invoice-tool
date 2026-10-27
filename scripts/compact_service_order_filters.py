"""Compact the existing work-order filters into one desktop row."""
import re
from pathlib import Path

STYLE='''
<style id="compact-work-order-filter">
@media (min-width: 1025px) {
  .erp-app .service-order-filterbar {
    grid-template-columns: minmax(130px,1.25fr) repeat(8,minmax(72px,1fr)) 124px auto;
  }
}
</style>
'''


def update(source):
    source,n=re.subn(r'^[ \t]*<label\b[^>]*>[^<]*<input\b[^>]*name="expected_settlement_to"[^>]*></label>[^\S\n]*\n?', '', source, count=1, flags=re.M)
    assert n==1,'Expected one settlement end-date input'
    if 'service-order-filterbar' not in source:
        source=source.replace('class="erp-filter erp-filterbar no-print"','class="erp-filter erp-filterbar service-order-filterbar no-print"',1)
    assert 'compact-work-order-filter' not in source
    anchor='{% endblock %}'
    end=source.index(anchor,source.index('{% block head %}'))
    source=source[:end]+STYLE+source[end:]
    validate(source)
    return source


def validate(source):
    assert 'name="expected_settlement_to"' not in source
    forms=[m[0] for m in re.finditer(r'<form\b[^>]*>.*?</form>',source,re.S) if 'name="q"' in m[0]]
    assert len(forms)==1
    assert forms[0].count('name="q"')==1
    assert forms[0].count('name="expected_settlement_date"')==1
    assert 'repeat(8,minmax(72px,1fr)) 124px auto' in source
    assert forms[0].count('type="submit"')==1


if __name__=='__main__':
    p=Path('templates/service_orders.html')
    p.write_text(update(p.read_text(encoding='utf-8')),encoding='utf-8')
    print('Single list filter form; end date removed; compact desktop grid applied')
