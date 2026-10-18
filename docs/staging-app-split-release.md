# app.py split: staging integration and test release

Date: 2026-10-18. Target: staging, 192.168.7.202, /opt/invoice-tool-test, invoice-tool-test. Production is outside this release.

Merged extraction head c6bfd376a3792d1687ed74180a4dd8f30036504e with staging head b8d7ccacdad0d853aaf6c73473628b4ba4786bef. Retained staging settlement amount adjustments, revision snapshots, current-version filtering, source freeze, same-series handling, and PDF archives in extracted domain modules. Root app.py remains assembly and compatibility forwarding.

Regression exposed three extracted helpers passing the domain module namespace to settlement-review APIs. Explicit callbacks now supply the live application namespace, preserving original behavior and monkeypatch compatibility. New route resolvers similarly preserve the root API passed to settlement_versions.

Validation:
- Targeted integration: 93 passed, 887 subtests passed in 44.71s.
- Broad vetted runtime set in the merged checkout: 245 passed, 4 deselected, 914 subtests passed in 133.40s. Four previously documented fixture exclusions remain; this is not the entire repository suite.
- Independent AST parity: 12 staging business function bodies preserve b8d7cca behavior after reversing dependency adapters; superseded status catalog checked. Included in broad run.
- Assembly boundary checked in both runs. Earlier extraction-stage contracts retain historical snapshots; broad merged runtime validation uses the new staging parity contract instead of those historical contracts.
- Initial Windows GBK subprocess decoding failures were resolved by UTF-8 interpreter startup. Test runners re-execute with -X utf8 when necessary.
- All database access used guarded invoice_test connections. External email/AI/network behavior uses existing vetted mocks.

Release procedure: temporarily hold the existing test deployment timer, push staging, verify exact remote commit, verify unchanged schema/dependency deployment files, back up invoice_test, fast-forward the test checkout, build/start only the existing invoice-tool-test Compose service, then verify HTTP health, database and role identity, login rendering, revision routes, status catalog, source layout and deployed commit. No migrations, schema changes, production service changes or deployment configuration changes are part of this structural release. Restore the existing test timer after success.

Local untracked reports that collided with tracked extraction reports were preserved under local-merge-backups/20261018-154458 before merging; unrelated untracked orchestration files are not included in the release.
