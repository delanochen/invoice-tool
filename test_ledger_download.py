import unittest
from io import BytesIO
from zipfile import ZipFile
from test_field_work import FieldWorkTest


class LedgerDownloadTest(unittest.TestCase):
    def setUp(self):
        self.f = FieldWorkTest()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.assertEqual(self.f.upload(equipment_number='M-101', position_number='P-7', container_number='C-8').status_code, 200)
        self.assertEqual(self.f.upload(equipment_number='M-202', position_number='P-7', container_number='C-9').status_code, 200)

    def test_filters_and_zip_use_identical_rows(self):
        filters = dict(equipment_number='101', position_number='p-7', container_number='C-8')
        rows = self.f.http.get('/api/field/photos', query_string=filters).json['rows']
        self.assertEqual(len(rows), 1)
        response = self.f.http.get('/api/field/photos.zip', query_string=filters)
        self.assertEqual(response.status_code, 200)
        with ZipFile(BytesIO(response.data)) as archive:
            self.assertEqual(len(archive.namelist()), 1)
            name = archive.namelist()[0]
            self.assertIn('/'+str(rows[0]['id'])+'-', name)
            self.assertGreater(len(archive.read(name)), 0)
        response.close()
        page = self.f.http.get('/reports/field-photos', query_string=filters)
        self.assertEqual(page.status_code, 200)
        for field in filters:
            self.assertIn(f'name="{field}"', page.text)
        self.assertEqual(self.f.http.get('/api/field/photos', query_string=dict(container_number='C-9', equipment_number='101')).json['rows'], [])

    def test_empty_zip_and_unauthenticated_access(self):
        self.assertEqual(self.f.http.get('/api/field/photos.zip?equipment_number=no-match').status_code, 422)
        with self.f.http.session_transaction() as session:
            session.clear()
        self.assertEqual(self.f.http.get('/api/field/photos.zip').status_code, 401)

    def test_repair_filters_match_export_and_technician_not_uploader(self):
        from openpyxl import load_workbook
        with self.f.module.app.app_context():
            db = self.f.module.db()
            db.execute("update field_photos set photo_type='equipment', technician_name='Repair Alice' where equipment_number='M-101'")
            db.execute("update field_photos set photo_type='equipment', technician_name='Repair Bob' where equipment_number='M-202'")
            db.commit()
        filters = dict(order_number='so-deleg', technician='alice', equipment_number='101', position_number='P-7', container_number='C-8')
        response = self.f.http.get('/reports/field-repairs', query_string=filters)
        self.assertEqual(response.status_code, 200)
        for field in filters:
            self.assertIn(f'name="{field}"', response.text)
        self.assertIn('Repair Alice', response.text)
        self.assertNotIn('Repair Bob', response.text)
        export = self.f.http.get('/api/field/repairs.xlsx', query_string=filters)
        self.assertEqual(export.status_code, 200)
        workbook = load_workbook(BytesIO(export.data), read_only=True)
        rows = list(workbook.active.values)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1][4], 'M-101')
        self.assertEqual(rows[1][7], 'Repair Alice')
        workbook.close()
        no_order = self.f.http.get('/reports/field-repairs', query_string={**filters, 'order_number': 'NO-SUCH-ORDER'})
        self.assertIn('没有符合条件的设备照片。', no_order.text)
        filters['technician'] = 'Bob'
        empty = self.f.http.get('/reports/field-repairs', query_string=filters)
        self.assertIn('没有符合条件的设备照片。', empty.text)
