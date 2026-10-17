"""Real temporary-file regression checks for report storage extraction."""
from contextlib import contextmanager
import io
from pathlib import Path
import unittest
from unittest.mock import patch

from flask import g
from PIL import Image
from werkzeug.datastructures import FileStorage
import test_report_read_module_contract as fixtures


class ReportStorageModuleContractTest(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ReportReadModuleContractTest()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()
        self.module,self.report_id = self.fixture.module,self.fixture.report_id
        self.temp = Path(self.fixture.fixture.temp.name)

    @contextmanager
    def context(self):
        with self.module.app.test_request_context(method='POST'):
            connection = self.module.db()
            g.user = connection.execute('select * from users where id=?',(self.fixture.fixture.people['Manager'],)).fetchone()
            yield connection

    def image(self,name='photo.png',color='green'):
        stream = io.BytesIO()
        Image.new('RGB',(16,16),color).save(stream,format='PNG')
        stream.seek(0)
        return FileStorage(stream=stream,filename=name,content_type='image/png')

    def test_compressed_image_hash_dedup_and_category_scope(self):
        with self.context() as connection:
            self.assertTrue(self.module.save_report_attachment(self.report_id,self.image(),'site'))
            self.assertFalse(self.module.save_report_attachment(self.report_id,self.image('renamed.png'),'site'))
            self.assertTrue(self.module.save_report_attachment(self.report_id,self.image('renamed.png'),'arrival'))
            rows = connection.execute('select * from service_report_attachments where report_id=?',(self.report_id,)).fetchall()
            self.assertEqual(len(rows),2)
            for row in rows:
                with Image.open(self.module.report_attachment_path(row)) as image:
                    self.assertEqual(image.format,'JPEG')
            self.assertFalse(list(Path(self.module.REPORT_ATTACHMENTS_DIR).rglob('*.tmp')))

    def test_file_upload_dedup_and_missing_file_name_fallback(self):
        with self.context() as connection:
            first = FileStorage(stream=io.BytesIO(b'%PDF-1.7\nTest'),filename='Proof.pdf',content_type='application/pdf')
            self.assertTrue(self.module.save_report_file_attachment(self.report_id,first,'mileage_proof'))
            duplicate = FileStorage(stream=io.BytesIO(b'%PDF-1.7\nTest'),filename='renamed.pdf',content_type='application/pdf')
            self.assertFalse(self.module.save_report_file_attachment(self.report_id,duplicate,'mileage_proof'))
            row = connection.execute('select * from service_report_attachments where report_id=?',(self.report_id,)).fetchone()
            Path(self.module.report_attachment_path(row)).unlink()
            self.assertTrue(self.module.is_duplicate_report_photo(self.report_id,'mileage_proof','Proof (1).pdf','different-hash'))
            self.assertEqual(connection.execute('select count(*) from service_report_attachments').fetchone()[0],1)
            self.assertFalse(list(Path(self.module.REPORT_ATTACHMENTS_DIR).rglob('*.tmp')))

    def test_relocation_moves_legacy_file_and_updates_database(self):
        aid,old_path = self.fixture.attachment()
        with self.context() as connection:
            connection.execute("update service_reports set report_date='2026-10-19' where id=?",(self.report_id,))
            self.module.relocate_report_attachments(self.report_id)
            row = connection.execute('select * from service_report_attachments where id=?',(aid,)).fetchone()
            target = Path(self.module.report_attachment_path(row))
            self.assertTrue(target.is_file())
            self.assertEqual(target.read_bytes(),b'%PDF-1.7\nTest')
            self.assertFalse(old_path.exists())
            self.assertIn('20261019',row['stored_filename'])
            self.module.relocate_report_attachments(self.report_id)
            self.assertTrue(target.is_file())

    def test_generated_evidence_replaces_same_record_and_removes_old_file(self):
        source = self.temp/'evidence.png'
        Image.new('RGB',(4,4),'blue').save(source)
        with self.context() as connection:
            first = self.module.save_generated_report_attachment(self.report_id,str(source),'first.png')
            row = connection.execute('select * from service_report_attachments where id=?',(first,)).fetchone()
            old_path = Path(self.module.report_attachment_path(row))
            second = self.module.save_generated_report_attachment(self.report_id,str(source),'second.png',replace_attachment_id=first)
            self.assertEqual(first,second)
            self.assertFalse(old_path.exists())
            row = connection.execute('select * from service_report_attachments where id=?',(first,)).fetchone()
            self.assertTrue(Path(self.module.report_attachment_path(row)).is_file())
            self.assertEqual(row['original_filename'],'second.png')
            self.assertEqual(connection.execute('select count(*) from service_report_attachments').fetchone()[0],1)

    def test_shared_processed_photo_dedup_keeps_source(self):
        shared = self.temp/'shared'
        photo = shared/'SO-DELEGATE/pictures/day/photo.jpg'
        photo.parent.mkdir(parents=True)
        Image.new('RGB',(4,4),'blue').save(photo)
        with patch.object(self.module,'SHARED_PHOTOS_DIR',str(shared)):
            with self.context() as connection:
                self.assertTrue(self.module.save_shared_report_photo(self.report_id,'SO-DELEGATE/pictures/day/photo.jpg','site'))
                self.assertFalse(self.module.save_shared_report_photo(self.report_id,'SO-DELEGATE/pictures/day/photo.jpg','site'))
                self.assertTrue(photo.is_file())
                self.assertEqual(connection.execute('select count(*) from service_report_attachments').fetchone()[0],1)

    def test_explicit_context_avoids_requery_and_runtime_compressor_is_used(self):
        original = self.module.compress_report_image
        with self.context():
            with patch.object(self.module,'report_storage_context',side_effect=AssertionError('Unexpected requery')):
                with patch.object(self.module,'compress_report_image',wraps=original) as compressor:
                    self.assertTrue(self.module.save_report_attachment(self.report_id,self.image(),'site',storage_context=('SO-CONTEXT','20261017')))
                    compressor.assert_called_once()

    def test_upload_oserror_is_translated_to_validation_error(self):
        with self.context():
            with patch.object(self.module,'uploaded_report_files',side_effect=OSError('simulated')):
                with self.assertRaisesRegex(ValueError,'日报照片保存失败'):
                    self.module.save_report_uploads(self.report_id)

    def test_path_basename_and_prune_stay_within_current_root(self):
        outside = self.temp/'outside/keep'
        outside.mkdir(parents=True)
        with self.context():
            relative = self.module.report_attachment_relative_path(self.report_id,'site','../safe.jpg')
            self.assertNotIn('..',Path(relative).parts)
            self.module.prune_empty_report_folders(str(outside/'missing.jpg'))
        self.assertTrue(outside.exists())
        self.assertTrue(Path(self.module.REPORT_ATTACHMENTS_DIR).parent.exists())
