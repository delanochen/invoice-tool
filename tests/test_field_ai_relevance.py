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
