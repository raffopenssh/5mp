-- name: GetUnreadNotifications :many
SELECT * FROM notifications 
WHERE is_read = 0 
ORDER BY created_at DESC 
LIMIT ?;

-- name: GetNotificationsByPark :many
SELECT * FROM notifications 
WHERE park_id = ? 
ORDER BY created_at DESC 
LIMIT ?;

-- name: GetNotificationsByType :many
SELECT * FROM notifications 
WHERE notification_type = ? 
ORDER BY created_at DESC 
LIMIT ?;

-- name: MarkNotificationRead :exec
UPDATE notifications SET is_read = 1 WHERE id = ?;

-- name: MarkAllNotificationsRead :exec
UPDATE notifications SET is_read = 1 WHERE is_read = 0;

-- name: GetUnreadNotificationCount :one
SELECT COUNT(*) as count FROM notifications WHERE is_read = 0;

-- name: InsertNotification :one
INSERT INTO notifications (park_id, notification_type, title, message, reference_id, reference_url)
VALUES (?, ?, ?, ?, ?, ?)
RETURNING id;

-- name: DeleteOldNotifications :exec
DELETE FROM notifications WHERE created_at < datetime('now', '-30 days') AND is_read = 1;
