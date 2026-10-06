"""Read-only reports over the existing employee attachment records."""
from flask import abort, g, render_template, request


def register_staff_reports(app, api):
    @app.get('/reports/user-certificates')
    @api['login_required']
    def user_certificate_query():
        if not api['has_menu_permission']('user_certificate_query') or not api['has_action_permission']('users', 'view'):
            abort(403)
        users = [dict(user) for user in api['db']().execute('select * from users order by name, id')
                 if api['can_manage_user_record'](user)]
        allowed = {user['id'] for user in users}
        q = request.args.get('q', '').strip().casefold()
        person = request.args.get('user_id', '')
        presence = request.args.get('presence', 'yes')
        active = request.args.get('active', '')
        all_rows = []
        for row in api['db']().execute('''
            select users.id as user_id, users.name, users.email, users.role, users.country_code, users.is_active,
                   a.id as attachment_id, a.original_filename, a.uploaded_at, uploader.name as uploader_name
            from users left join user_attachments a on a.user_id = users.id
            left join users uploader on uploader.id = a.uploaded_by
            order by users.name, users.id, a.original_filename, a.id
        '''):
            if row['user_id'] not in allowed:
                continue
            if person and str(row['user_id']) != person:
                continue
            if q and q not in ' '.join(str(row[key] or '') for key in ('name', 'email', 'original_filename', 'country_code')).casefold():
                continue
            all_rows.append(dict(row))

        def filtered_rows(*, attachment_presence, account_active):
            result = all_rows
            if account_active in {'0', '1'}:
                result = [row for row in result if str(row['is_active']) == account_active]
            if attachment_presence == 'yes':
                result = [row for row in result if row['attachment_id'] is not None]
            elif attachment_presence == 'no':
                result = [row for row in result if row['attachment_id'] is None]
            return result

        rows = filtered_rows(attachment_presence=presence, account_active=active)
        navigation_counts = {
            # These mirror the four sidebar links. Person and keyword filters remain
            # in scope, while each count uses its destination's presence/status.
            'all': len(filtered_rows(attachment_presence='', account_active='')),
            'attached': len(filtered_rows(attachment_presence='yes', account_active=active)),
            'missing': len(filtered_rows(attachment_presence='no', account_active=active)),
            'inactive': len(filtered_rows(attachment_presence='', account_active='0')),
        }
        return render_template('user_certificate_query.html', rows=rows, people=users,
                               employee_count=len({row['user_id'] for row in rows}),
                               attachment_count=sum(row['attachment_id'] is not None for row in rows),
                               navigation_counts=navigation_counts)
