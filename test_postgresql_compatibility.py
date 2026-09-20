import os
import sqlite3
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timezone,timedelta
from io import BytesIO
from pathlib import Path

from database import translate_sql,PostgreSQLConnection,Row


class SQLTranslationTests(unittest.TestCase):
    def test_literals_comments_and_identifier_are_not_rewritten(self):
        sql="select '?' as q, 'date(x) like ? 50%' as label, ? as value -- ? date(x)\n"
        converted=translate_sql(sql,True)
        self.assertIn("'date(x) like ? 50%%'",converted)
        self.assertIn('%s as value',converted)
        self.assertIn('-- ? date(x)',converted)
    def test_internal_marker_literal_survives(self):
        self.assertIn("'__invoice_bind__'",translate_sql("select '__invoice_bind__', ?",True))
    def test_unknown_replace_rejected(self):
        with self.assertRaises(sqlite3.NotSupportedError): translate_sql('insert or replace into users(id) values (?)',True)
    def test_row_preserves_first_duplicate_and_position(self):
        row=Row(['id','name','id'],[1,'test',2])
        self.assertEqual(row['id'],1)
        self.assertEqual(row[2],2)
        self.assertEqual(dict(row),{'id':1,'name':'test'})


@unittest.skipUnless('invoice_tests_rehearsal' in os.environ.get('DATABASE_URL',''),'dedicated PostgreSQL test database required')
class PostgreSQLIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.c=PostgreSQLConnection()
        self.addCleanup(self.c.close)
        self.key='pg-test-'+uuid.uuid4().hex
        self.user=self.c.execute("select id from users where role='admin' order by id limit 1").fetchone()[0]
    def test_binding_and_nulls(self):
        evil="x'); DELETE FROM users; -- ? 50%"
        row=self.c.execute("select ? as value, ? is null as empty, '?' as literal, '50%' as percent",(evil,None)).fetchone()
        self.assertEqual(row['value'],evil)
        self.assertTrue(row['empty'])
        self.assertEqual(row['literal'],'?')
        self.assertEqual(row['percent'],'50%')
    def test_rollback_and_primary_key_return(self):
        old=self.c.execute('select max(id) from messages').fetchone()[0]
        cur=self.c.execute('insert into messages(user_id,title,body,created_at) values(?,?,?,?)',(self.user,self.key,'test','2026-09-19T12:00:00Z'))
        self.assertGreater(cur.lastrowid,old)
        self.c.rollback()
        self.assertIsNone(self.c.execute('select id from messages where title=?',(self.key,)).fetchone())
    def test_foreign_key_failure_preserves_transaction(self):
        self.c.execute('insert into settings(key,value) values(?,?)',(self.key,'first'))
        with self.assertRaises(sqlite3.IntegrityError):
            self.c.execute('insert into messages(user_id,title,body,created_at) values(?,?,?,?)',(-999,self.key,'test','2026-09-19T12:00:00Z'))
        self.assertEqual(self.c.execute('select value from settings where key=?',(self.key,)).fetchone()[0],'first')
        self.c.rollback()
    def test_upsert_ignore_and_commit_visible_to_new_connection(self):
        for value in ('one','two'):
            self.c.execute('insert or replace into settings(key,value) values(?,?)',(self.key,value))
        self.c.execute('insert or ignore into settings(key,value) values(?,?)',(self.key,'ignored'))
        self.c.commit()
        other=PostgreSQLConnection()
        try: self.assertEqual(other.execute('select value from settings where key=?',(self.key,)).fetchone()[0],'two')
        finally: other.execute('delete from settings where key=?',(self.key,)); other.commit(); other.close()
    def test_dates_match_sqlite_utc_and_invalid_values(self):
        local=sqlite3.connect(':memory:')
        self.addCleanup(local.close)
        for value in ['2026-09-11T23:30:00-05:00','2026-09-11','',None,'invalid']:
            sql='select date(?),datetime(?),julianday(?)'
            left=local.execute(sql,(value,)*3).fetchone()
            right=tuple(self.c.execute(sql,(value,)*3).fetchone())
            self.assertEqual(left[:2],right[:2])
            if left[2] is None: self.assertIsNone(right[2])
            else: self.assertAlmostEqual(left[2],right[2],places=7)
    def test_ascii_like_and_glob(self):
        self.assertTrue(self.c.execute('select ? like ?',('AbCd','ab%')).fetchone()[0])
        self.assertFalse(self.c.execute('select ? like ?',('Æ','æ')).fetchone()[0])
        self.assertTrue(self.c.execute("select ? glob 'BUY[0-9][0-9][0-9][0-9][0-9]'",('BUY00001',)).fetchone()[0])
    def test_metadata_and_runtime_role_restrictions(self):
        self.assertTrue(self.c.execute('pragma table_info(users)').fetchall())
        with self.assertRaises(sqlite3.NotSupportedError): self.c.execute('pragma foreign_keys=off')
        with self.assertRaises(sqlite3.NotSupportedError): self.c.execute('create table unexpected(id integer)')
        self.assertFalse(self.c.raw.execute("select rolsuper from pg_roles where rolname=current_user").fetchone()[0])
    def test_ai_draft_save_and_optimistic_lock(self):
        from ai_daily_report.daily_report_service import DailyReportService,DraftVersionConflict
        order=self.c.execute('select id from service_orders order by id limit 1').fetchone()[0]
        svc=DailyReportService(self.c,lambda:'2026-09-19T12:00:00Z',self.user,'PG test')
        row=svc.create_draft(order,'2099-01-01')
        draft=svc.parse_draft_data(row)
        svc.save_draft(row['id'],draft,expected_version=row['draft_version'])
        with self.assertRaises(DraftVersionConflict): svc.save_draft(row['id'],draft,expected_version=row['draft_version'])
        self.c.rollback()
    def test_parallel_number_allocation(self):
        import app as m
        def create(_):
            with m.app.app_context():
                number=m.next_service_order_number()
                cur=m.db().execute('insert into service_orders(order_number,client_name,site_address,client_order_number,created_by,created_at) values(?,?,?,?,?,?)',
                    (number,self.key,'test','PG',self.user,'2026-09-19T12:00:00Z'))
                m.db().commit()
                return cur.lastrowid,number
        with ThreadPoolExecutor(max_workers=6) as pool: created=list(pool.map(create,range(12)))
        self.assertEqual(len({n for _,n in created}),12)
        self.assertEqual(len({i for i,_ in created}),12)
        self.c.execute('delete from service_orders where client_name=?',(self.key,))
        self.c.commit()
    def test_http_photo_retry_watermark_date_and_preview(self):
        import app as m
        from PIL import Image
        m.app.config.update(TESTING=True)
        client=m.app.test_client()
        with client.session_transaction() as session: session['user_id']=self.user
        token=client.get('/api/field/session').json['csrf']
        order=self.c.execute('select id from service_orders order by id limit 1').fetchone()[0]
        image=BytesIO(); Image.new('RGB',(64,64),'green').save(image,'JPEG')
        key=uuid.uuid4().hex
        def send():
            return client.post('/api/field/photos',headers={'X-Field-Token':token},data={
                'client_id':key,'user_id':str(self.user),'order_id':str(order),
                'captured_at':datetime.now(timezone.utc).isoformat(),'watermark_at':'2026-09-11T17:00:00-05:00',
                'timezone_name':'America/Chicago','latitude':'30','longitude':'-95','accuracy':'10','source':'camera',
                'photo':(BytesIO(image.getvalue()),'test.jpg')})
        first=send(); self.assertEqual(first.status_code,200,first.json)
        again=send(); self.assertEqual(again.status_code,200,again.json)
        self.assertEqual(first.json['id'],again.json['id'])
        self.assertTrue(again.json['duplicate'])
        row=self.c.execute('select * from field_photos where id=?',(first.json['id'],)).fetchone()
        self.assertEqual(row['capture_date'],'2026-09-11')
        self.assertIn('/2026-09-11/',row['relative_path'])
        photo=Path(m.SHARED_PHOTOS_DIR)/row['relative_path']
        self.assertTrue(photo.is_file())
        # All fixture files live under the isolated scratch tree.
        self.assertTrue(str(photo).startswith('/scratch/'))
        photo.unlink()
        self.c.execute('delete from field_photos where id=?',(row['id'],)); self.c.commit()


if __name__=='__main__': unittest.main()
