"""Keep date bounds inside the existing work-order filter form."""
import re
from pathlib import Path


def update(source):
    forms=list(re.finditer(r'<form\b[^>]*>.*?</form>',source,re.S))
    keyword=[match for match in forms if 'name="q"' in match[0]]
    if len(keyword)==2:
        primary,extra=keyword
        assert 'service-order-filterbar' in primary[0]
        assert 'expected_settlement_from' in extra[0] and 'expected_settlement_to' in extra[0]
        dates=re.findall(r'<label\b[^>]*>[^<]*<input\b[^>]*name="expected_settlement_(?:from|to)"[^>]*></label>',extra[0])
        assert len(dates)==2
        dates=[label.replace(' class="erp-field"','') for label in dates]
        old=primary[0]
        action=old.index('    <div class="erp-filter-actions">')
        merged=old[:action]+'    '+'\n    '.join(dates)+'\n'+old[action:]
        merged=merged.replace('method="get"','method="get" data-erp-filter',1)
        # Replace back to front so offsets of the original form stay valid.
        source=source[:extra.start()]+source[extra.end():]
        source=source[:primary.start()]+merged+source[primary.end():]
    validate(source)
    return source


def validate(source):
    filters=[match[0] for match in re.finditer(r'<form\b[^>]*>.*?</form>',source,re.S) if 'name="q"' in match[0]]
    assert len(filters)==1,'Expected one keyword/date filter form'
    form=filters[0]
    for name in ['q','expected_settlement_from','expected_settlement_to']:
        assert source.count('name="'+name+'"')==1,name
        assert 'name="'+name+'"' in form,name
    assert form.count('data-erp-filter')==1
    if 'service-order-filterbar' in form:
        for name in ['status','client_id','buyer_id','manufacturer_id','work_order_type_id','settlement','invoice','payment']:
            assert 'name="'+name+'"' in form,name
        assert form.count('type="submit"')==1


if __name__=='__main__':
    path=Path('templates/service_orders.html')
    original=path.read_text(encoding='utf-8')
    result=update(original)
    if result!=original:path.write_text(result,encoding='utf-8')
    validate(Path('templates/service_order_query.html').read_text(encoding='utf-8'))
    print('One complete filter form on both list and report; all fields retained')
