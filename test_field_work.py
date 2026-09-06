import hashlib
import tempfile
import unittest
import uuid
from unittest.mock import patch
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from PIL import Image
import test_expense_on_behalf as fixture
import photo_worker
import field_work


class FieldWorkTest(unittest.TestCase):
    def setUp(self):
        self.fixture = fixture.ExpenseOnBehalfTest()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()
        self.module = self.fixture.app
        self.http = self.fixture.http
        self.root = Path(self.fixture.temp.name) / 'shared'
        self.module.SHARED_PHOTOS_DIR = str(self.root)
        self.module.app.static_folder = str(fixture.ROOT / 'static')
        self.csrf = self.http.get('/api/field/session').json['csrf']
        self.capture_time = (datetime.now(timezone.utc) - timedelta(days=2)).replace(hour=23,minute=30,second=0,microsecond=0)
        photo = BytesIO()
        Image.new('RGB', (2600, 1950), 'green').save(photo, 'JPEG')
        self.photo = photo.getvalue()

    def upload(self, **overrides):
        data = dict(client_id=uuid.uuid4().hex, order_id=str(self.fixture.order),
                    user_id=str(self.fixture.people['Submitter']), captured_at=self.capture_time.isoformat(),
                    timezone_name='Pacific/Kiritimati', latitude='52.1', longitude='4.3', accuracy='8',
                    source='camera', note='Equipment check', photo=(BytesIO(self.photo), 'photo.jpg'))
        data.update(equipment_number='BESB-2B6-1', position_number='B6-1', equipment_session=uuid.uuid4().hex,
                    no_equipment_number='false')
        data.update(overrides)
        return self.http.post('/api/field/photos', data=data, headers={'X-Field-Token':self.csrf})

    def test_offline_capture_date_compression_and_worker_stable_filename(self):
        response = self.upload()
        self.assertEqual(response.status_code, 200, response.text)
        with self.module.app.app_context():
            row = self.module.db().execute('select * from field_photos').fetchone()
        expected_date = (self.capture_time + timedelta(hours=14)).date().isoformat()
        self.assertEqual(row['capture_date'], expected_date)
        path = self.root / row['relative_path']
        self.assertEqual(path.parent.name, expected_date)
        self.assertIn(f"-u{self.fixture.people['Submitter']}-", path.name)
        with Image.open(path) as photo:
            self.assertEqual(photo.format,'JPEG')
            self.assertLessEqual(max(photo.size),1800)
        old_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        self.assertEqual(photo_worker.rename_existing_pictures_by_datetime(self.root / 'SO-DELEGATE'),0)
        self.assertEqual(photo_worker.clean_duplicate_pictures(self.root / 'SO-DELEGATE'),0)
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),old_hash)

    def test_retry_is_idempotent_and_collision_is_rejected(self):
        key = uuid.uuid4().hex
        first = self.upload(client_id=key)
        retry = self.upload(client_id=key)
        self.assertTrue(retry.json['duplicate'])
        self.assertEqual(first.json['id'],retry.json['id'])
        self.photo = self.photo + b'extra'
        self.assertEqual(self.upload(client_id=key).status_code,409)
        with self.module.app.app_context():
            self.assertEqual(self.module.db().execute('select count(*) from field_photos').fetchone()[0],1)

    def test_uploaded_photo_immediately_available_in_nas_date_folder(self):
        self.assertEqual(self.upload().status_code, 200)
        day = (self.capture_time + timedelta(hours=14)).date().isoformat()
        response = self.http.get('/shared-photos/browse', query_string={'path':'SO-DELEGATE', 'day':day})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json['images']), 1)
        self.assertEqual(response.json['status']['completed'], 1)
        self.assertIn('/pictures/' + day + '/', response.json['images'][0]['path'])
        with self.http.get(response.json['images'][0]['thumbnail']) as thumbnail:
            self.assertEqual(thumbnail.status_code, 200)

    def test_invalid_coordinates_time_image_and_owner_rejected(self):
        for changes in ({'latitude':'nan'}, {'accuracy':'-1'}, {'longitude':'inf'},
                        {'timezone_name':'Unknown/Zone'}, {'captured_at':'2026-09-01'},
                        {'captured_at':(datetime.now(timezone.utc)+timedelta(days=2)).isoformat()},
                        {'photo':(BytesIO(b'not an image'),'fake.jpg')}):
            with self.subTest(changes=changes):
                self.assertEqual(self.upload(**changes).status_code,422)
        self.assertEqual(self.upload(user_id=str(self.fixture.people['Beneficiary'])).status_code,409)
        self.assertEqual(self.upload(equipment_number='', no_equipment_number='false').status_code,422)

    def test_device_session_metadata_is_saved_and_empty_number_is_explicit(self):
        session_id = uuid.uuid4().hex
        self.assertEqual(self.upload(equipment_number='INV-42', position_number='TOP', container_number='LYGU0217133',
                                     pump_fuse_numbers='1/2/3/4/5',
                                     technician_user_id=str(self.fixture.people['Beneficiary']),
                                     equipment_session=session_id).status_code, 200)
        self.assertEqual(self.upload(equipment_number='', position_number='LEFT', equipment_session=session_id, no_equipment_number='true').status_code, 200)
        with self.module.app.app_context():
            rows = self.module.db().execute('''select equipment_number, position_number, container_number, pump_fuse_numbers,
                                              equipment_session, technician_user_id, technician_name, user_id
                                       from field_photos order by id''').fetchall()
        self.assertEqual(tuple(rows[0]), ('INV-42', 'TOP', 'LYGU0217133', '1/2/3/4/5', session_id,
                                         self.fixture.people['Beneficiary'], 'Beneficiary', self.fixture.people['Submitter']))
        self.assertEqual(tuple(rows[1]), ('', 'LEFT', '', '', session_id,
                                         self.fixture.people['Submitter'], 'Submitter', self.fixture.people['Submitter']))

    def test_session_lists_technicians_and_invalid_technician_is_rejected(self):
        session = self.http.get('/api/field/session').json
        self.assertIn({'id': self.fixture.people['Submitter'], 'name': 'Submitter'}, session['technicians'])
        self.assertIn({'id': self.fixture.people['Beneficiary'], 'name': 'Beneficiary'}, session['technicians'])
        response = self.upload(technician_user_id='999999')
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json['error'], '请选择有效的施工员。')

    def test_nameplate_recognition_prefers_labeled_machine_number(self):
        completed = type('Result', (), {'returncode':0, 'stdout':b'Machine Type LCI-400CR-01AZ-3487U\nManufacture Year 2024\nMachine Number 1023231239085\n'})()
        with patch.object(field_work.subprocess, 'run', return_value=completed):
            response = self.http.post('/api/field/recognize-equipment',
                                      data={'photo':(BytesIO(self.photo),'plate.jpg')},
                                      headers={'X-Field-Token':self.csrf})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json['candidates'], ['1023231239085'])

    def test_watermark_password_and_adjusted_time_metadata(self):
        with self.module.app.app_context():
            self.module.set_setting('field_watermark_time_password', 'plain-test-password')
            self.module.db().commit()
        wrong = self.http.post('/api/field/verify-watermark-password', json={'password':'wrong'}, headers={'X-Field-Token':self.csrf})
        right = self.http.post('/api/field/verify-watermark-password', json={'password':'plain-test-password'}, headers={'X-Field-Token':self.csrf})
        self.assertEqual(wrong.status_code, 403)
        self.assertEqual(right.status_code, 200)
        adjusted = '2010-05-06T07:08:09+00:00'
        response = self.upload(photo_type='equipment', watermark_at=adjusted, batch_id=uuid.uuid4().hex)
        self.assertEqual(response.status_code, 200, response.text)
        with self.module.app.app_context():
            row = self.module.db().execute('select photo_type, watermark_at, batch_id from field_photos').fetchone()
        self.assertEqual(row['photo_type'], 'equipment')
        self.assertEqual(row['watermark_at'], adjusted)
        self.assertTrue(row['batch_id'])

    def test_csrf_login_and_permission_required(self):
        self.csrf = 'invalid'
        self.assertEqual(self.upload().status_code,403)
        with self.http.session_transaction() as session:
            session.clear()
        self.assertEqual(self.http.get('/api/field/session').status_code,401)
        self.assertEqual(self.upload().status_code,401)

    def test_photo_visibility_respects_owner_and_order_access(self):
        photo_id = self.upload().json['id']
        self.fixture.login('Beneficiary')
        self.assertEqual(self.http.get('/api/field/photos').json['rows'],[])
        self.assertEqual(self.http.get(f'/field/photos/{photo_id}').status_code,403)
        self.fixture.login('External')
        self.assertEqual(self.http.get('/api/field/session').json['orders'],[])
        self.assertEqual(self.http.get(f'/field/photos/{photo_id}').status_code,403)
        self.fixture.login('Manager')
        listing = self.http.get('/api/field/photos')
        self.assertEqual(len(listing.json['rows']),1)
        self.assertIn('no-store',listing.headers['Cache-Control'])
        with self.http.get(f'/field/photos/{photo_id}?thumb=1') as response:
            self.assertEqual(response.status_code,200)
            self.assertIn('no-store',response.headers['Cache-Control'])
        self.assertEqual(self.http.get('/reports/field-photos').status_code,200)
        self.assertEqual(self.http.get('/api/field/photos?q=unknown').json['rows'],[])
        self.assertEqual(self.http.get('/api/field/photos.xlsx?q=Equipment').status_code,200)

    def test_repair_register_groups_device_photos_and_exports_manual_fields(self):
        session_id = uuid.uuid4().hex
        for note in ('Before repair', 'After repair'):
            response = self.upload(equipment_number='10232502W0738', position_number='4A1-4',
                                   container_number='LYGU0217133', pump_fuse_numbers='1/2/3/4/5',
                                   equipment_session=session_id, batch_id='batch-one', photo_type='equipment', note=note)
            self.assertEqual(response.status_code, 200, response.text)
        self.fixture.login('Manager')
        page = self.http.get('/reports/field-repairs')
        self.assertEqual(page.status_code, 200)
        body = page.get_data(as_text=True)
        for value in ('10232502W0738', '4A1-4', 'LYGU0217133', '1/2/3/4/5', '2 张'):
            self.assertIn(value, body)
        export = self.http.get('/api/field/repairs.xlsx')
        self.assertEqual(export.status_code, 200)
        self.assertIn('spreadsheetml', export.content_type)

    def test_public_shell_has_no_employee_data_and_manifest_exists(self):
        with self.http.session_transaction() as session:
            session.clear()
        response = self.http.get('/field/')
        self.assertEqual(response.status_code,200)
        self.assertNotIn('Submitter',response.text)
        self.assertNotIn('SO-DELEGATE',response.text)
        self.assertEqual(self.http.get('/field/manifest.webmanifest').json['scope'],'/field/')
        with self.http.get('/field/sw.js') as response:
            self.assertEqual(response.status_code,200)

    def test_system_photo_picker_opens_before_async_location_work(self):
        script = (fixture.ROOT / 'static' / 'field-work.js').read_text(encoding='utf-8')
        picker_click = script.index("$('photoFile').click();")
        deferred_context = script.index("await makeContext('file', selection)")
        self.assertLess(picker_click, deferred_context)
        handler = script[script.index("$('fileCapture').addEventListener"):picker_click]
        self.assertNotIn('await ', handler)


if __name__ == '__main__':
    unittest.main()
