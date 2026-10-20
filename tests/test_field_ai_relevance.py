from invoice_tool.field_ai.services import FieldAIService


def test_identity_question_extracts_subject():
    assert FieldAIService._search_term("Glorianna是谁？") == "Glorianna"
    assert FieldAIService._search_term("Who is Glorianna?") == "Glorianna"


def test_gather_context_does_not_fallback_to_recent_messages():
    class Service(FieldAIService):
        def __init__(self):
            pass

        def search_wechat(self, user, question, limit=12):
            return []

        def recent_wechat(self, user, limit=20):
            raise AssertionError("unrelated recent messages must not be used")

        def confirmed_technical_knowledge(self, user, question, limit=8):
            return []

    context = Service().gather_context({"id": 1, "role": "admin"}, "Glorianna是谁？")
    assert context == {"wechat": [], "technical_knowledge": []}


def test_identity_search_matches_sender_name_not_whole_question():
    import sqlite3

    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript("""
        create table wechat_group_project_mappings (
            chat_username text, chat_name text, enabled integer,
            service_order_id integer, project_name text
        );
        create table wechat_raw_messages (
            id integer, message_time text, sender_name text, content text,
            chat_name text, chat_username text, chat_type text
        );
        insert into wechat_group_project_mappings values
            ('g@chatroom', 'PP工程群', 1, 1, 'Project');
        insert into wechat_raw_messages values
            (1, '2026-10-20T10:00:00', 'Glorianna', 'Please check the site report',
             'PP工程群', 'g@chatroom', 'group');
        insert into wechat_raw_messages values
            (2, '2026-10-20T10:01:00', 'Someone Else', 'unrelated recent message',
             'PP工程群', 'g@chatroom', 'group');
    """)
    service = FieldAIService(db=lambda: db)
    rows = service.search_wechat({"id": 1, "role": "admin"}, "Glorianna是谁？")
    assert [row["id"] for row in rows] == [1]


def test_wechat_groups_are_not_scoped_by_service_order():
    import sqlite3

    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript("""
        create table wechat_group_project_mappings (
            chat_username text, chat_name text, enabled integer,
            service_order_id integer, project_name text
        );
        create table wechat_raw_messages (
            id integer, message_time text, sender_name text, content text,
            chat_name text, chat_username text, chat_type text
        );
        insert into wechat_group_project_mappings values
            ('enabled@chatroom', 'Enabled Work Group', 1, null, null),
            ('disabled@chatroom', 'Disabled Group', 0, null, null);
        insert into wechat_raw_messages values
            (1, '2026-10-20T10:00:00', 'Worker', 'field update',
             'Enabled Work Group', 'enabled@chatroom', 'group'),
            (2, '2026-10-20T10:01:00', 'Worker', 'private config',
             'Disabled Group', 'disabled@chatroom', 'group');
    """)
    service = FieldAIService(db=lambda: db)
    ordinary_user = {"id": 99, "role": "employee"}
    groups = service.visible_groups(ordinary_user)
    messages = service.list_wechat(ordinary_user)
    assert [row["chat_username"] for row in groups] == ["enabled@chatroom"]
    assert [row["id"] for row in messages] == [1]


def test_mixed_language_question_extracts_project_name():
    assert FieldAIService._search_terms("Longbow今天有没有故障") == ["Longbow"]


def test_longbow_question_matches_message_without_whole_question():
    import sqlite3

    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript("""
        create table wechat_group_project_mappings (
            chat_username text, chat_name text, enabled integer,
            service_order_id integer, project_name text
        );
        create table wechat_raw_messages (
            id integer, message_time text, sender_name text, content text,
            chat_name text, chat_username text, chat_type text
        );
        insert into wechat_group_project_mappings values
            ('g@chatroom', '同飞股份-美国Prasinos Power沟通', 1, null, null);
        insert into wechat_raw_messages values
            (1, '2026-10-20T15:07:53+00:00', '高阳',
             'longbow 场站的甲方跟我们说有2台报错',
             '同飞股份-美国Prasinos Power沟通', 'g@chatroom', 'group');
    """)
    service = FieldAIService(db=lambda: db)
    rows = service.search_wechat({"id": 9, "role": "employee"}, "Longbow今天有没有故障")
    assert [row["id"] for row in rows] == [1]


def test_image_xml_is_not_rendered_or_sent_to_ai():
    xml = '<?xml version="1.0"?><msg><img aeskey="secret" cdnthumburl="https://example.invalid/x" /></msg>'
    assert FieldAIService.display_message(xml) == "\U0001f5bc \u56fe\u7247"
    assert '<msg>' not in FieldAIService.display_message(xml)


def test_unknown_xml_is_replaced_with_safe_rich_media_label():
    xml = '<?xml version="1.0"?><msg><appmsg><title>x</title></appmsg></msg>'
    assert FieldAIService.display_message(xml) == "\U0001f4ce \u6587\u4ef6 / \u94fe\u63a5 / \u5361\u7247"
