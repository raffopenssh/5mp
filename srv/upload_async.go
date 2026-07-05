package srv

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"io"
	"net/http"
	"strconv"

	"srv.exe.dev/db/dbgen"
)

// AsyncUploadResponse is the immediate response for queued uploads
type AsyncUploadResponse struct {
	QueueID int64  `json:"queue_id"`
	Status  string `json:"status"`
	Message string `json:"message"`
}

// UploadStatusResponse is the response for checking upload status
type UploadStatusResponse struct {
	QueueID       int64                  `json:"queue_id"`
	Status        string                 `json:"status"`
	ErrorMessage  string                 `json:"error_message,omitempty"`
	UploadID      int64                  `json:"upload_id,omitempty"`
	Result        map[string]interface{} `json:"result,omitempty"`
	CreatedAt     string                 `json:"created_at"`
	CompletedAt   string                 `json:"completed_at,omitempty"`
}

// HandleAsyncUpload handles POST requests for async GPX file uploads.
// Returns immediately with a queue ID, processing happens in background.
func (s *Server) HandleAsyncUpload(w http.ResponseWriter, r *http.Request) {
	// Get user from session (optional - allow anonymous uploads)
	user := s.Auth.GetUserFromRequest(r)
	var userID string = "anonymous"
	var userEmail string = "anonymous"
	if user != nil {
		userID = user.ID
		userEmail = user.Email
	}

	// Limit request body size
	r.Body = http.MaxBytesReader(w, r.Body, maxUploadSize)

	// Parse multipart form
	if err := r.ParseMultipartForm(maxUploadSize); err != nil {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusBadRequest)
		json.NewEncoder(w).Encode(map[string]string{
			"error": "failed to parse form: " + err.Error(),
		})
		return
	}
	defer r.MultipartForm.RemoveAll()

	// Get uploaded files
	files := r.MultipartForm.File["gpx"]
	if len(files) == 0 {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusBadRequest)
		json.NewEncoder(w).Encode(map[string]string{
			"error": "no GPX files provided",
		})
		return
	}

	ctx := r.Context()
	q := dbgen.New(s.DB)

	// For simplicity, handle single file upload (can extend for multiple)
	fileHeader := files[0]
	file, err := fileHeader.Open()
	if err != nil {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusBadRequest)
		json.NewEncoder(w).Encode(map[string]string{
			"error": "failed to open file: " + err.Error(),
		})
		return
	}
	defer file.Close()

	// Read file content
	content, err := io.ReadAll(file)
	if err != nil {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusBadRequest)
		json.NewEncoder(w).Encode(map[string]string{
			"error": "failed to read file: " + err.Error(),
		})
		return
	}

	// Calculate hash for deduplication
	hasher := sha256.New()
	hasher.Write(content)
	fileHash := hex.EncodeToString(hasher.Sum(nil))

	// Check for duplicate in queue
	env := RequestEnv(r)
	existing, err := q.GetUploadQueueByHash(ctx, dbgen.GetUploadQueueByHashParams{FileHash: &fileHash, Env: env})
	if err == nil && existing.ID > 0 {
		// Return existing queue item
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(AsyncUploadResponse{
			QueueID: existing.ID,
			Status:  existing.Status,
			Message: "File already queued or processed",
		})
		return
	}

	// Check for duplicate in completed uploads
	existingUpload, err := q.GetGPXUploadByHash(ctx, dbgen.GetGPXUploadByHashParams{FileHash: &fileHash, Env: env})
	if err == nil && existingUpload.ID > 0 {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusConflict)
		json.NewEncoder(w).Encode(map[string]interface{}{
			"error":   "duplicate_upload",
			"message": "This file has already been uploaded",
			"previous_upload": map[string]interface{}{
				"id":          existingUpload.ID,
				"filename":    existingUpload.Filename,
				"upload_date": existingUpload.UploadDate,
				"distance_km": existingUpload.TotalDistanceKm,
			},
		})
		return
	}

	// Queue the upload
	queueItem, err := q.QueueUpload(ctx, dbgen.QueueUploadParams{
		UserID:      userID,
		UserEmail:   userEmail,
		Filename:    fileHeader.Filename,
		FileHash:    &fileHash,
		FileContent: content,
		Env:         env,
	})
	if err != nil {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusInternalServerError)
		json.NewEncoder(w).Encode(map[string]string{
			"error": "failed to queue upload: " + err.Error(),
		})
		return
	}

	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusAccepted)
	json.NewEncoder(w).Encode(AsyncUploadResponse{
		QueueID: queueItem.ID,
		Status:  "pending",
		Message: "Upload queued for processing. Check status at /api/upload/status/" + strconv.FormatInt(queueItem.ID, 10),
	})
}

// HandleUploadStatus returns the status of a queued upload
func (s *Server) HandleUploadStatus(w http.ResponseWriter, r *http.Request) {
	idStr := r.PathValue("id")
	id, err := strconv.ParseInt(idStr, 10, 64)
	if err != nil {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusBadRequest)
		json.NewEncoder(w).Encode(map[string]string{
			"error": "invalid queue ID",
		})
		return
	}

	ctx := r.Context()
	q := dbgen.New(s.DB)

	status, err := q.GetUploadQueueStatus(ctx, id)
	if err != nil {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusNotFound)
		json.NewEncoder(w).Encode(map[string]string{
			"error": "queue item not found",
		})
		return
	}

	response := UploadStatusResponse{
		QueueID:   status.ID,
		Status:    status.Status,
		CreatedAt: status.CreatedAt.String(),
	}

	if status.ErrorMessage != nil {
		response.ErrorMessage = *status.ErrorMessage
	}

	if status.ResultUploadID != nil {
		response.UploadID = *status.ResultUploadID
	}

	if status.CompletedAt != nil {
		response.CompletedAt = status.CompletedAt.String()
	}

	if status.ResultJson != nil {
		var result map[string]interface{}
		if err := json.Unmarshal([]byte(*status.ResultJson), &result); err == nil {
			response.Result = result
		}
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(response)
}
