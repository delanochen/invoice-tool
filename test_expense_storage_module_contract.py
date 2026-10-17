"""Attachment file integrity, line ownership and legacy behavior."""
import ast
import hashlib
import inspect
import io
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

from PIL import Image
from werkzeug.datastructures import FileStorage
import app as module
import test_expense_on_behalf as fixture_module

NAMES = ('expense_attachment_dir','expense_attachment_path','expense_attachment_fingerprints','save_expense_attachment','save_expense_uploads','get_expense_attachments','group_expense_attachments')


class ExpenseStorageContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source = subprocess.check_output(['git','show','2c718e4:app.py'],cwd=Path(__file__).parent,text=True,encoding='utf-8')
        nodes = [n for n in ast.parse(source).body if isinstance(n,ast.FunctionDef) and n.name in NAMES]
        cls.reference = dict(vars(module))
        exec(compile(ast.Module(body=nodes,type_ignores=[]),'baseline-expense-storage.py','exec'),cls.reference)

    def test_signatures_preserved(self):
        for name in NAMES:
            self.assertEqual(inspect.signature(getattr(module,name)),inspect.signature(self.reference[name]))

    def test_file_and_image_fingerprints_match_baseline(self):
        with tempfile.TemporaryDirectory() as folder:
            for index,payload in enumerate((b'',b'not an image',b'x'*(1024*1024+17))):
                path = Path(folder)/str(index)
                path.write_bytes(payload)
                self.assertEqual(module.expense_attachment_fingerprints(path),self.reference['expense_attachment_fingerprints'](path))
                self.assertEqual(module.expense_attachment_fingerprints(path),(hashlib.sha256(payload).hexdigest(),''))
            path = Path(folder)/'receipt.png'
            image = Image.new('RGB',(18,16))
            image.putdata([(255-x*10, y*10, 0) for y in range(16) for x in range(18)])
            image.save(path)
            current = module.expense_attachment_fingerprints(path)
            self.assertEqual(current,self.reference['expense_attachment_fingerprints'](path))
            self.assertEqual(len(current[1]),16)

    def test_legacy_unassigned_attachment_group_is_preserved(self):
        rows = [dict(id=1,expense_item_key=None),dict(id=2,expense_item_key=' keep '),dict(id=3,expense_item_key=''),dict(id=4,expense_item_key='keep')]
        actual = module.group_expense_attachments(rows)
        self.assertEqual(actual,self.reference['group_expense_attachments'](rows))
        self.assertEqual([r['id'] for r in actual['']],[1,3])
        self.assertEqual([r['id'] for r in actual['keep']],[2,4])

    def test_uploads_reject_generic_files_and_forward_item_keys(self):
        with module.app.test_request_context('/',method='POST',data={'item_attachments_line-a':(io.BytesIO(b'receipt'),'receipt.pdf')}):
            with patch.object(module,'uploaded_attachments_from_request',return_value=[]),patch.object(module,'save_expense_attachment') as save:
                module.save_expense_uploads(7,[{'line_key':'line-a'}])
                self.assertEqual(save.call_count,1)
                self.assertEqual(save.call_args.args[0],7)
                self.assertEqual(save.call_args.args[2],'line-a')
            with patch.object(module,'uploaded_attachments_from_request',return_value=[object()]),patch.object(module,'save_expense_attachment') as save:
                with self.assertRaisesRegex(ValueError,'附件请挂到对应明细行'):
                    module.save_expense_uploads(7,[{'line_key':'line-a'}])
                save.assert_not_called()

    def test_save_failure_and_disallowed_extension_do_not_insert(self):
        upload = Mock(filename='receipt.pdf')
        upload.save.side_effect = OSError('disk unavailable')
        with tempfile.TemporaryDirectory() as folder,patch.object(module,'expense_attachment_dir',return_value=folder),patch.object(module,'db') as database:
            with self.assertRaisesRegex(ValueError,'附件保存失败'):
                module.save_expense_attachment(1,upload,'line-a')
            upload.filename='receipt.exe'
            with self.assertRaisesRegex(ValueError,'附件只支持'):
                module.save_expense_attachment(1,upload,'line-a')
            database.assert_not_called()

    def test_uploaded_bytes_and_metadata_round_trip_in_test_database(self):
        fixture = fixture_module.ExpenseOnBehalfTest()
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        expense = fixture.create()
        app = fixture.app
        payload = b'%PDF-1.4\nsynthetic receipt only'
        with tempfile.TemporaryDirectory() as folder,patch.object(app,'EXPENSE_ATTACHMENTS_DIR',folder),app.app.test_request_context('/'):
            app.g.user={'id':fixture.people['Submitter']}
            upload = FileStorage(stream=io.BytesIO(payload),filename='receipt.pdf',content_type='application/pdf')
            attachment_id = app.save_expense_attachment(expense['id'],upload,'line-test')
            app.db().commit()
            rows = app.get_expense_attachments(expense['id'])
            self.assertEqual(len(rows),1)
            row = rows[0]
            self.assertEqual(row['id'],attachment_id)
            self.assertEqual(row['expense_item_key'],'line-test')
            self.assertEqual(row['file_sha256'],hashlib.sha256(payload).hexdigest())
            self.assertEqual(row['uploaded_by'],fixture.people['Submitter'])
            self.assertEqual(Path(app.expense_attachment_path(row)).read_bytes(),payload)
            self.assertEqual(app.group_expense_attachments(rows)['line-test'][0]['id'],attachment_id)
