BEGIN;
CREATE OR REPLACE VIEW telegram_audit_portal AS
SELECT p.mxid AS room, p.id AS peer, p.receiver, u.id AS login, u.user_mxid AS owner,
       p.relay_login_id AS relay, p.room_type AS room_kind,
       COALESCE((p.metadata->>'is_forum_general')::boolean, FALSE) AS forum,
       COALESCE((p.metadata->>'is_supergroup')::boolean, FALSE) AS supergroup,
       (p.user_blocked OR p.message_request) AS blocked
FROM portal p JOIN user_login u ON (p.bridge_id,p.receiver)=(u.bridge_id,u.id)
WHERE p.bridge_id='' AND p.mxid IS NOT NULL AND p.parent_id IS NULL AND p.relay_bridge_id=p.bridge_id;
CREATE OR REPLACE VIEW telegram_audit_message AS
SELECT p.mxid AS room, m.room_id AS peer, m.room_receiver AS receiver,
       m.mxid AS event, m.sender_id, m.sender_mxid, m.timestamp/1000000 AS ts_ms
FROM message m JOIN portal p ON (m.bridge_id,m.room_id,m.room_receiver)=(p.bridge_id,p.id,p.receiver)
WHERE m.bridge_id='' AND NOT COALESCE(m.double_puppeted,FALSE) AND m.thread_root_id IS NULL;
REVOKE ALL ON telegram_audit_portal, telegram_audit_message FROM PUBLIC;
GRANT SELECT ON telegram_audit_portal, telegram_audit_message TO matrix_telegram_audit;
COMMIT;
