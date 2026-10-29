"""Manual photo types remain authoritative when building report assist groups."""
import unittest
from types import SimpleNamespace
from service_report_assist import ServiceReportAssistService

class AssistPhotoTypeAuthorityTest(unittest.TestCase):
    def test_roles_are_not_reassigned_by_morning_or_evening_time(self):
        photos=[SimpleNamespace(relative_path='arrival.jpg',classification='arrival',manual_classification='arrival',capture_time='2026-10-28T06:24:00'),
                SimpleNamespace(relative_path='safety.jpg',classification='safety',manual_classification='safety',capture_time='2026-10-28T06:25:00'),
                SimpleNamespace(relative_path='departure.jpg',classification='departure',manual_classification='departure',capture_time='2026-10-28T06:26:00')]
        service=ServiceReportAssistService(photo_discovery=SimpleNamespace(discover_photos=lambda *args:(photos,'discovered',None)),
                                         photo_metadata=SimpleNamespace(enrich_all_photos=lambda items,day:items))
        plan=service.build_photo_plan('SO-TEST','2026-10-28')
        for category,name in [('arrival','arrival.jpg'),('self_check','safety.jpg'),('departure','departure.jpg')]:
            self.assertEqual([p['relative_path'] for p in plan['photos'][category]],[name])
        self.assertEqual(plan['arrival_time'],'06:24')
        self.assertEqual(plan['departure_time'],'06:26')

    def test_manual_role_overrides_older_classification(self):
        photo=SimpleNamespace(relative_path='safety.jpg',classification='departure',manual_classification='safety',capture_time=None)
        service=ServiceReportAssistService(photo_discovery=None,photo_metadata=None)
        buckets=service.classify_photos([photo])
        self.assertEqual(buckets['self_check'],[photo])
        self.assertEqual(buckets['departure'],[])

if __name__=='__main__':unittest.main()
