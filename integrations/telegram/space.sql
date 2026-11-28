BEGIN;
-- Keep the view name compatible with deployed guide containers. An opened portal is enough to link it.
CREATE OR REPLACE VIEW telegram_space_sent_portal AS
SELECT DISTINCT p.mxid AS room, u.user_mxid AS owner, p.id AS peer, p.name AS name
FROM portal p JOIN user_login u ON (p.bridge_id,p.receiver)=(u.bridge_id,u.id)
WHERE p.bridge_id='' AND p.mxid IS NOT NULL AND p.parent_id IS NULL
  AND NOT (p.user_blocked OR p.message_request)
  AND p.id ~ '^(user|chat|channel):[1-9][0-9]*$'
  AND (p.id NOT LIKE 'channel:%' OR (
    p.metadata->'is_supergroup' = 'true'::jsonb AND p.metadata->'is_forum_general' IS DISTINCT FROM 'true'::jsonb
  ));
REVOKE ALL ON telegram_space_sent_portal FROM PUBLIC;
GRANT SELECT ON telegram_space_sent_portal TO matrix_telegram_space;
CREATE OR REPLACE VIEW telegram_space_known_group AS
SELECT DISTINCT p.mxid AS room, u.user_mxid AS owner, p.id AS peer, p.name AS name
FROM portal p JOIN user_login u ON (p.bridge_id,p.receiver)=(u.bridge_id,u.id)
WHERE p.bridge_id='' AND p.parent_id IS NULL AND NOT (p.user_blocked OR p.message_request)
  AND (p.id ~ '^chat:[1-9][0-9]*$' OR (p.id ~ '^channel:[1-9][0-9]*$'
    AND p.metadata->'is_supergroup' = 'true'::jsonb AND p.metadata->'is_forum_general' IS DISTINCT FROM 'true'::jsonb));
REVOKE ALL ON telegram_space_known_group FROM PUBLIC;
GRANT SELECT ON telegram_space_known_group TO matrix_telegram_space;
CREATE OR REPLACE VIEW telegram_space_login_status AS
SELECT user_mxid AS owner, count(*) AS linked_accounts
FROM user_login WHERE bridge_id='' GROUP BY user_mxid;
REVOKE ALL ON telegram_space_login_status FROM PUBLIC;
GRANT SELECT ON telegram_space_login_status TO matrix_telegram_space;
CREATE OR REPLACE VIEW telegram_space_admin_login AS
SELECT user_mxid AS owner, id::text AS login, xmin::text AS revision
FROM user_login WHERE bridge_id='';
REVOKE ALL ON telegram_space_admin_login FROM PUBLIC;
GRANT SELECT ON telegram_space_admin_login TO matrix_telegram_space;
COMMIT;
