"""Mobile clock-in and shared-photo access boundary regressions."""
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from PIL import Image
from flask import g, url_for
import test_service_order_manufacturer as fixtures


class PhotoCaptureModuleContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.ServiceOrderManufacturerTest.setUpClass()
        cls.module = fixtures.ServiceOrderManufacturerTest.module

    @classmethod
    def tearDownClass(cls):
        fixtures.ServiceOrderManufacturerTest.tearDownClass()

    def setUp(self):
        self.fixture = fixtures.ServiceOrderManufacturerTest()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()
        self.http = self.fixture.http
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)/'shared'
        root = patch.object(self.module,'SHARED_PHOTOS_DIR',str(self.root))
        root.start()
        self.addCleanup(root.stop)

    def image(self,path,color='green',size=(40,30)):
        path.parent.mkdir(parents=True,exist_ok=True)
        Image.new('RGB',size,color).save(path,format='JPEG')
        return path

    def form(self,**extra):
        stream = BytesIO()
        Image.new('RGB',(20,20),'green').save(stream,format='JPEG')
        stream.seek(0)
        values = dict(site_id=str(self.fixture.buyer_id),captured_at=datetime.now(timezone.utc).isoformat(),time_offset_minutes='0',latitude='30',longitude='-95',accuracy='8',photo=(stream,'clock.jpg'))
        values.update(extra)
        return values

    def test_routes_preserve_urls_methods_and_legacy_aliases(self):
        for name,url,methods in (
            ('mobile_clock_in','/mobile-clock-in',{'GET'}),
            ('upload_mobile_clock_in','/api/mobile-clock-in',{'POST'}),
            ('browse_shared_photos','/shared-photos/browse',{'GET'}),
            ('download_shared_photos','/shared-photos/download',{'POST'}),
            ('shared_photo_preview','/shared-photos/preview',{'GET'}),
            ('shared_photo_thumbnail','/shared-photos/thumbnail',{'GET'}),
        ):
            rules = list(self.module.app.url_map.iter_rules(name))
            self.assertEqual(len(rules),1)
            self.assertEqual(rules[0].methods-{'HEAD','OPTIONS'},methods)
            self.assertIs(getattr(self.module,name),self.module.app.view_functions[name])
            self.assertIn(getattr(self.module,name).__module__,(self.module.__name__,'invoice_tool.photos.routes'))
            with self.module.app.test_request_context():
                self.assertEqual(url_for(name),url)

    def test_clock_validation_rejects_bad_coordinates_offset_and_time(self):
        for extra in ({'latitude':'91'},{'time_offset_minutes':'1441'},{'captured_at':(datetime.now(timezone.utc)-timedelta(days=1)).isoformat()}):
            response = self.http.post('/api/mobile-clock-in',data=self.form(**extra))
            self.assertEqual(response.status_code,422)
        with self.module.app.app_context():
            self.assertEqual(self.module.db().execute('select count(*) from clock_in_photos').fetchone()[0],0)
        self.assertFalse(self.root.exists())

    def test_external_site_query_is_scoped_to_client(self):
        with self.module.app.test_request_context():
            g.user = {'id':1,'role':'external_manager','client_id':self.fixture.client_id}
            self.assertEqual([row['id'] for row in self.module.clock_in_site_rows()],[self.fixture.buyer_id])
            g.user = dict(g.user,client_id=None)
            self.assertEqual(self.module.clock_in_site_rows(),[])
        with patch.object(self.module,'clock_in_site_rows',return_value=[]):
            self.assertEqual(self.http.post('/api/mobile-clock-in',data=self.form()).status_code,422)

    def test_shared_paths_reject_traversal_and_nonprocessed_preview(self):
        self.image(self.root/'SO-TEST/incoming/raw.jpg')
        self.image(self.root/'SO-TEST/pictures/good.jpg')
        for url in ('/shared-photos/preview','/shared-photos/thumbnail'):
            self.assertEqual(self.http.get(url,query_string={'path':'../outside.jpg'}).status_code,403)
        self.assertEqual(self.http.get('/shared-photos/preview',query_string={'path':'SO-TEST/incoming/raw.jpg'}).status_code,403)
        self.assertEqual(self.http.post('/shared-photos/download',data={'path':'SO-TEST/incoming/raw.jpg'}).status_code,403)
        response = self.http.get('/shared-photos/preview',query_string={'path':'SO-TEST/pictures/good.jpg'})
        self.assertEqual(response.status_code,200)
        response.close()

    def test_external_users_cannot_browse_download_or_preview_shared_photos(self):
        with patch.object(self.module,'is_internal_user',return_value=False):
            for url in ('/shared-photos/browse','/shared-photos/preview','/shared-photos/thumbnail'):
                self.assertEqual(self.http.get(url).status_code,403)
            self.assertEqual(self.http.post('/shared-photos/download',data={'path':'any'}).status_code,403)

    def test_thumbnail_uses_valid_cache_then_computes_bounded_jpeg(self):
        self.image(self.root/'SO-TEST/pictures/day/photo.jpg',size=(1000,800))
        cached = self.image(self.root/'SO-TEST/thumbnails/day/photo.jpg',color='blue')
        url = '/shared-photos/thumbnail?path=SO-TEST/pictures/day/photo.jpg'
        response = self.http.get(url)
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.data,cached.read_bytes())
        response.close()
        cached.write_bytes(b'broken-cache')
        response = self.http.get(url)
        self.assertEqual(response.status_code,200)
        with Image.open(BytesIO(response.data)) as image:
            self.assertEqual(image.format,'JPEG')
            self.assertLessEqual(image.width,420)
            self.assertLessEqual(image.height,320)
        response.close()

    def test_browse_filters_invalid_hidden_and_nas_metadata_images(self):
        self.image(self.root/'SO-TEST/pictures/2026-10-17/good.jpg')
        self.image(self.root/'SO-TEST/pictures/2026-10-17/.hidden.jpg')
        self.image(self.root/'SO-TEST/pictures/2026-10-17/@eaDir/metadata.jpg')
        (self.root/'SO-TEST/pictures/2026-10-17/broken.jpg').write_bytes(b'bad')
        response = self.http.get('/shared-photos/browse?path=SO-TEST&day=2026-10-17')
        self.assertEqual(response.status_code,200)
        self.assertEqual([row['name'] for row in response.get_json()['images']],['good.jpg'])
        self.assertEqual(response.get_json()['status']['completed'],1)

    def test_download_deduplicates_paths_and_cleans_zip_on_close(self):
        path = self.image(self.root/'SO-TEST/pictures/day/photo.jpg')
        original = self.module.tempfile.NamedTemporaryFile
        def temporary(*args,**kwargs):
            return original(*args,**dict(kwargs,dir=self.temp.name))
        with self.module.app.test_request_context('/shared-photos/download',method='POST',data={'path':['SO-TEST/pictures/day/photo.jpg']*2}):
            g.user = self.module.db().execute('select * from users where id=?',(self.fixture.user_id,)).fetchone()
            with patch.object(self.module.tempfile,'NamedTemporaryFile',side_effect=temporary):
                response = self.module.download_shared_photos.__wrapped__()
            archives = list(Path(self.temp.name).glob('*.zip'))
            self.assertEqual(len(archives),1)
            stream = response.response
            try:
                response.direct_passthrough = False
                with ZipFile(BytesIO(response.get_data())) as archive:
                    self.assertEqual(archive.namelist(),['SO-TEST/day/photo.jpg'])
                    self.assertEqual(archive.read(archive.namelist()[0]),path.read_bytes())
            finally:
                # Reading a streaming response into memory changes Werkzeug's
                # close-callback ordering; close its original file stream first.
                stream.close()
                response.close()
            self.assertFalse(archives[0].exists())
