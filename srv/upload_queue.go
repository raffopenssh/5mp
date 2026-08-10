package srv

import (
	"bytes"
	"context"
	"database/sql"
	"encoding/json"
	"log/slog"
	"sync"
	"time"

	"srv.exe.dev/db/dbgen"
	"srv.exe.dev/srv/gpx"
)

// UploadQueueProcessor handles background processing of queued GPX uploads
type UploadQueueProcessor struct {
	db       *sql.DB
	server   *Server
	stopChan chan struct{}
	wg       sync.WaitGroup
	running  bool
	mu       sync.Mutex
}

// NewUploadQueueProcessor creates a new upload queue processor
func NewUploadQueueProcessor(db *sql.DB, server *Server) *UploadQueueProcessor {
	return &UploadQueueProcessor{
		db:       db,
		server:   server,
		stopChan: make(chan struct{}),
	}
}

// Start begins background processing of the upload queue
func (p *UploadQueueProcessor) Start() {
	p.mu.Lock()
	if p.running {
		p.mu.Unlock()
		return
	}
	p.running = true
	p.mu.Unlock()

	p.wg.Add(1)
	go p.processLoop()
	slog.Info("upload queue processor started")
}

// Stop halts background processing
func (p *UploadQueueProcessor) Stop() {
	p.mu.Lock()
	if !p.running {
		p.mu.Unlock()
		return
	}
	p.running = false
	p.mu.Unlock()

	close(p.stopChan)
	p.wg.Wait()
	slog.Info("upload queue processor stopped")
}

func (p *UploadQueueProcessor) processLoop() {
	defer p.wg.Done()

	ticker := time.NewTicker(2 * time.Second) // Check queue every 2 seconds
	defer ticker.Stop()

	for {
		select {
		case <-p.stopChan:
			return
		case <-ticker.C:
			p.processNextBatch()
		}
	}
}

func (p *UploadQueueProcessor) processNextBatch() {
	ctx := context.Background()
	q := dbgen.New(p.db)

	// Get pending uploads (process up to 5 at a time)
	items, err := q.GetPendingUploads(ctx, 5)
	if err != nil {
		if err != sql.ErrNoRows {
			slog.Error("failed to get pending uploads", "error", err)
		}
		return
	}

	for _, item := range items {
		select {
		case <-p.stopChan:
			return
		default:
			p.processUpload(ctx, item)
		}
	}
}

func (p *UploadQueueProcessor) processUpload(ctx context.Context, item dbgen.GetPendingUploadsRow) {
	q := dbgen.New(p.db)

	// Mark as processing
	if err := q.MarkUploadProcessing(ctx, item.ID); err != nil {
		slog.Error("failed to mark upload as processing", "id", item.ID, "error", err)
		return
	}

	slog.Info("processing queued upload", "id", item.ID, "filename", item.Filename)

	// Parse GPX from stored content
	reader := bytes.NewReader(item.FileContent)
	gpxData, err := gpx.ParseGPX(reader)
	if err != nil {
		errMsg := "failed to parse GPX: " + err.Error()
		q.MarkUploadFailed(ctx, dbgen.MarkUploadFailedParams{
			ErrorMessage: &errMsg,
			ID:           item.ID,
		})
		return
	}

	// Count points
	var totalPoints int
	for _, track := range gpxData.Tracks {
		for _, seg := range track.Segments {
			totalPoints += len(seg)
		}
	}

	// Split into segments
	segments := gpx.SplitIntoSegments(gpxData, 0)
	segments = gpx.RemoveStraightLineGaps(segments)

	// Calculate total distance
	var totalDistanceKm float64
	for _, seg := range segments {
		if len(seg.Points) >= 2 && seg.DistanceKm >= 0.001 {
			totalDistanceKm += seg.DistanceKm
		}
	}

	// Persist upload using the server's method
	fileHash := item.FileHash

	result, err := p.server.persistUploadWithValidation(
		ctx,
		item.UserID,
		item.UserEmail,
		item.Filename,
		stringOrEmpty(fileHash),
		segments,
		item.Env,
	)
	if err != nil {
		errMsg := "failed to persist upload: " + err.Error()
		q.MarkUploadFailed(ctx, dbgen.MarkUploadFailedParams{
			ErrorMessage: &errMsg,
			ID:           item.ID,
		})
		return
	}

	// Build response JSON
	response := map[string]interface{}{
		"status":         "processed",
		"total_points":   totalPoints,
		"total_distance": totalDistanceKm,
		"segments_count": len(segments),
		"validation":     result,
	}
	responseJSON, _ := json.Marshal(response)

	// Mark as completed (no upload ID available from this method)
	resultJsonStr := string(responseJSON)
	q.MarkUploadCompleted(ctx, dbgen.MarkUploadCompletedParams{
		ResultUploadID: nil,
		ResultJson:     &resultJsonStr,
		ID:             item.ID,
	})

	slog.Info("completed queued upload", "id", item.ID, "distance_km", totalDistanceKm)
}

func stringOrEmpty(s *string) string {
	if s == nil {
		return ""
	}
	return *s
}
