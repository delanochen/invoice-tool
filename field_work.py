"""Field photo PWA endpoints; keeps the existing application permission model."""
import hashlib
import math
import os
import re
import subprocess
import tempfile
import zipfile
from datetime import datetime, timedelta, timezone
from functools import wraps
from io import BytesIO
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from flask import abort, g, jsonify, render_template, request, send_file, session, url_for
from PIL import Image, ImageEnhance, ImageFilter, ImageOps
from werkzeug.utils import secure_filename
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from image_processing import compress_image, create_thumbnail
from photo_capture_date import resolve_capture_date


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
    for name in ('equipment_number', 'position_number', 'container_number', 'pump_fuse_numbers', 'equipment_session', 'photo_type',
                 'watermark_at', 'watermark_source', 'batch_id', 'technician_name', 'capture_date_source'):
        if name not in columns:
            connection.execute(f"alter table field_photos add column {name} text not null default ''")
    connection.execute("update field_photos set watermark_source = 'system' where watermark_source = ''")
    if 'technician_user_id' not in columns:
        connection.execute('alter table field_photos add column technician_user_id integer')
    if 'location_verified' not in columns:
        connection.execute('alter table field_photos add column location_verified integer not null default 1')
    # Normalize historical metadata as well as newly uploaded device identifiers.
    for row in connection.execute('select id, position_number, container_number from field_photos').fetchall():
        position = (row[1] or '').strip().upper()
        container = (row[2] or '').strip().upper()
        if position != row[1] or container != row[2]:
            connection.execute('update field_photos set position_number = ?, container_number = ? where id = ?',
                               (position, container, row[0]))


def register_field_routes(app, api):
    @app.after_request
    def field_response_version(response):
        if request.path.startswith('/api/field/'):
            response.headers['X-Field-Version'] = api['APP_VERSION']
            response.headers['Cache-Control'] = 'no-store, private'
        return response

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
        # The photo register is shared among all authenticated Field Work users.
        return [], []

    def photo_rows():
        clauses, params = photo_clauses()
        order_number = request.args.get('order_number', '').strip()
        if order_number:
            clauses.append('instr(lower(service_orders.order_number), lower(?)) > 0')
            params.append(order_number)
        technician = request.args.get('technician', '').strip()
        if technician:
            clauses.append("instr(lower(coalesce(nullif(trim(p.technician_name), ''), users.name)), lower(?)) > 0")
            params.append(technician)
        for key in ('equipment_number', 'position_number', 'container_number'):
            value = request.args.get(key, '').strip()
            if value:
                clauses.append(f'instr(lower(p.{key}), lower(?)) > 0')
                params.append(value)
        for key, column in [('order_id', 'p.order_id'), ('user_id', 'p.user_id'),
                            ('date_from', 'p.capture_date'), ('date_to', 'p.capture_date')]:
            value = request.args.get(key, '').strip()
            if value:
                op = '>=' if key == 'date_from' else '<=' if key == 'date_to' else '='
                clauses.append(f'{column} {op} ?')
                params.append(value)
        q = request.args.get('q', '').strip()
        if q:
            clauses.append('''(service_orders.order_number like ? or service_orders.client_name like ? or users.name like ?
                              or p.technician_name like ? or p.equipment_number like ? or p.position_number like ?
                              or p.container_number like ? or p.pump_fuse_numbers like ? or p.note like ? or clients.name like ?)''')
            params.extend(['%' + q + '%'] * 10)
        return api['db']().execute('''
            select p.*, service_orders.order_number, service_orders.client_name as site_name, service_orders.site_address,
                   users.name as employee_name, clients.name as customer_name
            from field_photos p join service_orders on service_orders.id = p.order_id
            join users on users.id = p.user_id
            left join clients on clients.id = service_orders.client_id
            where ''' + (' and '.join(clauses) or '1=1') + ' order by p.captured_at desc, p.id desc limit 2001', params).fetchall()

    def photo_order_rows():
        return api['db']().execute('''
            select distinct service_orders.id, service_orders.order_number, service_orders.client_name
            from field_photos p join service_orders on service_orders.id = p.order_id
            order by service_orders.order_number desc''').fetchall()

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
        technicians = api['db']().execute('''
            select id, name from users
            where is_active = 1 and role in ('admin', 'manager', 'finance', 'employee', 'internal', 'user', 'external_employee')
            order by name collate nocase
        ''').fetchall()
        return jsonify(user=dict(id=g.user['id'], name=g.user['name']), csrf=token(),
                       technicians=[dict(row) for row in technicians],
                       orders=[dict(row) for row in order_rows()], version=api['APP_VERSION'],
                       ledger_orders=[dict(row) for row in photo_order_rows()],
                       can_capture=api['has_action_permission']('service_reports', 'create'),
                       create_order_url=url_for('new_service_order') if api['can_create_service_order']() else None,
                       distance_limit=max(100, min(10000, int(os.environ.get('FIELD_DISTANCE_METERS', '500')))))

    @app.post('/api/field/verify-watermark-password')
    @access
    def verify_watermark_password():
        check_write()
        configured = api['get_setting']('field_watermark_time_password', '')
        supplied = request.get_json(silent=True) or {}
        valid = bool(configured) and api['secrets'].compare_digest(str(supplied.get('password', '')), configured)
        return jsonify(ok=valid), (200 if valid else 403)

    @app.post('/api/field/photos')
    @access
    def upload_field_photo():
        check_write()
        if not api['has_action_permission']('service_reports', 'create'):
            abort(403)
        # The authenticated server session is authoritative. A stale PWA draft
        # may carry an old local user id after iOS restores or refreshes the app.
        # Never attribute an upload to that client-supplied value.
        if not request.form or 'photo' not in request.files:
            # Log transport shape only, never photo bytes, names, tokens or notes.
            app.logger.warning(
                'field_upload_parse_failed mime=%s length=%s transfer=%s terminated=%s fields=%d files=%d',
                request.mimetype, request.content_length,
                request.environ.get('HTTP_TRANSFER_ENCODING', ''),
                request.environ.get('wsgi.input_terminated', False),
                len(request.form), len(request.files))
            return jsonify(error='上传数据未完整解析，照片仍保留在本机。请刷新页面后重试。',
                           code='upload_body_invalid'), 400
        raw_key = request.form.get('client_id', '')[:500]
        source = request.form.get('source', '')
        watermark_source = request.form.get('watermark_source', 'system').strip()
        if source not in {'camera', 'file'} and watermark_source == 'original':
            source = 'file'
        if source not in {'camera', 'file'}:
            app.logger.warning('field_upload_source_invalid source_present=%s watermark_present=%s fields=%d',
                               'source' in request.form, 'watermark_source' in request.form, len(request.form))
            return jsonify(error='照片来源无效。', code='upload_source_invalid'), 422
        if watermark_source not in {'system', 'original'} or (watermark_source == 'original' and source != 'file'):
            return jsonify(error='水印来源无效。'), 422
        original_import = source == 'file' and watermark_source == 'original'
        location_verified = False if original_import else request.form.get('location_verified', 'true') == 'true'
        try:
            order_id = int(request.form.get('order_id', ''))
        except (ValueError, TypeError):
            return jsonify(error='工单无效。'), 422
        order = api['require_service_order'](order_id)
        try:
            captured = datetime.fromisoformat(request.form.get('captured_at', '').replace('Z', '+00:00'))
            if captured.tzinfo is None or captured.year < 2000 or captured > datetime.now(timezone.utc) + timedelta(minutes=10):
                raise ValueError
            tz_name = request.form.get('timezone_name', 'UTC')
            local = captured.astimezone(ZoneInfo(tz_name))
            watermark = datetime.fromisoformat(request.form.get('watermark_at', request.form.get('captured_at', '')).replace('Z', '+00:00'))
            if watermark.tzinfo is None or watermark.year < 1900 or watermark.year > 2200:
                raise ValueError
            lat, lng, accuracy = (float(request.form.get(k, '')) for k in ('latitude', 'longitude', 'accuracy'))
            if not all(math.isfinite(v) for v in (lat, lng, accuracy)) or not (-90 <= lat <= 90 and -180 <= lng <= 180 and 0 <= accuracy <= 100000):
                raise ValueError
        except (ValueError, TypeError, OverflowError, ZoneInfoNotFoundError):
            if not original_import:
                return jsonify(error='拍摄时间、时区或定位数据不正确。'), 422
            captured = datetime.now(timezone.utc)
            watermark = captured
            tz_name = 'UTC'
            local = captured
            lat, lng, accuracy = 0.0, 0.0, 100000.0
        if not location_verified and not (source == 'file' and watermark_source == 'original'):
            return jsonify(error='只有保留原图水印的选图可以跳过位置检查。'), 422
        note = request.form.get('note', '').strip()[:1000]
        has_device_metadata = any(key in request.form for key in ('equipment_number', 'position_number', 'container_number', 'pump_fuse_numbers',
                                                                   'equipment_session', 'no_equipment_number'))
        equipment_number = request.form.get('equipment_number', '').strip()[:200]
        position_number = request.form.get('position_number', '').strip().upper()[:200]
        container_number = request.form.get('container_number', '').strip().upper()[:200]
        pump_fuse_numbers = request.form.get('pump_fuse_numbers', '').strip()[:200]
        equipment_session = request.form.get('equipment_session', '').strip()[:64]
        photo_type = request.form.get('photo_type', 'legacy').strip()
        watermark_at = watermark.isoformat()
        batch_id = request.form.get('batch_id', '').strip()[:64]
        if photo_type not in {'equipment', 'general', 'legacy'}:
            return jsonify(error='照片类型无效。'), 422
        no_equipment_number = request.form.get('no_equipment_number') == 'true'
        if has_device_metadata and ((not equipment_number and not no_equipment_number) or not equipment_session):
            return jsonify(error='请确认设备编号，或明确选择“此设备无编号”。'), 422
        if not has_device_metadata:
            equipment_session = 'legacy-pending-photo'
        technician_user_id = request.form.get('technician_user_id', str(g.user['id'])).strip()
        technician = None
        if technician_user_id.isdigit():
            technician = api['db']().execute('''
                select id, name from users where id = ? and is_active = 1
                and role in ('admin', 'manager', 'finance', 'employee', 'internal', 'user', 'external_employee')
            ''', (int(technician_user_id),)).fetchone()
        if not technician:
            return jsonify(error='请选择有效的施工员。'), 422
        technician_name = technician['name']
        location_note = request.form.get('location_note', '').strip()[:500]
        photo = request.files.get('photo')
        content = photo.read(40 * 1024 * 1024 + 1) if photo else b''
        if not content or len(content) > 40 * 1024 * 1024:
            return jsonify(error='照片为空或超过 40MB。'), 422
        digest = hashlib.sha256(content).hexdigest()
        if re.fullmatch(r'[0-9a-f]{32}', raw_key):
            key = raw_key
        else:
            # Repair legacy or partially restored PWA identifiers deterministically.
            # Including the content hash keeps different selected files distinct,
            # while the same retry resolves to the same id and remains idempotent.
            identity = '|'.join((str(g.user['id']), raw_key, str(order_id), captured.isoformat(),
                                 batch_id, photo.filename or '', digest))
            key = hashlib.sha256(identity.encode('utf-8')).hexdigest()[:32]
        try:
            with Image.open(BytesIO(content)) as image:
                if image.format not in {'JPEG', 'PNG', 'WEBP', 'HEIF', 'HEIC', 'AVIF'} or image.width * image.height > 40_000_000:
                    raise ValueError
                image.verify()
        except (OSError, ValueError, Image.DecompressionBombError):
            return jsonify(error='无法读取照片，请重新拍摄。'), 422
        db = api['db']()
        capture_date_source = 'camera'
        if source == 'file':
            previous = db.execute('select * from field_photos where user_id = ? and client_id = ?', (g.user['id'], key)).fetchone()
            if previous:
                if previous['content_hash'] != digest or previous['order_id'] != order_id:
                    return jsonify(error='照片编号已用于不同的内容。'), 409
                return jsonify(ok=True, id=previous['id'], duplicate=True, capture_date=previous['capture_date'])
            try:
                local, capture_date_source, candidates = resolve_capture_date(content, tz_name, request.form.get('manual_capture_date', ''))
            except (OSError, RuntimeError, subprocess.TimeoutExpired):
                return jsonify(error='照片日期识别暂时失败，请重试。'), 503
            except ValueError as error:
                return jsonify(error=str(error)), 422
            if local is None:
                return jsonify(error='请选择照片的拍摄日期。', needs_capture_date=True, date_candidates=candidates), 422
            captured = local
            watermark_at = local.isoformat()
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
                 equipment_number, position_number, container_number, pump_fuse_numbers, equipment_session, photo_type, watermark_at,
                 watermark_source, batch_id, technician_user_id, technician_name, location_verified, capture_date_source)
                values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                (key, order_id, g.user['id'], captured.astimezone(timezone.utc).isoformat(), api['now'](),
                 local.date().isoformat(), tz_name, lat, lng, accuracy, location_note, note, source,
                 relative.as_posix(), digest, target.stat().st_size, equipment_number, position_number, container_number, pump_fuse_numbers,
                 equipment_session, photo_type, watermark_at, watermark_source, batch_id, technician['id'], technician_name, int(location_verified), capture_date_source))
            api['log_action']('create', 'field_photo', cursor.lastrowid, order['order_number'], 'PWA 工单照片上传')
            db.commit()
        except Exception:
            db.rollback()
            raise
        return jsonify(ok=True, id=cursor.lastrowid, capture_date=local.date().isoformat(), capture_date_source=capture_date_source)

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
                image.load()
                enhanced = ImageOps.autocontrast(ImageOps.grayscale(image), cutoff=1)
                scale = min(3, max(1, math.ceil(2400 / max(enhanced.width, enhanced.height))))
                if scale > 1:
                    enhanced = enhanced.resize((enhanced.width * scale, enhanced.height * scale), Image.Resampling.LANCZOS)
                enhanced = ImageEnhance.Contrast(enhanced).enhance(1.5).filter(ImageFilter.SHARPEN).filter(ImageFilter.SHARPEN)
                processed = BytesIO()
                enhanced.save(processed, format='PNG')
            results = [
                subprocess.run(['tesseract', 'stdin', 'stdout', '--psm', psm, '-l', 'eng'], input=payload,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20, check=False)
                for psm, payload in [('11', content), ('6', processed.getvalue())]
            ]
        except (OSError, ValueError, Image.DecompressionBombError):
            return jsonify(error='无法读取铭牌照片。'), 422
        except subprocess.TimeoutExpired:
            return jsonify(error='铭牌识别超时，请重试或手工输入。'), 504
        if not any(result.returncode == 0 for result in results):
            return jsonify(error='服务器暂时无法识别铭牌，请手工输入。'), 503
        text = '\n'.join(result.stdout.decode('utf-8', errors='ignore') for result in results if result.returncode == 0)
        text = re.sub(r'(?<=\d)[ \t](?=\d)', '', text)
        labels = r'(?:mach[i1l]ne\s*n(?:umber|urnber|o\.?|#)|serial\s*(?:number|no\.?|#)|s\s*/?\s*n|生产编号|设备编号)'
        candidates = []
        for match in re.finditer(labels + r'\s*[:：#-]?\s*([A-Z0-9][A-Z0-9._/-]{3,})', text, re.I):
            value = match.group(1).strip('._/-')
            if re.fullmatch(r'\d{13}', value) and value not in candidates:
                candidates.append(value)
        if not candidates:
            candidates.extend(dict.fromkeys(re.findall(r'(?<![A-Z0-9_-])\d{13}(?![A-Z0-9_-])', text, re.I)))
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
        response = send_file(photo_file(row, request.args.get('thumb') == '1'), mimetype='image/jpeg',
                             as_attachment=request.args.get('download') == '1')
        response.headers['Cache-Control'] = 'no-store, private'
        return response

    @app.get('/reports/field-photos')
    @access
    def field_photo_query():
        rows = photo_rows()
        return render_template('field_photo_query.html', rows=rows[:2000], truncated=len(rows) > 2000,
                               photo_orders=photo_order_rows())

    @app.get('/api/field/photos.xlsx')
    @access
    def field_photo_export():
        rows = photo_rows()
        if len(rows) > 2000:
            return jsonify(error='结果超过 2000 条，请缩小日期或工单范围后导出。'), 422
        headers = ['工单', '客户', '站点', '铭牌号', '位置号', '集装箱号', '已更换水泵保险编号', '施工员', '实际拍摄账号', '拍摄日期',
                   '设备拍摄时间（UTC）', '水印时间', '水印来源', '归档时区', '上传时间', '纬度', '经度', '精度（米）',
                   '位置状态', '备注', '位置确认说明', '来源', '文件路径']
        keys = ['order_number', 'customer_name', 'site_name', 'equipment_number', 'position_number', 'container_number', 'pump_fuse_numbers',
                'technician_name', 'employee_name', 'capture_date', 'captured_at', 'watermark_at', 'watermark_source', 'timezone_name',
                'received_at', 'latitude', 'longitude', 'accuracy', 'location_verified', 'note', 'location_note', 'source', 'relative_path']
        export_rows = []
        for row in rows:
            values = [str(row[key] or '') for key in keys]
            source_index = keys.index('watermark_source')
            values[source_index] = '保留原图水印' if row['watermark_source'] == 'original' else '系统生成水印'
            location_index = keys.index('location_verified')
            values[location_index] = '已检查' if row['location_verified'] else '未检查'
            export_rows.append(values)
        buffer = api['build_simple_xlsx'](headers, export_rows, sheet_name='工单照片台账')
        return send_file(buffer, as_attachment=True, download_name='field-photos.xlsx', mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

    @app.get('/api/field/photos.zip')
    @access
    def field_photo_download_zip():
        rows = photo_rows()
        if len(rows) > 2000:
            return jsonify(error='结果超过 2000 张，请缩小日期或工单范围后下载。'), 422
        if not rows:
            return jsonify(error='没有符合条件的照片。'), 422
        # Validate every path before returning a ZIP; never silently omit a photo.
        paths = [(row, photo_file(row)) for row in rows]
        buffer = tempfile.SpooledTemporaryFile(max_size=16 * 1024 * 1024, mode='w+b')
        try:
            with zipfile.ZipFile(buffer, 'w', compression=zipfile.ZIP_STORED) as archive:
                for row, path in paths:
                    order = secure_filename(row['order_number']) or str(row['order_id'])
                    archive.write(path, f"{order}/{row['capture_date']}/{row['id']}-{Path(path).name}")
            buffer.seek(0)
            response = send_file(buffer, as_attachment=True, download_name='field-photos.zip', mimetype='application/zip')
            response.headers['Cache-Control'] = 'no-store, private'
            response.call_on_close(buffer.close)
            return response
        except Exception:
            buffer.close()
            raise

    def repair_table_rows():
        photos = [row for row in photo_rows() if row['photo_type'] == 'equipment']
        grouped = {}
        for photo in photos:
            technician = (photo['technician_name'] or photo['employee_name'] or '').strip()
            equipment_number = (photo['equipment_number'] or '').strip()
            position_number = (photo['position_number'] or '').strip()
            container_number = (photo['container_number'] or '').strip()
            if equipment_number:
                group_key = (
                    'device', photo['order_id'], photo['capture_date'], equipment_number.casefold(),
                    position_number.casefold(), container_number.casefold(), technician.casefold(),
                )
            else:
                group_key = ('session', photo['order_id'], photo['equipment_session'] or f"photo-{photo['id']}")
            entry = grouped.setdefault(group_key, dict(
                order_number=photo['order_number'], date=photo['capture_date'], position_number=position_number,
                container_number=container_number, equipment_number=equipment_number, pump_fuses=[],
                notes=[], technicians=[], photo_count=0, photo_ids=[], order_id=photo['order_id'],
            ))
            entry['photo_count'] += 1
            entry['photo_ids'].append(photo['id'])
            for fuse in re.split(r'[/,，;；\s]+', (photo['pump_fuse_numbers'] or '').strip()):
                if fuse and fuse.casefold() not in {item.casefold() for item in entry['pump_fuses']}:
                    entry['pump_fuses'].append(fuse)
            if photo['note'] and photo['note'] not in entry['notes']:
                entry['notes'].append(photo['note'])
            if technician and technician not in entry['technicians']:
                entry['technicians'].append(technician)
        rows = []
        for index, entry in enumerate(sorted(grouped.values(), key=lambda item: (item['date'], item['order_number'], item['position_number'])), 1):
            entry['sequence'] = index
            pump_fuses = entry.pop('pump_fuses')
            pump_fuses.sort(key=lambda value: (0, int(value)) if value.isdigit() else (1, value.casefold()))
            entry['pump_fuse_numbers'] = '/'.join(pump_fuses)
            entry['note'] = ' / '.join(entry.pop('notes'))
            entry['technician'] = ' / '.join(entry.pop('technicians'))
            rows.append(entry)
        return rows

    @app.get('/reports/field-repairs')
    @access
    def field_repair_report():
        rows = repair_table_rows()
        can_delete = api['has_action_permission']('service_reports', 'delete')
        allowed_orders = set()
        if can_delete:
            clauses, params = api['service_order_access_filters']()
            allowed_orders = {row['id'] for row in api['db']().execute(
                'select service_orders.id from service_orders where ' + (' and '.join(clauses) or '1=1'), params)}
        signer = URLSafeTimedSerializer(app.secret_key, salt='delete-repair-photos')
        for row in rows:
            row['can_delete'] = can_delete and row['order_id'] in allowed_orders
            if row['can_delete']:
                row['delete_token'] = signer.dumps({'user': g.user['id'], 'ids': row['photo_ids']})
        return render_template('field_repair_report.html', rows=rows, photo_orders=photo_order_rows(),
                               field_csrf=token(), can_delete_repairs=can_delete)

    @app.post('/api/field/repairs/delete')
    @access
    def delete_field_repair():
        check_write()
        if not api['has_action_permission']('service_reports', 'delete'):
            abort(403)
        signer = URLSafeTimedSerializer(app.secret_key, salt='delete-repair-photos')
        try:
            selection = signer.loads((request.get_json(silent=True) or {}).get('token', ''), max_age=7200)
        except (BadSignature, SignatureExpired, TypeError):
            return jsonify(error='删除确认已失效，请刷新清单后重试。'), 409
        if selection.get('user') != g.user['id']:
            abort(403)
        ids = selection.get('ids', [])
        if not ids or len(ids) > 2001 or any(type(value) is not int for value in ids):
            abort(400)
        db = api['db']()
        moved = []
        staging = None
        try:
            db.execute('begin immediate')
            rows = db.execute('select * from field_photos where id in (' + ','.join('?' for _ in ids) + ')', ids).fetchall()
            if not rows:
                db.rollback()
                return jsonify(ok=True, deleted=0)
            if len(rows) != len(ids) or any(row['photo_type'] != 'equipment' for row in rows):
                db.rollback()
                return jsonify(error='清单记录已变化，请刷新后重新确认。'), 409
            for order_id in {row['order_id'] for row in rows}:
                api['require_service_order'](order_id)
            root = api['shared_photos_root']().resolve()
            paths = []
            for row in rows:
                relative = Path(row['relative_path'])
                if relative.is_absolute() or len(relative.parts) != 4 or relative.parts[1] != 'pictures' or '..' in relative.parts:
                    abort(409, description='照片路径异常，未执行删除。')
                for relative_file in (relative, Path(relative.parts[0], 'thumbnails', *relative.parts[2:])):
                    path = root / relative_file
                    resolved = path.resolve()
                    if not resolved.is_relative_to(root) or any(parent.is_symlink() for parent in [path, *path.parents] if parent != root and parent.is_relative_to(root)):
                        abort(409, description='照片路径异常，未执行删除。')
                    if path.exists():
                        if not path.is_file():
                            abort(409, description='照片路径异常，未执行删除。')
                        paths.append(path)
            if paths:
                staging = Path(tempfile.mkdtemp(prefix='.repair-delete-', dir=root))
                for index, path in enumerate(dict.fromkeys(paths)):
                    staged = staging / str(index)
                    os.replace(path, staged)
                    moved.append((path, staged))
            db.execute('delete from field_photos where id in (' + ','.join('?' for _ in ids) + ')', ids)
            api['log_action']('delete', 'field_photo', ids[0], '设备维修清单',
                              '删除设备维修记录及照片，照片 ID：' + ','.join(map(str, ids)))
            db.commit()
        except Exception:
            db.rollback()
            for path, staged in reversed(moved):
                if staged.exists():
                    os.replace(staged, path)
            if staging is not None:
                staging.rmdir()
            raise
        cleanup_pending = False
        for _, staged in moved:
            try:
                staged.unlink(missing_ok=True)
            except OSError:
                cleanup_pending = True
                app.logger.exception('Repair photo deleted from register; staged file cleanup failed: %s', staged)
        if staging is not None and not cleanup_pending:
            staging.rmdir()
        return jsonify(ok=True, deleted=len(rows), cleanup_pending=cleanup_pending)

    @app.get('/api/field/repairs.xlsx')
    @access
    def field_repair_export():
        rows = repair_table_rows()
        headers = ['序号', '日期', '地标号', '集装箱号', '同飞铭牌', '已更换水泵保险编号', '备注', '维修人员', '工单', '照片数量']
        keys = ['sequence', 'date', 'position_number', 'container_number', 'equipment_number', 'pump_fuse_numbers',
                'note', 'technician', 'order_number', 'photo_count']
        buffer = api['build_simple_xlsx'](headers, [[str(row[key] or '') for key in keys] for row in rows], sheet_name='设备维修清单')
        return send_file(buffer, as_attachment=True, download_name='field-repairs.xlsx', mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

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
