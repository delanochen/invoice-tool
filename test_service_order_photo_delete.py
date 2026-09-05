import tempfile
import unittest
from datetime import date
from pathlib import Path

import test_expense_on_behalf as fixture


class ServiceOrderPhotoDeleteTest(unittest.TestCase):
    def setUp(self):
        self.fixture = fixture.ExpenseOnBehalfTest()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()
        self.module = self.fixture.app
        self.http = self.fixture.http
        self.root = Path(self.fixture.temp.name) / 'shared'
        self.module.SHARED_PHOTOS_DIR = str(self.root)
        self.fixture.login('Manager')

    def test_nonempty_photo_folder_requires_confirmation_then_is_removed(self):
        folder = self.root / 'SO-DELEGATE' / 'pictures' / '2026-09-05'
        folder.mkdir(parents=True)
        (folder / 'photo.jpg').write_bytes(b'photo')
        response = self.http.post(f'/service-orders/{self.fixture.order}/delete')
        self.assertEqual(response.status_code, 302)
        self.assertTrue(folder.exists())
        with self.module.app.app_context():
            self.assertIsNotNone(self.module.db().execute('select id from service_orders where id=?', (self.fixture.order,)).fetchone())
        response = self.http.post(f'/service-orders/{self.fixture.order}/delete', data={'confirm_photo_cleanup':'yes'})
        self.assertEqual(response.status_code, 302)
        self.assertFalse((self.root / 'SO-DELEGATE').exists())
        with self.module.app.app_context():
            self.assertIsNone(self.module.db().execute('select id from service_orders where id=?', (self.fixture.order,)).fetchone())

    def test_next_number_reuses_first_gap_for_current_month(self):
        prefix = f'SO{date.today():%y%m}'
        with self.module.app.app_context():
            db = self.module.db()
            for suffix in ('001', '003'):
                db.execute('''insert into service_orders (order_number,client_name,site_address,client_order_number,
                           created_by,created_at) values (?,?,?,?,?,?)''',
                           (prefix + suffix, 'Site', 'Address', suffix, self.fixture.people['Manager'], self.module.now()))
            db.commit()
            self.assertEqual(self.module.next_service_order_number(), prefix + '002')


if __name__ == '__main__':
    unittest.main()
