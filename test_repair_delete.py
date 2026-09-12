import re
import unittest
from pathlib import Path
from unittest.mock import patch

from test_field_work import FieldWorkTest


class RepairDeleteTest(unittest.TestCase):
    def setUp(self):
        self.f = FieldWorkTest()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.http = self.f.http
        self.module = self.f.module
        self.ids = [self.f.upload(photo_type='equipment', equipment_number='DEVICE-A', note=note).json['id']
                    for note in ('before', 'after')]
        self.other = self.f.upload(photo_type='equipment', equipment_number='DEVICE-B').json['id']
        self.f.fixture.login('Manager')

    def token(self):
        page = self.http.get('/reports/field-repairs?equipment_number=DEVICE-A')
        self.assertEqual(page.status_code, 200)
        return re.search(r'data-repair-delete="([^"]+)"', page.text).group(1)

    def delete(self, token, csrf=None):
        return self.http.post('/api/field/repairs/delete', json={'token': token},
                              headers={'X-Field-Token': csrf or self.f.csrf})

    def rows(self):
        with self.module.app.app_context():
            return self.module.db().execute('select * from field_photos order by id').fetchall()

    def paths(self, row):
        relative = Path(row['relative_path'])
        return [self.f.root / relative, self.f.root / relative.parts[0] / 'thumbnails' / Path(*relative.parts[2:])]

    def test_delete_exact_group_and_files_then_retry(self):
        rows = self.rows()
        token = self.token()
        response = self.delete(token)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json['deleted'], 2)
        self.assertEqual([row['id'] for row in self.rows()], [self.other])
        for row in rows:
            for path in self.paths(row):
                self.assertEqual(path.exists(), row['id'] == self.other)
        self.assertEqual(self.delete(token).json['deleted'], 0)
        listed = self.http.get('/api/field/photos').json['rows']
        self.assertEqual([row['id'] for row in listed], [self.other])
        self.assertEqual(self.http.get(f'/field/photos/{self.ids[0]}').status_code, 404)

    def test_permission_csrf_and_signed_selection(self):
        token = self.token()
        self.assertEqual(self.delete(token, 'wrong').status_code, 403)
        self.assertEqual(self.delete(token + 'tampered').status_code, 409)
        self.f.fixture.login('Submitter')
        self.assertNotIn('data-repair-delete=', self.http.get('/reports/field-repairs').text)
        self.assertEqual(self.delete(token).status_code, 403)
        self.assertEqual(len(self.rows()), 3)

    def test_database_error_restores_photos_and_records(self):
        rows = self.rows()
        with patch.dict(self.module.__dict__, {'log_action': unittest.mock.Mock(side_effect=ValueError('test rollback'))}):
            with self.assertRaisesRegex(ValueError, 'test rollback'):
                self.delete(self.token())
        self.assertEqual(len(self.rows()), 3)
        self.assertTrue(all(path.exists() for row in rows for path in self.paths(row)))

    def test_missing_file_does_not_prevent_stale_record_cleanup(self):
        row = self.rows()[0]
        self.paths(row)[0].unlink()
        self.assertEqual(self.delete(self.token()).status_code, 200)
        self.assertEqual(len(self.rows()), 1)

    def test_path_escape_rejected_without_deleting_records(self):
        token = self.token()
        with self.module.app.app_context():
            self.module.db().execute('update field_photos set relative_path=? where id=?', ('../pictures/date/escape.jpg', self.ids[0]))
            self.module.db().commit()
        self.assertEqual(self.delete(token).status_code, 409)
        self.assertEqual(len(self.rows()), 3)


if __name__ == '__main__':
    unittest.main()
