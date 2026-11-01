"""Render synthetic management fixtures for browser checks; no app or DB import."""
from pathlib import Path
from types import SimpleNamespace
from jinja2 import Environment, ChoiceLoader, DictLoader, FileSystemLoader, ChainableUndefined

root = Path(__file__).resolve().parents[1]
out = root / 'tmp' / 'list-actions'
out.mkdir(parents=True, exist_ok=True)
env = Environment(loader=ChoiceLoader([
    DictLoader({'base.html': '{% block head %}{% endblock %}{% block content %}{% endblock %}{% block scripts %}{% endblock %}'}),
    FileSystemLoader(root / 'templates'),
]), undefined=ChainableUndefined, autoescape=True)
env.filters['money'] = lambda value: str(value)
env.filters['role_label'] = lambda value: value
rows = [dict(id=i, name=f'Synthetic {i:03}', email=f'user{i}@example.invalid',
             client_number=f'C{i}', buyer_number=f'B{i}', owner_number=f'O{i}',
             manufacturer_number=f'M{i}', project_type='invoice', unit_price=1,
             default_amount=1, tax_rate=0, is_active=i % 2, role='employee',
             created_at='2026-10-31', code=f'T{i}') for i in range(1, 61)]
context = dict(
    url_for=lambda endpoint, **kw: '/static/' + kw['filename'] if endpoint == 'static' else '/' + endpoint + '/' + str(next(iter(kw.values()), '')),
    has_action_permission=lambda *args: True,
    is_external_manager=lambda: False,
    can_assign=True, can_approve=True, can_manage=True,
    normalized_role=lambda role='admin': role,
    requires_user_address=lambda role: False,
    state_label=lambda value: str(value),
    project_type_labels={'invoice': '发票', 'expense': '报销'}, expense_map=[],
    user_attachments={row['id']: [] for row in rows}, tax_status_by_user={},
    g=SimpleNamespace(user=SimpleNamespace(id=100)),
    current_language='zh-CN', sort='buyer_number', direction='asc', q='', state_names={},
    role_options={'employee': '员工'}, supported_languages={},
)
pages = ['manufacturers', 'owners', 'clients', 'work_order_types', 'projects', 'buyers', 'users']
context.update({page: rows for page in pages})
for page in pages:
    (out / (page + '.html')).write_text(env.get_template(page + '.html').render(**context), encoding='utf-8')
context['manufacturers'] = rows[:1]
(out / 'manufacturer_single.html').write_text(env.get_template('manufacturers.html').render(**context), encoding='utf-8')
context['has_action_permission'] = lambda *args: False
(out / 'manufacturer_readonly.html').write_text(env.get_template('manufacturers.html').render(**context), encoding='utf-8')
print('Rendered seven synthetic lists:', out)
