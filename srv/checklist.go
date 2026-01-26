package srv

import (
	"encoding/json"
	"net/http"
	"os"

	"srv.exe.dev/db/dbgen"
)

// ChecklistSchema represents the full checklist structure
type ChecklistSchema struct {
	Categories []ChecklistCategory `json:"categories"`
}

type ChecklistCategory struct {
	ID    string          `json:"id"`
	Name  string          `json:"name"`
	Items []ChecklistItem `json:"items"`
}

type ChecklistItem struct {
	ID   string `json:"id"`
	Name string `json:"name"`
}

var checklistSchema *ChecklistSchema

func init() {
	// Load checklist schema
	data, err := os.ReadFile("data/park_checklist_schema.json")
	if err == nil {
		json.Unmarshal(data, &checklistSchema)
	}
}

// HandleAPIChecklistSchema returns the checklist schema
func (s *Server) HandleAPIChecklistSchema(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	if checklistSchema == nil {
		json.NewEncoder(w).Encode(map[string]string{"error": "schema not loaded"})
		return
	}
	json.NewEncoder(w).Encode(checklistSchema)
}

// HandleAPIGetParkChecklist returns checklist status for a park
func (s *Server) HandleAPIGetParkChecklist(w http.ResponseWriter, r *http.Request) {
	paID := r.PathValue("id")
	if paID == "" {
		http.Error(w, "park ID required", http.StatusBadRequest)
		return
	}

	q := dbgen.New(s.DB)
	items, err := q.GetParkChecklistItems(r.Context(), paID)
	if err != nil {
		http.Error(w, "database error", http.StatusInternalServerError)
		return
	}

	// Build response with schema and status
	itemStatus := make(map[string]map[string]interface{})
	for _, item := range items {
		itemStatus[item.ItemID] = map[string]interface{}{
			"status":       item.Status,
			"notes":        item.Notes,
			"document_url": item.DocumentUrl,
			"updated_at":   item.UpdatedAt,
		}
	}

	response := map[string]interface{}{
		"pa_id":    paID,
		"schema":   checklistSchema,
		"items":    itemStatus,
	}

	// Get stats
	stats, err := q.GetChecklistStats(r.Context(), paID)
	if err == nil {
		response["stats"] = map[string]interface{}{
			"total":       stats.Total,
			"complete":    stats.Complete,
			"in_progress": stats.InProgress,
			"pending":     stats.Pending,
		}
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(response)
}

// HandleAPIUpdateChecklistItem updates a checklist item status
func (s *Server) HandleAPIUpdateChecklistItem(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}

	user := s.Auth.GetUserFromRequest(r)
	if user == nil {
		http.Error(w, "unauthorized", http.StatusUnauthorized)
		return
	}

	var req struct {
		PAID        string `json:"pa_id"`
		ItemID      string `json:"item_id"`
		Status      string `json:"status"`
		Notes       string `json:"notes"`
		DocumentURL string `json:"document_url"`
	}

	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, "invalid request", http.StatusBadRequest)
		return
	}

	q := dbgen.New(s.DB)
	err := q.UpsertChecklistItem(r.Context(), dbgen.UpsertChecklistItemParams{
		PaID:        req.PAID,
		ItemID:      req.ItemID,
		Status:      req.Status,
		Notes:       &req.Notes,
		DocumentUrl: &req.DocumentURL,
		UpdatedBy:   &user.Email,
	})

	if err != nil {
		http.Error(w, "database error", http.StatusInternalServerError)
		return
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]string{"status": "ok"})
}
