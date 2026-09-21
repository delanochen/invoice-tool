"""Exercise deployment backend selection with shell stubs, never real Docker."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


@unittest.skipUnless(shutil.which('sh'), 'POSIX shell required')
class DeploymentBackendTests(unittest.TestCase):
    def run_shell(self, configured, running, action, fail_runtime=False, fail_build=False):
        source = (Path(__file__).parent / 'scripts/debian-auto-deploy.sh').read_text()
        functions, marker, _ = source.partition('\ntrap cleanup EXIT INT TERM\n\nif ! mkdir')
        self.assertTrue(marker, 'Cannot separate functions from production entrypoint')
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'deploy').mkdir()
            (root / 'deploy/docker-compose.postgresql.yml').touch()
            (root / 'VERSION').write_text('0.1.260')
            trace = root / 'trace'
            stubs = '''
docker() {
  printf 'docker %s\\n' "$*" >> "$TRACE"
  if [ "$1" = exec ]; then printf '%s\\n' "$RUNNING"; return 0; fi
  if [ "$1" = compose ] && [ "$FAIL_BUILD" = 1 ]; then return 7; fi
}
python3() {
  printf 'python %s\\n' "$*" >> "$TRACE"
  [ "$FAIL_RUNTIME" = 0 ]
}
'''
            env = dict(os.environ, INVOICE_TOOL_DIR=folder,
                       INVOICE_TOOL_BACKUP_DIR=folder, INVOICE_DATABASE_BACKEND=configured,
                       POSTGRES_DATABASE='invoice', RUNNING=running, TRACE=str(trace),
                       FAIL_RUNTIME=str(int(fail_runtime)), FAIL_BUILD=str(int(fail_build)))
            result = subprocess.run(['sh', '-c', functions + stubs + '\n' + action],
                                    cwd=folder, env=env, capture_output=True, text=True)
            return result, trace.read_text() if trace.exists() else ''

    def test_sqlite_keeps_default_compose(self):
        result, trace = self.run_shell('sqlite', 'sqlite', 'configure_database_backend && build_current_version')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('docker compose up -d --build', trace)
        self.assertNotIn('check_postgresql_runtime', trace)

    def test_postgres_requires_overlay_and_runtime_check(self):
        result, trace = self.run_shell('postgresql', 'postgresql', 'configure_database_backend && build_current_version')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('check_postgresql_runtime.py --database invoice', trace)
        self.assertIn('/deploy/docker-compose.postgresql.yml up -d --build', trace)

    def test_backend_mismatch_stops_before_build(self):
        for configured, running in [('sqlite', 'postgresql'), ('postgresql', 'sqlite')]:
            result, trace = self.run_shell(configured, running, 'configure_database_backend && build_current_version')
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn('docker compose', trace)

    def test_invalid_runtime_stops_before_build(self):
        result, trace = self.run_shell('postgresql', 'postgresql', 'configure_database_backend && build_current_version', fail_runtime=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('docker compose', trace)

    def test_build_failure_propagates(self):
        result, _ = self.run_shell('postgresql', 'postgresql', 'configure_database_backend && build_current_version', fail_build=True)
        self.assertNotEqual(result.returncode, 0)

    def test_postgres_backup_uses_pg_dump_helper(self):
        result, trace = self.run_shell('postgresql', 'postgresql', 'configure_database_backend && backup_database test-stamp')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('backup_postgresql.py --container invoice-tool-postgres --database invoice', trace)
        self.assertIn('invoices-test-stamp.dump', trace)
        self.assertNotIn('compose exec', trace)
