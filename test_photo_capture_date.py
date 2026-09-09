import unittest
from io import BytesIO
from unittest.mock import patch
from PIL import Image
import photo_capture_date as dates
import test_field_work as field_tests

def photo(exif_date=None):
    im=Image.new('RGB',(500,400),'white')
    exif=Image.Exif()
    if exif_date:
        exif[36867]=exif_date
        exif[36881]='-05:00'
    out=BytesIO();im.save(out,'JPEG',exif=exif)
    return out.getvalue()

class PhotoDateTest(unittest.TestCase):
    def test_watermark_wins_over_conflicting_exif_and_manual(self):
        with patch.object(dates,'read_watermark',return_value='Time 2026-09-02 23:45:10 GMT-5'):
            value,source,_=dates.resolve_capture_date(photo('2026:08:01 11:00:00'),'UTC','2026-07-01')
        self.assertEqual(source,'watermark');self.assertEqual(value.isoformat(),'2026-09-02T23:45:10-05:00')

    def test_exif_fallback_preserves_local_date_and_offset(self):
        with patch.object(dates,'read_watermark',return_value=''):
            value,source,_=dates.resolve_capture_date(photo('2026:09:03 23:30:00'),'UTC')
        self.assertEqual(source,'exif');self.assertEqual(value.date().isoformat(),'2026-09-03')
        self.assertEqual(value.utcoffset().total_seconds(),-18000)

    def test_missing_ambiguous_and_invalid_dates_require_user(self):
        for text in ['', 'Time 2026-02-30 10:00', '09/08/2026 10:30']:
            with self.subTest(text=text),patch.object(dates,'read_watermark',return_value=text):
                value,source,candidates=dates.resolve_capture_date(photo(),'UTC')
                self.assertIsNone(value)
                value,source,_=dates.resolve_capture_date(photo(),'UTC','2026-09-06')
                self.assertEqual(source,'manual');self.assertEqual(value.date().isoformat(),'2026-09-06')

    def test_ambiguous_watermark_does_not_silently_use_exif(self):
        with patch.object(dates,'read_watermark',return_value='09/08/2026 10:30'):
            value,source,candidates=dates.resolve_capture_date(photo('2026:09:05 10:00:00'),'UTC')
        self.assertIsNone(value);self.assertEqual(candidates,['2026-08-09','2026-09-08'])

class ImportDateEndpointTest(unittest.TestCase):
    def setUp(self):
        self.f=field_tests.FieldWorkTest();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.f.photo=photo('2026:08:01 10:00:00')

    def upload(self,**kwargs):
        return self.f.upload(source='file',watermark_source='original',location_verified='false',**kwargs)

    def test_broken_multipart_reports_transport_error_without_writing(self):
        response = self.f.http.post('/api/field/photos', data=b'not a multipart body',
            content_type='multipart/form-data; boundary=missing',
            headers={'X-Field-Token':self.f.csrf})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json['code'], 'upload_body_invalid')
        with self.f.module.app.app_context():
            self.assertEqual(self.f.module.db().execute('select count(*) from field_photos').fetchone()[0], 0)

    def test_watermark_date_controls_ledger_and_directory(self):
        with patch.object(dates,'read_watermark',return_value='2026-09-02 23:45:10 GMT-5'):
            response=self.upload()
        self.assertEqual(response.status_code,200,response.text)
        with self.f.module.app.app_context():
            row=self.f.module.db().execute('select * from field_photos').fetchone()
            self.assertEqual(row['capture_date'],'2026-09-02')
            self.assertEqual(row['capture_date_source'],'watermark')
            self.assertIn('/pictures/2026-09-02/',row['relative_path'])
            self.assertTrue((self.f.root/row['relative_path']).exists())
            self.assertTrue(row['captured_at'].startswith('2026-09-03T04:45:10'))

    def test_exif_date_controls_directory(self):
        with patch.object(dates,'read_watermark',return_value=''):
            response=self.upload()
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json['capture_date'],'2026-08-01')
        self.assertEqual(response.json['capture_date_source'],'exif')

    def test_no_date_has_no_write_then_manual_retry_is_idempotent(self):
        self.f.photo=photo()
        key='a'*32
        with patch.object(dates,'read_watermark',return_value=''):
            response=self.upload(client_id=key)
            self.assertEqual(response.status_code,422);self.assertTrue(response.json['needs_capture_date'])
            with self.f.module.app.app_context():
                self.assertEqual(self.f.module.db().execute('select count(*) from field_photos').fetchone()[0],0)
            response=self.upload(client_id=key,manual_capture_date='2026-09-04')
            self.assertEqual(response.status_code,200,response.text)
            self.assertEqual(response.json['capture_date'],'2026-09-04')
            retry=self.upload(client_id=key,manual_capture_date='2026-09-05')
            self.assertTrue(retry.json['duplicate']);self.assertEqual(retry.json['capture_date'],'2026-09-04')

    def test_ocr_error_preserves_photo_for_retry(self):
        with patch.object(dates,'read_watermark',side_effect=RuntimeError('OCR failed')):
            self.assertEqual(self.upload().status_code,503)

    def test_camera_does_not_require_ocr(self):
        with patch.object(dates,'read_watermark',side_effect=AssertionError('must not OCR camera')):
            self.assertEqual(self.f.upload().status_code,200)

if __name__=='__main__':unittest.main()
