"""Field photo PWA endpoints; keeps the existing application permission model."""
import hashlib
import math
import os
import re
import subprocess
from datetime import datetime, timedelta, timezone
from functools import wraps
from io import BytesIO
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from flask import abort, g, jsonify, render_template, request, send_file, session, url_for
from PIL import Image
from werkzeug.utils import secure_filename
from image_processing import compress_image, create_thumbnail


def init_field_schema(connection):
    connection.executescript('''
        create table if not exists field_photos (
            id integer primary key autoincrement,
            client_id text not null,
            order_id integer not null references service_orders(id) on delete cascade,
            user_id integer not null references users(id),
            captured_at text not null, received_at text not null,
            capture_date text not null, timezone_name text not null,
            latitude real not null, longitude real not null, accuracy real not null,
            location_note text not null default '', note text not null default '',
            source text not null, relative_path text not null unique,
            content_hash text not null, bytes integer not null,
            unique(user_id, client_id)
        );
        create index if not exists idx_field_photos_order_date on field_photos(order_id, capture_date);
        create index if not exists idx_field_photos_user on field_photos(user_id);
    ''')
    columns = {row[1] for row in connection.execute('pragma table_info(field_photos)')}
    for name in ('equipment_number', 'position_number', 'equipment_session'):
        if name not in columns:
            connection.execute(f"alter table field_photos add column {name} text not null default ''")


def register_field_routes(app, api):
    def access(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if not g.user:
                return jsonify(error='请联网登录员工账号后继续。'), 401
            if not api['has_menu_permission']('field_work') or not api['has_action_permission']('service_orders', 'view'):
                abort(403)
            return view(*args, **kwargs)
        return wrapped

    def token():
        if 'field_token' not in session:
            session['field_token'] = api['secrets'].token_urlsafe(32)
        return session['field_token']

    def check_write():
        if not api['secrets'].compare_digest(request.headers.get('X-Field-Token', ''), session.get('field_token') or '!'):
            abort(403)

    def order_rows():
        clauses, params = api['service_order_access_filters']()
        clauses.append("service_orders.status != 'closed'")
        return api['db']().execute('''
            select service_orders.id, service_orders.order_number, service_orders.client_name,
                   service_orders.site_address, buyers.latitude, buyers.longitude,
                   clients.name as customer_name
            from service_orders left join buyers on buyers.id = service_orders.buyer_id
            left join clients on clients.id = service_orders.client_id
            where ''' + ' and '.join(clauses) + ' order by service_orders.id desc', params).fetchall()

    def photo_clauses():
        clauses, params = api['service_order_access_filters']()
        if api['normalized_role']() in {'employee', 'external_employee'}:
            clauses.append('p.user_id = ?')
            params.append(g.user['id'])
        return clauses, params

    def photo_rows():
        clauses, params = photo_clauses()
        for key, column in [('order_id', 'p.order_id'), ('user_id', 'p.user_id'),
                            ('date_from', 'p.capture_date'), ('date_to', 'p.capture_date')]:
            value = request.args.get(key, '').strip()
            if value:
                op = '>=' if key == 'date_from' else '<=' if key == 'date_to' else '='
                clauses.append(f'{column} {op} ?')
                params.append(value)
        q = request.args.get('q', '').strip()
        if q:
            clauses.append('(service_orders.order_number like ? or service_orders.client_name like ? or users.name like ? or p.note like ? or clients.name like ?)')
            params.extend(['%' + q + '%'] * 5)
        return api['db']().execute('''
            select p.*, service_orders.order_number, service_orders.client_name as site_name,
                   users.name as employee_name, clients.name as customer_name
            from field_photos p join service_orders on service_orders.id = p.order_id
            join users on users.id = p.user_id
            left join clients on clients.id = service_orders.client_id
            where ''' + (' and '.join(clauses) or '1=1') + ' order by p.captured_at desc, p.id desc limit 2001', params).fetchall()

    def photo_file(row, thumb=False):
        root = api['shared_photos_root']()
        relative = Path(row['relative_path'])
        if thumb:
            relative = Path(relative.parts[0], 'thumbnails', *relative.parts[2:])
        target = (root / relative).resolve()
        if not target.is_relative_to(root) or not target.is_file():
            abort(404)
        return target

    @app.get('/field/')
    def field_work():
        # Public, data-free shell. Employee data is fetched only after authentication.
        return render_template('field_work.html')

    @app.get('/field/manifest.webmanifest')
    def field_manifest():
        return jsonify(name='Prasinos Power 现场工作', short_name='Prasinos', id='/field/',
                       start_url='/field/', scope='/field/', display='standalone',
                       background_color='#f3f7f6', theme_color='#0f766e',
                       icons=[dict(src=f'/static/field-icon-{size}.png', sizes=f'{size}x{size}', type='image/png') for size in (192, 512)])

    @app.get('/field/sw.js')
    def field_service_worker():
        response = app.send_static_file('field-sw.js')
        response.headers['Cache-Control'] = 'no-cache'
        return response

    @app.get('/api/field/session')
    @access
    def field_session():
        return jsonify(user=dict(id=g.user['id'], name=g.user['name']), csrf=token(),
                       orders=[dict(row) for row in order_rows()], version=api['APP_VERSION'],
                       can_capture=api['has_action_permission']('service_reports', 'create'),
                       create_order_url=url_for('new_service_order') if api['can_create_service_order']() else None,
                       distance_limit=max(100, min(10000, int(os.environ.get('FIELD_DISTANCE_METERS', '500')))))

    @app.post('/api/field/photos')
    @access
    def upload_field_photo():
        check_write()
        if not api['has_action_permission']('service_reports', 'create'):
            abort(403)
        if request.form.get('user_id') != str(g.user['id']):
            return jsonify(error='照片属于另一个账号，请切换回拍摄账号上传。'), 409
        key = request.form.get('client_id', '')
        if not re.fullmatch(r'[0-9a-f]{32}', key):
            return jsonify(error='照片编号无效。'), 422
        try:
            order_id = int(request.form.get('order_id', ''))
            order = api['require_service_order'](order_id)
            captured = datetime.fromisoformat(request.form.get('captured_at', '').replace('Z', '+00:00'))
            if captured.tzinfo is None or captured.year < 2000 or captured > datetime.now(timezone.utc) + timedelta(minutes=10):
                raise ValueError
            tz_name = request.form.get('timezone_name', 'UTC')
            local = captured.astimezone(ZoneInfo(tz_name))
            lat, lng, accuracy = (float(request.form.get(k, '')) for k in ('latitude', 'longitude', 'accuracy'))
            if not all(math.isfinite(v) for v in (lat, lng, accuracy)) or not (-90 <= lat <= 90 and -180 <= lng <= 180 and 0 <= accuracy <= 100000):
                raise ValueError
        except (ValueError, TypeError, OverflowError, ZoneInfoNotFoundError):
            return jsonify(error='拍摄时间、时区或定位数据不正确。'), 422
        source = request.form.get('source', '')
        if source not in {'camera', 'file'}:
            return jsonify(error='照片来源无效。'), 422
        note = request.form.get('note', '').strip()[:1000]
        has_device_metadata = any(key in request.form for key in ('equipment_number', 'position_number', 'equipment_session', 'no_equipment_number'))
        equipment_number = request.form.get('equipment_number', '').strip()[:200]
        position_number = request.form.get('position_number', '').strip()[:200]
        equipment_session = request.form.get('equipment_session', '').strip()[:64]
        no_equipment_number = request.form.get('no_equipment_number') == 'true'
        if has_device_metadata and ((not equipment_number and not no_equipment_number) or not equipment_session):
            return jsonify(error='请确认设备编号，或明确选择“此设备无编号”。'), 422
        if not has_device_metadata:
            equipment_session = 'legacy-pending-photo'
        location_note = request.form.get('location_note', '').strip()[:500]
        photo = request.files.get('photo')
        content = photo.read(15 * 1024 * 1024 + 1) if photo else b''
        if not content or len(content) > 15 * 1024 * 1024:
            return jsonify(error='照片为空或超过 15MB。'), 422
        digest = hashlib.sha256(content).hexdigest()
        try:
            with Image.open(BytesIO(content)) as image:
                if image.format not in {'JPEG', 'PNG', 'WEBP'} or image.width * image.height > 40_000_000:
                    raise ValueError
                image.verify()
        except (OSError, ValueError, Image.DecompressionBombError):
            return jsonify(error='无法读取照片，请重新拍摄。'), 422
        db = api['db']()
        # Serialize retries before writing files: timeout/lost responses never duplicate a photo.
        db.execute('begin immediate')
        try:
            previous = db.execute('select * from field_photos where user_id = ? and client_id = ?', (g.user['id'], key)).fetchone()
            if previous:
                db.rollback()
                if previous['content_hash'] != digest or previous['order_id'] != order_id:
                    return jsonify(error='照片编号已用于不同的内容。'), 409
                return jsonify(ok=True, id=previous['id'], duplicate=True)
            root = api['shared_photos_root']()
            order_folder = secure_filename(order['order_number'])
            filename = f"{local:%Y%m%d_%H%M%S}-u{g.user['id']}-{key}.jpg"
            relative = Path(order_folder, 'pictures', local.date().isoformat(), filename)
            target = (root / relative).resolve()
            thumb = (root / order_folder / 'thumbnails' / local.date().isoformat() / filename).resolve()
            if not target.is_relative_to(root) or not thumb.is_relative_to(root):
                abort(403)
            compress_image(BytesIO(content), target, max_size=1800, quality=78)
            create_thumbnail(target, thumb)
            cursor = db.execute('''insert into field_photos
                (client_id, order_id, user_id, captured_at, received_at, capture_date, timezone_name,
                 latitude, longitude, accuracy, location_note, note, source, relative_path, content_hash, bytes,
                 equipment_number, position_number, equipment_session)
                values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                (key, order_id, g.user['id'], captured.astimezone(timezone.utc).isoformat(), api['now'](),
                 local.date().isoformat(), tz_name, lat, lng, accuracy, location_note, note, source,
                 relative.as_posix(), digest, target.stat().st_size, equipment_number, position_number, equipment_session))
            api['log_action']('create', 'field_photo', cursor.lastrowid, order['order_number'], 'PWA 工单照片上传')
            db.commit()
        except Exception:
            db.rollback()
            raise
        return jsonify(ok=True, id=cursor.lastrowid)

    @app.post('/api/field/recognize-equipment')
    @access
    def recognize_equipment():
        check_write()
        photo = request.files.get('photo')
        content = photo.read(10 * 1024 * 1024 + 1) if photo else b''
        if not content or len(content) > 10 * 1024 * 1024:
            return jsonify(error='铭牌照片为空或超过 10MB。'), 422
        try:
            with Image.open(BytesIO(content)) as image:
                if image.format not in {'JPEG', 'PNG', 'WEBP'} or image.width * image.height > 40_000_000:
                    raise ValueError
                image.verify()
            result = subprocess.run(['tesseract', 'stdin', 'stdout', '--psm', '11', '-l', 'eng'], input=content,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20, check=False)
        except (OSError, ValueError, Image.DecompressionBombError):
            return jsonify(error='无法读取铭牌照片。'), 422
        except subprocess.TimeoutExpired:
            return jsonify(error='铭牌识别超时，请重试或手工输入。'), 504
        if result.returncode != 0:
            return jsonify(error='服务器暂时无法识别铭牌，请手工输入。'), 503
        text = result.stdout.decode('utf-8', errors='ignore')
        labels = r'(?:machine\s*(?:number|no\.?|#)|serial\s*(?:number|no\.?|#)|s\s*/?\s*n|生产编号|设备编号)'
        candidates = []
        for match in re.finditer(labels + r'\s*[:：#-]?\s*([A-Z0-9][A-Z0-9._/-]{3,})', text, re.I):
            value = match.group(1).strip('._/-')
            if value and value.casefold() not in {item.casefold() for item in candidates}:
                candidates.append(value)
        return jsonify(ok=True, candidates=candidates[:5])

    @app.get('/api/field/photos')
    @access
    def field_photo_list():
        rows = photo_rows()
        return jsonify(rows=[dict(row) | dict(preview=url_for('field_photo_preview', photo_id=row['id']),
                                             thumbnail=url_for('field_photo_preview', photo_id=row['id'], thumb=1)) for row in rows[:2000]],
                       truncated=len(rows) > 2000)

    @app.get('/field/photos/<int:photo_id>')
    @access
    def field_photo_preview(photo_id):
        row = api['db']().execute('select * from field_photos where id = ?', (photo_id,)).fetchone()
        if not row:
            abort(404)
        api['require_service_order'](row['order_id'])
        if api['normalized_role']() in {'employee', 'external_employee'} and row['user_id'] != g.user['id']:
            abort(403)
        response = send_file(photo_file(row, request.args.get('thumb') == '1'), mimetype='image/jpeg',
                             as_attachment=request.args.get('download') == '1')
        response.headers['Cache-Control'] = 'no-store, private'
        return response

    @app.get('/reports/field-photos')
    @access
    def field_photo_query():
        rows = photo_rows()
        return render_template('field_photo_query.html', rows=rows[:2000], truncated=len(rows) > 2000)

    @app.get('/api/field/photos.xlsx')
    @access
    def field_photo_export():
        rows = photo_rows()
        if len(rows) > 2000:
            return jsonify(error='结果超过 2000 条，请缩小日期或工单范围后导出。'), 422
        headers = ['工单', '客户', '站点', '员工', '拍摄日期', '设备拍摄时间（UTC）', '归档时区', '上传时间', '纬度', '经度', '精度（米）', '备注', '位置确认说明', '来源', '文件路径']
        keys = ['order_number', 'customer_name', 'site_name', 'equipment_number', 'position_number', 'employee_name', 'capture_date', 'captured_at', 'timezone_name', 'received_at', 'latitude', 'longitude', 'accuracy', 'note', 'location_note', 'source', 'relative_path']
        buffer = api['build_simple_xlsx'](headers, [[str(row[key] or '') for key in keys] for row in rows], sheet_name='工单照片台账')
        return send_file(buffer, as_attachment=True, download_name='field-photos.xlsx', mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

    @app.post('/api/field/order-request')
    @access
    def field_order_request():
        check_write()
        data = request.get_json(silent=True) or {}
        detail = str(data.get('detail', '')).strip()[:1000]
        if not detail:
            return jsonify(error='请填写站点和工作说明。'), 422
        api['notify_role'](['admin', 'manager'], '现场员工申请新建工单', f"{g.user['name']}：{detail}", url_for('service_orders'))
        api['db']().commit()
        return jsonify(ok=True)

    @app.after_request
    def no_cache_field_data(response):
        if request.path.startswith('/api/field/'):
            response.headers['Cache-Control'] = 'no-store, private'
        return response
