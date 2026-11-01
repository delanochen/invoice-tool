"""Exercise real grade/rate routes in invoice_test, rolling back every change."""
def main():
    import os
    import uuid
    from urllib.parse import urlparse
    from unittest.mock import patch
    from pathlib import Path
    import sys

    assert urlparse(os.environ['DATABASE_URL']).path == '/invoice_test'
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import app as module
    from rate_engine import EMPLOYEE_RATE_TYPES, employee_rate

    with module.app.app_context():
        connection = module.db()
        assert connection.execute('select current_database() as name').fetchone()['name'] == 'invoice_test'
        admin = connection.execute("select id from users where role = 'admin' order by id limit 1").fetchone()
        assert admin, 'Test administrator missing'
        name = 'Codex grade release ' + uuid.uuid4().hex
        try:
            # All test requests share this app context and connection. Prevent
            # route commits, then roll back the complete transaction below.
            with patch.object(connection, 'commit', return_value=None):
                with module.app.test_client() as client:
                    with client.session_transaction() as session:
                        session['user_id'] = admin['id']
                    response = client.post('/employee-grades', data=dict(
                        grade_name=name, car_allowance_method='none'))
                    assert response.status_code == 302, response.status_code
                    grade = connection.execute('select * from employee_grades where grade_name = ?', (name,)).fetchone()
                    assert grade and grade['car_allowance_method'] == 'none'
                    grade_id = grade['id']
                    connection.execute('update users set employee_grade_id = ? where id = ?', (grade_id, admin['id']))
                    values = {key: '20' for key, _, _ in EMPLOYEE_RATE_TYPES}
                    values.update(effective_from='2026-10-01', base_salary='100', meal_daily_amount='10')
                    response = client.post(f'/employee-grades/{grade_id}/rates', data=values)
                    assert response.status_code == 302, response.status_code
                    version = connection.execute('select * from employee_rate_versions where employee_grade_id = ?', (grade_id,)).fetchone()
                    assert version
                    assert employee_rate(connection, admin['id'], '2026-10-30', 'base_salary')['rate'] == 100
                    assert employee_rate(connection, admin['id'], '2026-10-30', 'meal_daily_amount')['rate'] == 10
                    for key in ['mileage', 'travel_hours', 'public_transport_hours', 'rental_drive_hours']:
                        assert employee_rate(connection, admin['id'], '2026-10-30', key)['rate'] == 0, key
                    page = client.get(f'/employee-grades?grade_id={grade_id}')
                    assert page.status_code == 200, page.status_code
                    html = page.get_data(as_text=True)
                    dialog = html.split(f'id="editGradeDialog{grade_id}"', 1)[1].split('</dialog>', 1)[0]
                    assert 'name="base_salary"' not in dialog
                    assert 'name="meal_daily_amount"' not in dialog
                    assert 'value="hourly"' not in dialog
                    rate_dialog = html.split(f'id="newRateVersionDialog{grade_id}"', 1)[1].split('</dialog>', 1)[0]
                    assert 'name="base_salary"' in rate_dialog
                    assert 'name="meal_daily_amount"' in rate_dialog
                    # Turning off travel pay must not replace configured
                    # nonzero transport rates with zeros in a new version.
                    assert 'name="mileage" value="20.00"' in rate_dialog
                    values.update(base_salary='200', meal_daily_amount='30')
                    response = client.post(f'/employee-grades/{grade_id}/rates/{version["id"]}/edit', data=values)
                    assert response.status_code == 302, response.status_code
                    assert employee_rate(connection, admin['id'], '2026-10-30', 'base_salary')['rate'] == 200
                    assert employee_rate(connection, admin['id'], '2026-10-30', 'meal_daily_amount')['rate'] == 30
                    # Older versions may lack the new items. Editing them must
                    # insert the items rather than silently ignore the values.
                    assert connection.execute('select current_database() as name').fetchone()['name'] == 'invoice_test'
                    connection.execute(
                        "delete from employee_rate_items where version_id = ? and rate_type in ('base_salary', 'meal_daily_amount')",
                        (version['id'],))
                    response = client.post(f'/employee-grades/{grade_id}/rates/{version["id"]}/edit', data=values)
                    assert response.status_code == 302, response.status_code
                    assert employee_rate(connection, admin['id'], '2026-10-30', 'meal_daily_amount')['rate'] == 30
                    print('TEST_GRADE_ROUTES_OK: create/edit/version fallback, allowance exclusion and complete page render.')
        finally:
            connection.rollback()
        assert connection.execute('select count(*) as n from employee_grades where grade_name = ?', (name,)).fetchone()['n'] == 0
        connection.rollback()
        print('TEST_TRANSACTION_ROLLED_BACK: temporary grade, rates and user assignment were not committed.')


if __name__ == '__main__':
    main()
