package srv

import (
	"encoding/json"
	"log/slog"
	"net/http"

	"srv.exe.dev/db/dbgen"
)

// Document category constants
const (
	DocCategoryManagementPlan = "management_plan"
	DocCategoryAnnualReport   = "annual_report"
	DocCategoryResearchReport = "research_report"
	DocCategoryLegalDocument  = "legal_document"
	DocCategoryOther          = "other"
)

// DocumentResponse represents a park document in API responses
type DocumentResponse struct {
	ID          int64   `json:"id"`
	PaID        string  `json:"pa_id"`
	Category    string  `json:"category"`
	Title       string  `json:"title"`
	Description *string `json:"description,omitempty"`
	URL         *string `json:"url,omitempty"`
	FileType    *string `json:"file_type,omitempty"`
	Year        *int64  `json:"year,omitempty"`
	Summary     *string `json:"summary,omitempty"`
}

// HandleAPIParkDocuments returns all documents for a protected area.
// GET /api/parks/{id}/documents
// Optional query param: category (management_plan, annual_report, etc)
func (s *Server) HandleAPIParkDocuments(w http.ResponseWriter, r *http.Request) {
	paID := r.PathValue("id")
	if paID == "" {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusBadRequest)
		json.NewEncoder(w).Encode(map[string]string{"error": "missing park ID"})
		return
	}

	ctx := r.Context()
	q := dbgen.New(s.DB)

	category := r.URL.Query().Get("category")

	var docs []dbgen.ParkDocument
	var err error

	if category != "" {
		docs, err = q.GetParkDocumentsByCategory(ctx, dbgen.GetParkDocumentsByCategoryParams{
			PaID:     paID,
			Category: category,
		})
	} else {
		docs, err = q.GetAllParkDocuments(ctx, paID)
	}

	if err != nil {
		slog.Error("failed to get park documents", "pa_id", paID, "error", err)
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusInternalServerError)
		json.NewEncoder(w).Encode(map[string]string{"error": "database error"})
		return
	}

	// Transform to API response
	results := make([]DocumentResponse, 0, len(docs))
	for _, d := range docs {
		results = append(results, DocumentResponse{
			ID:          d.ID,
			PaID:        d.PaID,
			Category:    d.Category,
			Title:       d.Title,
			Description: d.Description,
			URL:         d.FileUrl,
			FileType:    d.FileType,
			Year:        d.Year,
			Summary:     d.Summary,
		})
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]interface{}{
		"pa_id":     paID,
		"documents": results,
		"count":     len(results),
	})
}

// HandleAPIParkManagementPlans returns management plans for a protected area.
// GET /api/parks/{id}/management-plans
// This is a convenience endpoint that filters by management_plan category.
func (s *Server) HandleAPIParkManagementPlans(w http.ResponseWriter, r *http.Request) {
	paID := r.PathValue("id")
	if paID == "" {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusBadRequest)
		json.NewEncoder(w).Encode(map[string]string{"error": "missing park ID"})
		return
	}

	ctx := r.Context()
	q := dbgen.New(s.DB)

	docs, err := q.GetParkDocumentsByCategory(ctx, dbgen.GetParkDocumentsByCategoryParams{
		PaID:     paID,
		Category: DocCategoryManagementPlan,
	})
	if err != nil {
		slog.Error("failed to get management plans", "pa_id", paID, "error", err)
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusInternalServerError)
		json.NewEncoder(w).Encode(map[string]string{"error": "database error"})
		return
	}

	// Transform to API response
	results := make([]DocumentResponse, 0, len(docs))
	for _, d := range docs {
		results = append(results, DocumentResponse{
			ID:          d.ID,
			PaID:        d.PaID,
			Category:    d.Category,
			Title:       d.Title,
			Description: d.Description,
			URL:         d.FileUrl,
			FileType:    d.FileType,
			Year:        d.Year,
			Summary:     d.Summary,
		})
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]interface{}{
		"pa_id":            paID,
		"management_plans": results,
		"count":            len(results),
	})
}
