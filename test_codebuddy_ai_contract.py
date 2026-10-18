"""AI endpoints: independent branches with service doubles, no model or photo I/O."""
import test_codebuddy_query_contract as shared
from unittest.mock import patch

class AIData(shared.Data):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        # Draft list SQL uses COUNT(*) AS cnt; preserve the shared fixture fields.
        if set(self)=={'count','total'}:self['cnt']=self['count']

class RouteContract(shared.RouteContract):
    def invoke(self,name,old,mode,method):
        with patch.object(shared,'Data',AIData):
            return super().invoke(name,old,mode,method)

GROUPS=[('draft_get',['ai_daily_report_get_draft']),('draft_prepare',['ai_daily_report_auto_prepare']),('photos_discover',['ai_daily_report_discover_photos']),('photo_timeline',['ai_daily_report_confirm_photo_timeline']),('photos_classify',['ai_daily_report_classify_photos']),('safety_photo',['ai_daily_report_change_safety_photo']),('service_photo_add',['ai_daily_report_add_service_photo']),('service_photo_remove',['ai_daily_report_remove_service_photo']),('photos_auto_select',['ai_daily_report_auto_select_photos']),('photo_mark',['ai_daily_report_mark_photo']),('photo_time',['ai_daily_report_update_photo_time']),('draft_cancel',['ai_daily_report_cancel']),('draft_delete',['ai_daily_report_delete']),('draft_reopen',['ai_daily_report_reopen']),('draft_csrf',['ai_daily_report_csrf']),('draft_orders',['ai_daily_report_service_orders_options']),('draft_create',['ai_daily_report_create_draft']),('draft_list',['ai_daily_report_drafts_list']),('draft_pages',['ai_daily_report_drafts_page','ai_daily_report_draft_detail_page'])]
for _key,_names in GROUPS:
    _class=''.join(s.title() for s in _key.split('_'))+'Test'
    globals()[_class]=type(_class,(RouteContract,),{'__module__':__name__,'BASELINE':'10fa445df6ebc6d23898a1f7ccb91c30d034d152','FUNCTIONS':tuple(_names),'CASE_KIND':'ai','test_registration_and_branches':RouteContract.check_contract})
