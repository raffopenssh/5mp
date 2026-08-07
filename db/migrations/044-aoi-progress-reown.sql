-- 044: re-key the stale 'SYSTEM'-owned aoi_progress notifications.
--
-- scripts/aoi_runner.py originally wrote its per-run progress notification with
-- notify_status()'s default park_id='SYSTEM'. That is a privacy hole rather than
-- an untidiness: aoiNotifSQLFilter() decides an AOI notification's visibility
-- from park_id, and 'SYSTEM' is not an AOI id, so the filter treated the row as
-- an ordinary park notification and served it to every principal -- message
-- included, and that message named the AOI and its ingest progress. The fact
-- that someone is watching a piece of ground is as much the secret as the
-- polygon (docs/AOI_HANDOVER_2.md §2).
--
-- The writer was fixed to pass park_id=<aoi id>; rows written before that are
-- still out there. This is a migration rather than a one-off statement because
-- the manual UPDATE was blocked by SQLite's single writer on three separate
-- attempts across two sessions (the AOI Hansen unit holds it for 35+ minutes),
-- and "run this SQL by hand" had already been carried forward through two
-- handovers without ever executing.
--
-- The id is recovered from the message, which the runner prefixed with
-- '<aoi_id>/<dataset>: ...'; the prefix is then stripped since park_id now
-- carries it. Rows whose prefix does not match a known AOI are deleted: an
-- aoi_progress row we cannot attribute cannot be shown safely, and it is a
-- progress note about work that finished long ago.
UPDATE notifications
   SET park_id = (SELECT a.id FROM aois a
                   WHERE notifications.message LIKE a.id || '/%'),
       message = replace(message,
                   (SELECT a.id || '/' FROM aois a
                     WHERE notifications.message LIKE a.id || '/%'), '')
 WHERE notification_type = 'aoi_progress'
   AND park_id = 'SYSTEM'
   AND EXISTS (SELECT 1 FROM aois a WHERE notifications.message LIKE a.id || '/%');

DELETE FROM notifications
 WHERE notification_type = 'aoi_progress' AND park_id = 'SYSTEM';
