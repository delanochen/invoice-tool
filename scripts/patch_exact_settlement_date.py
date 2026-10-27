"""Switch the work-order list to one exact settlement date and bump release version."""
from pathlib import Path


def replace(path,old,new):
    p=Path(path);s=p.read_text(encoding='utf-8')
    assert s.count(old)==1,(path,old,s.count(old))
    p.write_text(s.replace(old,new),encoding='utf-8')


def apply():
    p='invoice_tool/service_orders/routes.py'
    replace(p,'optional_settlement_date, settlement_date_filters','optional_settlement_date, exact_settlement_date_filter')
    replace(p,'settlement_date_filters(request.args, clauses, params)','exact_settlement_date_filter(request.args, clauses, params)')
    p='templates/service_orders.html'
    replace(p,'预计结算日起','预计结算日')
    replace(p,'name="expected_settlement_from"','name="expected_settlement_date"')
    replace(p,'{{ expected_settlement_from }}','{{ expected_settlement_date }}')
    old=tuple(map(int,Path('VERSION').read_text().strip().split('.')))
    assert old<(0,1,432),old
    Path('VERSION').write_text('0.1.432\n',encoding='utf-8')
    print('List uses exact expected settlement date; VERSION=0.1.432')


if __name__=='__main__':
    apply()
