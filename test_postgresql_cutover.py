import hashlib
from pathlib import Path
import tempfile
import unittest

from scripts.migrate_postgresql import migrate
from scripts.migrate_postgresql_cutover import validate


class FrozenSnapshotGuards(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.source = Path(self.folder.name) / 'snapshot.sqlite3'
        self.source.write_bytes(b'synthetic validation fixture')
        self.sha = hashlib.sha256(self.source.read_bytes()).hexdigest()
        self.url = 'postgresql://invoice_owner:synthetic@postgres/invoice'

    def check(self, **changes):
        values = dict(source=self.source, source_sha=self.sha, url=self.url,
                      target_host='postgres', target_name='invoice',
                      confirm_target='invoice', writes_frozen=True)
        values.update(changes)
        return validate(**values)

    def test_explicit_inputs_pass_preflight(self):
        self.assertEqual(self.check(), self.source.resolve())

    def test_wrong_targets_and_missing_freeze_are_rejected(self):
        for change in [dict(target_host='other'), dict(confirm_target='other'),
                       dict(writes_frozen=False), dict(source_sha='0' * 64),
                       dict(url=self.url+'?host=other'),
                       dict(url=self.url.replace('invoice_owner', 'invoice_app'))]:
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.check(**change)

    def test_source_change_and_active_wal_are_rejected(self):
        self.source.write_bytes(b'changed')
        with self.assertRaises(ValueError):
            self.check()
        self.source.write_bytes(b'synthetic validation fixture')
        Path(str(self.source)+'-wal').write_bytes(b'active')
        with self.assertRaises(ValueError):
            self.check()

    def test_original_rehearsal_entry_still_refuses_production(self):
        with self.assertRaisesRegex(ValueError, 'limited to'):
            migrate(self.source, self.url, 'invoice')
