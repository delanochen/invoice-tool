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
