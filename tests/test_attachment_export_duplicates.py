"""Archive duplicate-source regressions; no database access required."""
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

from invoice_tool.attachments.streaming_zip import stream_attachment_zip


class AttachmentExportDuplicatesTest(unittest.TestCase):
    def test_same_source_is_exported_once_across_categories_and_names(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'receipt.pdf'
            source.write_bytes(b'original receipt')
            files = [('photos', source, 'receipt.pdf'),
                     ('photos', root / '.' / 'receipt.pdf', 'receipt-copy.pdf'),
                     ('settlement', source, 'receipt.pdf')]
            with ZipFile(BytesIO(b''.join(stream_attachment_zip(['photos', 'settlement'], files)))) as archive:
                members = [name for name in archive.namelist() if not name.endswith('/')]
                self.assertEqual(members, ['photos/receipt.pdf'])
                self.assertEqual(archive.read(members[0]), b'original receipt')
                self.assertIsNone(archive.testzip())

    def test_distinct_files_with_same_name_are_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first, second = root / 'first.pdf', root / 'second.pdf'
            first.write_bytes(b'first')
            second.write_bytes(b'second')
            files = [('settlement', first, 'receipt.pdf'),
                     ('settlement', second, 'receipt.pdf')]
            with ZipFile(BytesIO(b''.join(stream_attachment_zip(['settlement'], files)))) as archive:
                self.assertEqual(archive.read('settlement/receipt.pdf'), b'first')
                self.assertEqual(archive.read('settlement/receipt-2.pdf'), b'second')


if __name__ == '__main__':
    unittest.main()
