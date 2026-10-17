"""Calendar/map behavior at extracted module boundaries; no external API calls."""
from datetime import date
import unittest
from unittest.mock import patch

from flask import url_for
import test_service_order_manufacturer as fixtures


class CalendarMapModuleContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.ServiceOrderManufacturerTest.setUpClass()
        cls.module = fixtures.ServiceOrderManufacturerTest.module

    @classmethod
    def tearDownClass(cls):
        fixtures.ServiceOrderManufacturerTest.tearDownClass()

    def setUp(self):
        self.fixture = fixtures.ServiceOrderManufacturerTest()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.http = self.fixture.http

    def test_route_urls_methods_and_legacy_exports(self):
        for name, url, methods in (
            ('service_order_calendar','/service-orders/calendar',{'GET'}),
            ('service_order_map','/service-orders/map',{'GET'}),
            ('service_order_map_static_image','/service-orders/map/static-image',{'POST'}),
            ('geocode_next_service_order','/service-orders/map/geocode-next',{'POST'}),
            ('retry_failed_service_order_geocodes','/service-orders/map/retry-failed',{'POST'}),
        ):
            rules = list(self.module.app.url_map.iter_rules(name))
            self.assertEqual(len(rules), 1)
            self.assertEqual(rules[0].methods - {'HEAD','OPTIONS'}, methods)
            self.assertIs(getattr(self.module,name), self.module.app.view_functions[name])
            self.assertEqual(getattr(self.module,name).__module__, 'invoice_tool.service_orders.calendar_map_routes')
            with self.module.app.test_request_context():
                self.assertEqual(url_for(name), url)

    def test_calendar_reads_current_helper_and_handles_invalid_month(self):
        with patch.object(self.module, 'service_order_calendar_weeks', return_value=[]) as weeks:
            response = self.http.get('/service-orders/calendar?year=bad&month=99')
        self.assertEqual(response.status_code, 200)
        weeks.assert_called_once_with(date.today().year, date.today().month)

    def test_calendar_query_preserves_external_client_scope(self):
        with self.module.app.app_context():
            connection = self.module.db()
            connection.execute('update service_orders set start_date=? where id=?', ('2026-10-01', self.fixture.order_id))
            connection.commit()
        with self.module.app.test_request_context():
            from flask import g
            g.user = {'id':123, 'role':'external_manager', 'client_id':self.fixture.client_id}
            rows = self.module.service_order_calendar_rows(date(2026,10,1), date(2026,10,31))
            self.assertEqual([row['id'] for row in rows], [self.fixture.order_id])
            g.user = dict(g.user, client_id=None)
            self.assertEqual(self.module.service_order_calendar_rows(date(2026,10,1), date(2026,10,31)), [])

    def test_disabled_geocoding_uses_current_flag_without_calling_provider(self):
        with patch.object(self.module, 'GEOCODING_ENABLED', False):
            with patch.object(self.module, 'geocode_buyer') as geocode:
                response = self.http.post('/service-orders/map/geocode-next')
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.get_json(), {'available':False, 'remaining':0})
        geocode.assert_not_called()

    def test_temporary_geocoding_error_preserves_pending_buyer(self):
        with self.module.app.app_context():
            self.module.db().execute("update buyers set geocode_status='pending',manual_coordinates=0 where id=?", (self.fixture.buyer_id,))
            self.module.db().commit()
        with patch.object(self.module, 'GEOCODING_ENABLED', True):
            with patch.object(self.module, 'geocode_buyer', side_effect=self.module.TemporaryGeocodingError('simulated')):
                response = self.http.post('/service-orders/map/geocode-next')
        self.assertEqual(response.status_code, 503)
        self.assertTrue(response.get_json()['temporary_error'])
        with self.module.app.app_context():
            self.assertEqual(self.module.db().execute('select geocode_status from buyers where id=?', (self.fixture.buyer_id,)).fetchone()['geocode_status'], 'pending')

    def test_retry_resets_only_current_client_nonmanual_failed_sites(self):
        with self.module.app.app_context():
            connection = self.module.db()
            uid = connection.execute("insert into users (name,email,password_hash,role,client_id,is_active,created_at) values ('External manager','map-manager@example.test','unused','external_manager',?,1,?)", (self.fixture.client_id,self.module.now())).lastrowid
            connection.execute("update buyers set geocode_status='failed',manual_coordinates=0 where id=?", (self.fixture.buyer_id,))
            other = connection.execute("insert into buyers (buyer_number,name,client_id,country,country_code,detailed_address,geocode_status,manual_coordinates,created_at) values ('MAP-OTHER','Other site',null,'US','US','Other address','failed',0,?)", (self.module.now(),)).lastrowid
            connection.commit()
        with self.http.session_transaction() as session:
            session['user_id'] = uid
        self.assertEqual(self.http.post('/service-orders/map/retry-failed').status_code, 200)
        with self.module.app.app_context():
            connection = self.module.db()
            self.assertEqual(connection.execute('select geocode_status from buyers where id=?', (self.fixture.buyer_id,)).fetchone()['geocode_status'], 'pending')
            self.assertEqual(connection.execute('select geocode_status from buyers where id=?', (other,)).fetchone()['geocode_status'], 'failed')
            connection.execute("update buyers set geocode_status='failed',manual_coordinates=1 where id=?", (self.fixture.buyer_id,))
            connection.commit()
        self.http.post('/service-orders/map/retry-failed')
        with self.module.app.app_context():
            self.assertEqual(self.module.db().execute('select geocode_status from buyers where id=?', (self.fixture.buyer_id,)).fetchone()['geocode_status'], 'failed')

    def test_current_static_marker_limit_is_resolved_before_provider_call(self):
        with patch.object(self.module, 'SERVICE_ORDER_STATIC_MAP_MAX_MARKERS', 0):
            with patch.object(self.module, 'get_google_static_maps_api_key') as key:
                response = self.http.post('/service-orders/map/static-image', json={'points':[{'latitude':30,'longitude':-95}]})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()['error'], 'no_visible_sites')
        key.assert_not_called()

    def test_geocode_success_commits_and_returns_refreshed_map_payload(self):
        with self.module.app.app_context():
            self.module.db().execute("update buyers set geocode_status='pending',manual_coordinates=0,geocode_address=null where id=?", (self.fixture.buyer_id,))
            self.module.db().commit()
        with patch.object(self.module, 'GEOCODING_ENABLED', True):
            with patch.object(self.module, 'geocode_address', return_value=(30.1,-95.2)) as provider:
                response = self.http.post('/service-orders/map/geocode-next')
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload['buyer']['id'], self.fixture.buyer_id)
        self.assertEqual(payload['buyer']['geocode_status'], 'success')
        self.assertEqual(float(payload['buyer']['latitude']), 30.1)
        self.assertEqual(payload['remaining'], 0)
        provider.assert_called_once_with('100 Test St')
        with self.module.app.app_context():
            self.assertEqual(self.module.db().execute('select geocode_status from buyers where id=?', (self.fixture.buyer_id,)).fetchone()['geocode_status'], 'success')
