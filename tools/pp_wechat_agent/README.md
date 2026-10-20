# PP WeChat Agent

Windows wrapper around wechatauto-replica for PP Field AI.

Privacy rules:
- Private/direct chats are never uploaded.
- Group discovery sends only group ID, group name, and account ID.
- Message bodies are read only for group IDs enabled by invoice-tool.
- First enable starts from current position. Historical import requires an explicit backfill command.

Environment:
PP_WECHAT_SERVER_URL
PP_WECHAT_INGEST_TOKEN

Commands:
python agent.py sync-groups
python agent.py collect-once
python agent.py backfill --limit 200
python agent.py daemon
