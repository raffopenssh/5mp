package srv

import (
	"database/sql"
	"encoding/json"
	"net/http"
	"strconv"
	"time"
)

// Notification represents a notification record
type Notification struct {
	ID               int64     `json:"id"`
	ParkID           string    `json:"park_id"`
	NotificationType string    `json:"notification_type"`
	Title            string    `json:"title"`
	Message          string    `json:"message,omitempty"`
	ReferenceID      string    `json:"reference_id,omitempty"`
	ReferenceURL     string    `json:"reference_url,omitempty"`
	IsRead           bool      `json:"is_read"`
	CreatedAt        time.Time `json:"created_at"`
}

// HandleGetNotifications returns unread or filtered notifications
// GET /api/notifications?limit=50&park_id=XXX&type=new_publication
func (s *Server) HandleGetNotifications(w http.ResponseWriter, r *http.Request) {
	limit := 50
	if l := r.URL.Query().Get("limit"); l != "" {
		if parsed, err := strconv.Atoi(l); err == nil && parsed > 0 && parsed <= 200 {
			limit = parsed
		}
	}

	parkID := r.URL.Query().Get("park_id")
	notifType := r.URL.Query().Get("type")
	unreadOnly := r.URL.Query().Get("unread") == "true" || r.URL.Query().Get("unread") == "1"

	var query string
	var args []interface{}

	if parkID != "" {
		query = `SELECT id, park_id, notification_type, title, message, reference_id, reference_url, is_read, created_at
		         FROM notifications WHERE park_id = ? ORDER BY created_at DESC LIMIT ?`
		args = []interface{}{parkID, limit}
	} else if notifType != "" {
		query = `SELECT id, park_id, notification_type, title, message, reference_id, reference_url, is_read, created_at
		         FROM notifications WHERE notification_type = ? ORDER BY created_at DESC LIMIT ?`
		args = []interface{}{notifType, limit}
	} else if unreadOnly {
		query = `SELECT id, park_id, notification_type, title, message, reference_id, reference_url, is_read, created_at
		         FROM notifications WHERE is_read = 0 ORDER BY created_at DESC LIMIT ?`
		args = []interface{}{limit}
	} else {
		query = `SELECT id, park_id, notification_type, title, message, reference_id, reference_url, is_read, created_at
		         FROM notifications ORDER BY created_at DESC LIMIT ?`
		args = []interface{}{limit}
	}

	rows, err := s.DB.Query(query, args...)
	if err != nil {
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
	}
	defer rows.Close()

	notifications := []Notification{}
	for rows.Next() {
		var n Notification
		var message, refID, refURL sql.NullString
		var isRead int
		var createdAt string

		err := rows.Scan(&n.ID, &n.ParkID, &n.NotificationType, &n.Title,
			&message, &refID, &refURL, &isRead, &createdAt)
		if err != nil {
			continue
		}

		n.Message = message.String
		n.ReferenceID = refID.String
		n.ReferenceURL = refURL.String
		n.IsRead = isRead == 1
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
	s.DB.QueryRow("SELECT COUNT(*) FROM notifications WHERE is_read = 0").Scan(&unreadCount)

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
		http.Error(w, err.Error(), http.StatusInternalServerError)
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
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
	}

	affected, _ := result.RowsAffected()

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]interface{}{
		"status":   "ok",
		"affected": affected,
	})
}
