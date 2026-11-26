BEGIN;
CREATE OR REPLACE VIEW telegram_space_sent_portal AS
SELECT DISTINCT p.mxid AS room, u.user_mxid AS owner
FROM portal p JOIN user_login u ON (p.bridge_id,p.receiver)=(u.bridge_id,u.id)
WHERE p.bridge_id='' AND p.mxid IS NOT NULL AND p.parent_id IS NULL
  AND NOT (p.user_blocked OR p.message_request)
  AND p.id ~ '^(user|chat|channel):[1-9][0-9]*$'
  AND (p.id NOT LIKE 'channel:%' OR (
    p.metadata->'is_supergroup' = 'true'::jsonb AND p.metadata->'is_forum_general' IS DISTINCT FROM 'true'::jsonb
  ))
  AND EXISTS (
    SELECT 1 FROM message m
    WHERE (m.bridge_id,m.room_id,m.room_receiver)=(p.bridge_id,p.id,p.receiver)
      AND m.sender_mxid=u.user_mxid AND m.sender_id=u.id AND m.id<>''
      AND NOT COALESCE(m.double_puppeted,FALSE) AND m.thread_root_id IS NULL
  );
REVOKE ALL ON telegram_space_sent_portal FROM PUBLIC;
GRANT SELECT ON telegram_space_sent_portal TO matrix_telegram_space;
COMMIT;
