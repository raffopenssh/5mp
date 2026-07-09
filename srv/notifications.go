package srv

import (
	"database/sql"
	"encoding/json"
	"net/http"
	"strconv"
	"strings"
	"time"
)

// Notification represents a notification record
type Notification struct {
	ID               int64     `json:"id"`
	ParkID           string                 `json:"park_id"`
	NotificationType string                 `json:"notification_type"`
	Title            string                 `json:"title"`
	Message          string                 `json:"message,omitempty"`
	ReferenceID      string                 `json:"reference_id,omitempty"`
	ReferenceURL     string                 `json:"reference_url,omitempty"`
	ReferenceData    map[string]interface{} `json:"reference_data,omitempty"`
	IsRead           bool                   `json:"is_read"`
	CreatedAt        time.Time              `json:"created_at"`
}

// HandleGetNotifications returns unread or filtered notifications
// GET /api/notifications?limit=50&park_id=XXX&type=new_publication
func (s *Server) HandleGetNotifications(w http.ResponseWriter, r *http.Request) {
	limit := 50
	if l := r.URL.Query().Get("limit"); l != "" {
		if parsed, err := strconv.Atoi(l); err == nil && parsed > 0 && parsed <= 1000 {
			limit = parsed
		}
	}

	parkID := r.URL.Query().Get("park_id")
	notifType := r.URL.Query().Get("type")
	unreadOnly := r.URL.Query().Get("unread") == "true" || r.URL.Query().Get("unread") == "1"
	activeOnly := r.URL.Query().Get("active") == "true" || r.URL.Query().Get("active") == "1"

	var query string
	var args []interface{}

	// Env scoping: 'new_upload' and MBTiles notifications are tenant-scoped;
	// all other notification types are shared across prod and test.
	env := RequestEnv(r)
	envCond := "(notification_type NOT IN ('new_upload','mbtiles_complete','mbtiles_failed') OR env = ?)"

	// For fire_alert notifications with active=true, filter by recent end_date in feature_geometries
	if notifType == "fire_alert" && activeOnly {
		query = `SELECT DISTINCT n.id, n.park_id, n.notification_type, n.title, n.message, n.reference_id, n.reference_url, n.reference_data, n.is_read, n.created_at
		         FROM notifications n
		         JOIN feature_geometries fg ON n.park_id = fg.park_id AND n.reference_id = fg.feature_id
		         WHERE n.notification_type = 'fire_alert'
		           AND fg.feature_type = 'fire_trajectory'
		           AND julianday('now') - julianday(fg.end_date) <= 3
		         ORDER BY n.created_at DESC LIMIT ?`
		args = []interface{}{limit}
	} else if parkID != "" {
		query = `SELECT id, park_id, notification_type, title, message, reference_id, reference_url, reference_data, is_read, created_at
		         FROM notifications WHERE park_id = ? AND ` + envCond + ` ORDER BY created_at DESC LIMIT ?`
		args = []interface{}{parkID, env, limit}
	} else if notifType != "" {
		// comma-separated list of types supported (e.g. cron status types)
		types := strings.Split(notifType, ",")
		placeholders := strings.TrimSuffix(strings.Repeat("?,", len(types)), ",")
		query = `SELECT id, park_id, notification_type, title, message, reference_id, reference_url, reference_data, is_read, created_at
		         FROM notifications WHERE notification_type IN (` + placeholders + `) AND ` + envCond + ` ORDER BY created_at DESC LIMIT ?`
		for _, t := range types {
			args = append(args, strings.TrimSpace(t))
		}
		args = append(args, env, limit)
	} else if unreadOnly {
		query = `SELECT id, park_id, notification_type, title, message, reference_id, reference_url, reference_data, is_read, created_at
		         FROM notifications WHERE is_read = 0 AND ` + envCond + ` ORDER BY created_at DESC LIMIT ?`
		args = []interface{}{env, limit}
	} else {
		query = `SELECT id, park_id, notification_type, title, message, reference_id, reference_url, reference_data, is_read, created_at
		         FROM notifications WHERE ` + envCond + ` ORDER BY created_at DESC LIMIT ?`
		args = []interface{}{env, limit}
	}

	rows, err := s.DB.Query(query, args...)
	if err != nil {
		internalError(w, "request failed", err)
		return
	}
	defer rows.Close()

	notifications := []Notification{}
	for rows.Next() {
		var n Notification
		var message, refID, refURL, refData sql.NullString
		var isRead int
		var createdAt string

		err := rows.Scan(&n.ID, &n.ParkID, &n.NotificationType, &n.Title,
			&message, &refID, &refURL, &refData, &isRead, &createdAt)
		if err != nil {
			continue
		}

		n.Message = message.String
		n.ReferenceID = refID.String
		n.ReferenceURL = refURL.String
		n.IsRead = isRead == 1
		
		// Parse reference_data JSON if present
		if refData.Valid && refData.String != "" {
			var data map[string]interface{}
			if json.Unmarshal([]byte(refData.String), &data) == nil {
				n.ReferenceData = data
			}
		}
		
		// Try multiple time formats
		if t, err := time.Parse("2006-01-02 15:04:05", createdAt); err == nil {
			n.CreatedAt = t
		} else if t, err := time.Parse(time.RFC3339, createdAt); err == nil {
			n.CreatedAt = t
		}

		notifications = append(notifications, n)
	}

	// Get unread count
	var unreadCount int
	s.DB.QueryRow("SELECT COUNT(*) FROM notifications WHERE is_read = 0 AND "+envCond, env).Scan(&unreadCount)

	response := map[string]interface{}{
		"notifications": notifications,
		"unread_count":  unreadCount,
		"total":         len(notifications),
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(response)
}

// HandleMarkNotificationRead marks a notification as read
// POST /api/notifications/{id}/read
func (s *Server) HandleMarkNotificationRead(w http.ResponseWriter, r *http.Request) {
	idStr := r.PathValue("id")
	if idStr == "" {
		http.Error(w, "missing notification id", http.StatusBadRequest)
		return
	}

	id, err := strconv.ParseInt(idStr, 10, 64)
	if err != nil {
		http.Error(w, "invalid notification id", http.StatusBadRequest)
		return
	}

	_, err = s.DB.Exec("UPDATE notifications SET is_read = 1 WHERE id = ?", id)
	if err != nil {
		internalError(w, "request failed", err)
		return
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]string{"status": "ok"})
}

// HandleMarkAllNotificationsRead marks all notifications as read
// POST /api/notifications/read-all
func (s *Server) HandleMarkAllNotificationsRead(w http.ResponseWriter, r *http.Request) {
	result, err := s.DB.Exec("UPDATE notifications SET is_read = 1 WHERE is_read = 0")
	if err != nil {
		internalError(w, "request failed", err)
		return
	}

	affected, _ := result.RowsAffected()

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]interface{}{
		"status":   "ok",
		"affected": affected,
	})
}
