package srv

import (
	"bytes"
	"context"
	"database/sql"
	"encoding/json"
	"fmt"
	"image"
	"image/jpeg"
	_ "image/png"
	"io"
	"log/slog"
	"math"
	"net/http"
	"os"
	"path/filepath"
	"runtime"
	"sort"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"syscall"
	"time"

	"srv.exe.dev/srv/areas"
)

// TileSource represents a satellite imagery source
type TileSource struct {
	Name      string
	URLFormat string // {z}/{x}/{y} pattern
	// MaxZoom is the deepest level the SERVER has. Requests beyond it are
	// answered by upscaling the ancestor tile at MaxZoom (see downloadTile),
	// so an offline map can be zoomed in on a 10 m image without holes —
	// the pixels get bigger, no detail is invented.
	MaxZoom int
	// UpscaleTo is the deepest level the builder will fabricate by upscaling.
	UpscaleTo   int
	Headers     map[string]string
	Attribution string // the credit line the publisher prescribes, verbatim
	Licence     string
	LicenceURL  string
}

// Available tile sources.
//
// ONLY IMAGERY WE MAY COPY. This builder downloads tiles in bulk and hands
// them to the user as a file, which is redistribution. Google Maps, Bing Maps
// and Esri World Imagery all forbid bulk download and offline caching in their
// terms of use, so they were removed here (2026-09). EOX's Sentinel-2 cloudless
// is CC BY-NC-SA 4.0: copying and redistribution are permitted for
// non-commercial use with the attribution below, which is written into every
// MBTiles' metadata. The register in license.go is the source of truth.
var TileSources = map[string]TileSource{
	"s2cloudless": {
		Name:        "Sentinel-2 cloudless 2024 (EOX)",
		URLFormat:   "https://tiles.maps.eox.at/wmts/1.0.0/s2cloudless-2024_3857/default/g/{z}/{y}/{x}.jpg",
		MaxZoom:     18,
		UpscaleTo:   18, // equal to MaxZoom: upscaling available but not offered
		Headers:     map[string]string{"User-Agent": "5MP Conservation Monitoring (https://github.com/raffopenssh/5mp)"},
		Attribution: "Sentinel-2 cloudless - https://s2maps.eu by EOX IT Services GmbH (Contains modified Copernicus Sentinel data 2024)",
		Licence:     "CC BY-NC-SA 4.0",
		LicenceURL:  "https://creativecommons.org/licenses/by-nc-sa/4.0/",
	},
}

// defaultTileSource is the source used when the request names none.
const defaultTileSource = "s2cloudless"

// tileSourceNames lists the accepted source keys, for error messages and the
// estimate endpoints. Derived, never typed (invariant 2).
func tileSourceNames() []string {
	names := make([]string, 0, len(TileSources))
	for k := range TileSources {
		names = append(names, k)
	}
	sort.Strings(names)
	return names
}

// maxBuildZoom is the deepest zoom any source can deliver, natively or by
// upscaling. The UI slider and the handlers clamp to it.
func maxBuildZoom() int {
	m := 0
	for _, src := range TileSources {
		z := src.MaxZoom
		if src.UpscaleTo > z {
			z = src.UpscaleTo
		}
		if z > m {
			m = z
		}
	}
	return m
}

// MBTilesJob represents a tile generation job
type MBTilesJob struct {
	ID              string     `json:"id"`
	ParkID          string     `json:"park_id"`
	ParkName        string     `json:"park_name"`
	Source          string     `json:"source"`
	MinZoom         int        `json:"min_zoom"`
	MaxZoom         int        `json:"max_zoom"`
	BufferKm        float64    `json:"buffer_km"`
	BBox            [4]float64 `json:"bbox"`   // [minLon, minLat, maxLon, maxLat]
	Status          string     `json:"status"` // pending, processing, completed, failed
	Progress        float64    `json:"progress"`
	TotalTiles      int64      `json:"total_tiles"`
	DownloadedTiles int64      `json:"downloaded_tiles"`
	EstimatedSize   int64      `json:"estimated_size_bytes"`
	FilePath        string     `json:"file_path"`
	Error           string     `json:"error,omitempty"`
	CreatedAt       time.Time  `json:"created_at"`
	CompletedAt     *time.Time `json:"completed_at,omitempty"`
	UserID          string     `json:"user_id,omitempty"`
	Env             string     `json:"env,omitempty"` // tenant (RequestEnv) — scopes notifications
}

// MBTilesQueue manages tile generation jobs
type MBTilesQueue struct {
	jobs       map[string]*MBTilesJob
	mu         sync.RWMutex
	processing atomic.Bool
	ctx        context.Context
	cancel     context.CancelFunc
	outputDir  string
	maxCPU     int // Max concurrent downloads
	db         interface {
		Exec(string, ...interface{}) (sql.Result, error)
	}
}

var mbtilesQueue *MBTilesQueue

// InitMBTilesQueue initializes the MBTiles generation queue
func InitMBTilesQueue(outputDir string, db interface {
	Exec(string, ...interface{}) (sql.Result, error)
}) {
	ctx, cancel := context.WithCancel(context.Background())
	mbtilesQueue = &MBTilesQueue{
		jobs:      make(map[string]*MBTilesJob),
		ctx:       ctx,
		cancel:    cancel,
		outputDir: outputDir,
		maxCPU:    runtime.NumCPU() / 2, // Use half of available CPUs
		db:        db,
	}
	if mbtilesQueue.maxCPU < 1 {
		mbtilesQueue.maxCPU = 1
	}

	// Create output directory
	os.MkdirAll(outputDir, 0755)

	// Start the processor
	go mbtilesQueue.processJobs()

	slog.Info("MBTiles queue initialized", "outputDir", outputDir, "maxCPU", mbtilesQueue.maxCPU)
}

// AddJob adds a new job to the queue
func (q *MBTilesQueue) AddJob(job *MBTilesJob) error {
	q.mu.Lock()
	defer q.mu.Unlock()

	// Check available disk space (require 1.2x estimated size)
	availableSpace := getAvailableDiskSpace(q.outputDir)
	requiredSpace := uint64(float64(job.EstimatedSize) * 1.2)
	if job.EstimatedSize > 0 && availableSpace < requiredSpace {
		return fmt.Errorf("insufficient disk space: need %d bytes, have %d", requiredSpace, availableSpace)
	}

	job.Status = "pending"
	job.CreatedAt = time.Now()
	q.jobs[job.ID] = job

	return nil
}

// GetJob returns a job by ID
func (q *MBTilesQueue) GetJob(id string) *MBTilesJob {
	q.mu.RLock()
	defer q.mu.RUnlock()
	return q.jobs[id]
}

// ListJobs returns all jobs
func (q *MBTilesQueue) ListJobs() []*MBTilesJob {
	q.mu.RLock()
	defer q.mu.RUnlock()

	jobs := make([]*MBTilesJob, 0, len(q.jobs))
	for _, job := range q.jobs {
		jobs = append(jobs, job)
	}
	return jobs
}

// processJobs processes pending jobs one at a time
func (q *MBTilesQueue) processJobs() {
	ticker := time.NewTicker(5 * time.Second)
	defer ticker.Stop()

	for {
		select {
		case <-q.ctx.Done():
			return
		case <-ticker.C:
			if q.processing.Load() {
				continue
			}

			// Find next pending job
			q.mu.Lock()
			var nextJob *MBTilesJob
			for _, job := range q.jobs {
				if job.Status == "pending" {
					nextJob = job
					break
				}
			}
			q.mu.Unlock()

			if nextJob != nil {
				q.processing.Store(true)
				q.executeJob(nextJob)
				q.processing.Store(false)
			}
		}
	}
}

// executeJob processes a single job
func (q *MBTilesQueue) executeJob(job *MBTilesJob) {
	slog.Info("Starting MBTiles job", "id", job.ID, "park", job.ParkID, "source", job.Source)

	q.mu.Lock()
	job.Status = "processing"
	q.mu.Unlock()

	// Create MBTiles file
	outputPath := filepath.Join(q.outputDir, fmt.Sprintf("%s_%s_%s.mbtiles", job.ParkID, job.Source, job.ID))
	job.FilePath = outputPath

	err := q.generateMBTiles(job, outputPath)

	q.mu.Lock()
	if err != nil {
		job.Status = "failed"
		job.Error = err.Error()
		slog.Error("MBTiles job failed", "id", job.ID, "error", err)
		// Create failure notification
		q.createNotification(job.ParkID, "mbtiles_failed",
			fmt.Sprintf("MBTiles Failed: %s", job.ParkName),
			fmt.Sprintf("Tile generation for %s failed: %s", job.ParkName, err.Error()),
			"", job.Env)
	} else {
		job.Status = "completed"
		now := time.Now()
		job.CompletedAt = &now
		slog.Info("MBTiles job completed", "id", job.ID, "path", outputPath)
		// Create success notification
		fileSizeMB := job.EstimatedSize / (1024 * 1024)
		q.createNotification(job.ParkID, "mbtiles_complete",
			fmt.Sprintf("MBTiles Ready: %s", job.ParkName),
			fmt.Sprintf("Offline tiles for %s (%s, %d MB) ready for download", job.ParkName, job.Source, fileSizeMB),
			fmt.Sprintf("/api/parks/%s/mbtiles/download/%s", job.ParkID, job.ID), job.Env)

		// Schedule cleanup of completed job and file (keep for 2 hours max)
		go func(jobID, filePath string) {
			time.Sleep(2 * time.Hour)
			q.mu.Lock()
			delete(q.jobs, jobID)
			q.mu.Unlock()
			os.Remove(filePath)
			slog.Info("Auto-cleaned MBTiles file after 2 hours", "id", jobID, "path", filePath)
		}(job.ID, outputPath)
	}
	q.mu.Unlock()
}

// createNotification creates a notification for MBTiles events
func (q *MBTilesQueue) createNotification(parkID, notifType, title, message, link, env string) {
	if q.db == nil {
		return
	}
	if env == "" {
		env = clientTenant
	}
	_, err := q.db.Exec(`
		INSERT INTO notifications (park_id, notification_type, title, message, reference_url, env, created_at)
		VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
	`, parkID, notifType, title, message, link, env)
	if err != nil {
		slog.Warn("Failed to create MBTiles notification", "error", err)
	}
}

// generateMBTiles creates the MBTiles file
func (q *MBTilesQueue) generateMBTiles(job *MBTilesJob, outputPath string) error {
	source, ok := TileSources[job.Source]
	if !ok {
		return fmt.Errorf("unknown tile source: %s", job.Source)
	}

	// Create SQLite database with MBTiles schema
	db, err := sql.Open("sqlite", outputPath)
	if err != nil {
		return fmt.Errorf("failed to create database: %w", err)
	}
	defer db.Close()

	// Initialize MBTiles schema
	if err := initMBTilesSchema(db, job, source); err != nil {
		return fmt.Errorf("failed to init schema: %w", err)
	}

	// Calculate tiles to download
	tiles := calculateTiles(job.BBox, job.MinZoom, job.MaxZoom)
	job.TotalTiles = int64(len(tiles))

	slog.Info("Downloading tiles", "total", job.TotalTiles, "source", source.Name)

	// Download tiles with concurrency limit
	semaphore := make(chan struct{}, q.maxCPU)
	var wg sync.WaitGroup
	var downloaded atomic.Int64
	var errors atomic.Int64

	// Prepare insert statement
	insertStmt, err := db.Prepare("INSERT OR REPLACE INTO tiles (zoom_level, tile_column, tile_row, tile_data) VALUES (?, ?, ?, ?)")
	if err != nil {
		return fmt.Errorf("failed to prepare statement: %w", err)
	}
	defer insertStmt.Close()

	var insertMu sync.Mutex

	for _, tile := range tiles {
		select {
		case <-q.ctx.Done():
			return fmt.Errorf("job cancelled")
		default:
		}

		wg.Add(1)
		semaphore <- struct{}{}

		go func(t Tile) {
			defer wg.Done()
			defer func() { <-semaphore }()

			data, err := downloadTile(source, t)
			if err != nil {
				errors.Add(1)
				return
			}

			// MBTiles uses TMS scheme (y-flipped)
			tmsY := (1 << t.Z) - 1 - t.Y

			insertMu.Lock()
			insertStmt.Exec(t.Z, t.X, tmsY, data)
			insertMu.Unlock()

			count := downloaded.Add(1)
			job.DownloadedTiles = count
			job.Progress = float64(count) / float64(job.TotalTiles) * 100
		}(tile)
	}

	wg.Wait()

	if errors.Load() > job.TotalTiles/2 {
		return fmt.Errorf("too many download errors: %d/%d failed", errors.Load(), job.TotalTiles)
	}

	// Get file size
	if info, err := os.Stat(outputPath); err == nil {
		job.EstimatedSize = info.Size()
	}

	return nil
}

// Tile represents a map tile coordinate
type Tile struct {
	X, Y, Z int
}

// calculateTiles returns all tiles within bbox for given zoom range
func calculateTiles(bbox [4]float64, minZoom, maxZoom int) []Tile {
	var tiles []Tile

	for z := minZoom; z <= maxZoom; z++ {
		minX, minY := lonLatToTile(bbox[0], bbox[3], z) // top-left
		maxX, maxY := lonLatToTile(bbox[2], bbox[1], z) // bottom-right

		for x := minX; x <= maxX; x++ {
			for y := minY; y <= maxY; y++ {
				tiles = append(tiles, Tile{X: x, Y: y, Z: z})
			}
		}
	}

	return tiles
}

// lonLatToTile converts lon/lat to tile coordinates
func lonLatToTile(lon, lat float64, zoom int) (int, int) {
	n := math.Pow(2, float64(zoom))
	x := int((lon + 180.0) / 360.0 * n)
	latRad := lat * math.Pi / 180.0
	y := int((1.0 - math.Log(math.Tan(latRad)+1.0/math.Cos(latRad))/math.Pi) / 2.0 * n)

	// Clamp to valid range
	if x < 0 {
		x = 0
	}
	if x >= int(n) {
		x = int(n) - 1
	}
	if y < 0 {
		y = 0
	}
	if y >= int(n) {
		y = int(n) - 1
	}

	return x, y
}

// tileToQuadKey converts tile coordinates to Bing quadkey
func tileToQuadKey(x, y, z int) string {
	quadKey := make([]byte, z)
	for i := z; i > 0; i-- {
		digit := '0'
		mask := 1 << (i - 1)
		if (x & mask) != 0 {
			digit++
		}
		if (y & mask) != 0 {
			digit += 2
		}
		quadKey[z-i] = byte(digit)
	}
	return string(quadKey)
}

// downloadTile fetches one tile. Beyond the source's native MaxZoom it fetches
// the ancestor at MaxZoom and crops+upscales the matching quadrant, so a z20
// tile is a 4x-magnified quarter of a z18 tile. The result is re-encoded as
// JPEG so the MBTiles stays one format.
func downloadTile(source TileSource, tile Tile) ([]byte, error) {
	if source.MaxZoom > 0 && tile.Z > source.MaxZoom {
		if source.UpscaleTo > 0 && tile.Z > source.UpscaleTo {
			return nil, fmt.Errorf("zoom %d beyond upscale limit %d", tile.Z, source.UpscaleTo)
		}
		return upscaledTile(source, tile)
	}
	return fetchTile(source, tile)
}

// fetchTile performs the HTTP GET for a tile the server actually has.
func fetchTile(source TileSource, tile Tile) ([]byte, error) {
	url := source.URLFormat
	url = replaceAll(url, "{z}", fmt.Sprintf("%d", tile.Z))
	url = replaceAll(url, "{x}", fmt.Sprintf("%d", tile.X))
	url = replaceAll(url, "{y}", fmt.Sprintf("%d", tile.Y))
	url = replaceAll(url, "{s}", fmt.Sprintf("%d", tile.X%4))

	req, err := http.NewRequest("GET", url, nil)
	if err != nil {
		return nil, err
	}
	for k, v := range source.Headers {
		req.Header.Set(k, v)
	}
	client := &http.Client{Timeout: 30 * time.Second}
	resp, err := client.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("HTTP %d", resp.StatusCode)
	}
	return io.ReadAll(resp.Body)
}

// upscaledTile builds tile (Z > MaxZoom) from its ancestor at MaxZoom.
// Ancestors are memoised per build in ancestorCache so the 4^(Z-MaxZoom)
// descendants of one native tile cost one HTTP request, not 4^n.
func upscaledTile(source TileSource, tile Tile) ([]byte, error) {
	dz := tile.Z - source.MaxZoom
	ax, ay := tile.X>>dz, tile.Y>>dz
	anc := Tile{Z: source.MaxZoom, X: ax, Y: ay}

	img, err := ancestorImage(source, anc)
	if err != nil {
		return nil, err
	}
	// Quadrant of the ancestor this tile covers, in ancestor pixels.
	n := 1 << dz
	b := img.Bounds()
	w := b.Dx() / n
	h := b.Dy() / n
	if w == 0 || h == 0 {
		return nil, fmt.Errorf("upscale factor %d exceeds tile size", n)
	}
	sx := b.Min.X + (tile.X-ax<<dz)*w
	sy := b.Min.Y + (tile.Y-ay<<dz)*h

	out := image.NewRGBA(image.Rect(0, 0, b.Dx(), b.Dy()))
	// Nearest-neighbour: honest about the source resolution (pixels stay
	// visibly pixels rather than a blur pretending to be detail).
	for y := 0; y < b.Dy(); y++ {
		for x := 0; x < b.Dx(); x++ {
			out.Set(x, y, img.At(sx+x/n, sy+y/n))
		}
	}
	var buf bytes.Buffer
	if err := jpeg.Encode(&buf, out, &jpeg.Options{Quality: 85}); err != nil {
		return nil, err
	}
	return buf.Bytes(), nil
}

// ancestorCache holds decoded native tiles for the upscaling path. Bounded:
// a job walks zooms in order so an ancestor is only hot for a while.
var ancestorCache = struct {
	sync.Mutex
	m     map[string]image.Image
	order []string
}{m: map[string]image.Image{}}

const ancestorCacheMax = 512

func ancestorImage(source TileSource, t Tile) (image.Image, error) {
	key := fmt.Sprintf("%s/%d/%d/%d", source.URLFormat, t.Z, t.X, t.Y)
	ancestorCache.Lock()
	if img, ok := ancestorCache.m[key]; ok {
		ancestorCache.Unlock()
		return img, nil
	}
	ancestorCache.Unlock()

	data, err := fetchTile(source, t)
	if err != nil {
		return nil, err
	}
	img, _, err := image.Decode(bytes.NewReader(data))
	if err != nil {
		return nil, fmt.Errorf("decode ancestor tile: %w", err)
	}

	ancestorCache.Lock()
	defer ancestorCache.Unlock()
	if _, ok := ancestorCache.m[key]; !ok {
		ancestorCache.m[key] = img
		ancestorCache.order = append(ancestorCache.order, key)
		for len(ancestorCache.order) > ancestorCacheMax {
			delete(ancestorCache.m, ancestorCache.order[0])
			ancestorCache.order = ancestorCache.order[1:]
		}
	}
	return img, nil
}

// initMBTilesSchema creates the MBTiles SQLite schema
func initMBTilesSchema(db *sql.DB, job *MBTilesJob, source TileSource) error {
	schema := `
		CREATE TABLE IF NOT EXISTS metadata (name TEXT, value TEXT);
		CREATE TABLE IF NOT EXISTS tiles (zoom_level INTEGER, tile_column INTEGER, tile_row INTEGER, tile_data BLOB);
		CREATE UNIQUE INDEX IF NOT EXISTS tile_index ON tiles (zoom_level, tile_column, tile_row);
	`

	if _, err := db.Exec(schema); err != nil {
		return err
	}

	// Insert metadata
	metadata := map[string]string{
		"name":        fmt.Sprintf("%s - %s", job.ParkName, source.Name),
		"type":        "baselayer",
		"version":     "1.0",
		"description": fmt.Sprintf("Satellite imagery for %s from %s", job.ParkName, source.Name),
		"format":      "jpg",
		"bounds":      fmt.Sprintf("%f,%f,%f,%f", job.BBox[0], job.BBox[1], job.BBox[2], job.BBox[3]),
		"minzoom":     fmt.Sprintf("%d", job.MinZoom),
		"maxzoom":     fmt.Sprintf("%d", job.MaxZoom),
		// The publisher's terms travel with the file. Without these lines a
		// downloaded MBTiles is an unattributed copy, which is what the
		// licence forbids.
		"attribution": source.Attribution,
		"licence":     source.Licence,
		"licence_url": source.LicenceURL,
	}
	if job.MaxZoom > source.MaxZoom {
		metadata["upscaled_from_zoom"] = fmt.Sprintf("%d", source.MaxZoom)
		metadata["description"] += fmt.Sprintf(". Native imagery to zoom %d; levels %d-%d are nearest-neighbour upscales of it (no added detail).", source.MaxZoom, source.MaxZoom+1, job.MaxZoom)
	}

	stmt, err := db.Prepare("INSERT INTO metadata (name, value) VALUES (?, ?)")
	if err != nil {
		return err
	}
	defer stmt.Close()

	for k, v := range metadata {
		if _, err := stmt.Exec(k, v); err != nil {
			return err
		}
	}

	return nil
}

// getAvailableDiskSpace returns available disk space in bytes
func getAvailableDiskSpace(path string) uint64 {
	var stat syscall.Statfs_t
	if err := syscall.Statfs(path, &stat); err != nil {
		slog.Warn("failed to get disk space", "path", path, "error", err)
		return 10 * 1024 * 1024 * 1024 // Fallback to 10GB
	}
	// Available blocks * block size
	return stat.Bavail * uint64(stat.Bsize)
}

// estimateMBTilesSize estimates the output file size
func estimateMBTilesSize(bbox [4]float64, minZoom, maxZoom int) int64 {
	tiles := calculateTiles(bbox, minZoom, maxZoom)
	// Estimate ~15KB per tile average for satellite imagery
	estimatedSize := estimateTileBytes(len(tiles))

	// Enforce shared maximum (maxMBTilesSize in mbtiles_zenodo.go)
	if estimatedSize > maxMBTilesSize {
		return maxMBTilesSize + 1 // Return slightly over limit to trigger error
	}

	return estimatedSize
}

// Helper functions
func replaceAll(s, old, new string) string {
	for contains(s, old) {
		idx := indexOf(s, old)
		s = s[:idx] + new + s[idx+len(old):]
	}
	return s
}

func contains(s, substr string) bool {
	return indexOf(s, substr) >= 0
}

func indexOf(s, substr string) int {
	for i := 0; i <= len(s)-len(substr); i++ {
		if s[i:i+len(substr)] == substr {
			return i
		}
	}
	return -1
}

// HTTP Handlers

// HandleAPIMBTilesCreate creates a new MBTiles generation job
func (s *Server) HandleAPIMBTilesCreate(w http.ResponseWriter, r *http.Request) {
	parkID := r.PathValue("id")
	if parkID == "" {
		http.Error(w, "Park ID required", http.StatusBadRequest)
		return
	}

	// Get parameters
	source := r.URL.Query().Get("source")
	if source == "" {
		source = defaultTileSource
	}
	if _, ok := TileSources[source]; !ok {
		http.Error(w, "Invalid source. Use: "+strings.Join(tileSourceNames(), ", "), http.StatusBadRequest)
		return
	}

	// Get park (or AOI) data
	areaName, rawBBox, ok := s.resolveAreaBBox(parkID)
	if !ok {
		http.Error(w, "Park not found", http.StatusNotFound)
		return
	}

	// Calculate bbox with buffer
	bufferKm := 5.0 // 5km buffer
	bbox := bufferBBox(rawBBox, bufferKm)

	// Estimate size
	minZoom := 1
	maxZoom := 17

	// Get maxZoom from query parameter
	if maxZoomStr := r.URL.Query().Get("maxZoom"); maxZoomStr != "" {
		if mz, err := strconv.Atoi(maxZoomStr); err == nil && mz >= 1 && mz <= maxBuildZoom() {
			maxZoom = mz
		}
	}

	estimatedSize := estimateMBTilesSize(bbox, minZoom, maxZoom)

	// Check shared size limit (maxMBTilesSize in mbtiles_zenodo.go)
	if estimatedSize > maxMBTilesSize {
		http.Error(w, fmt.Sprintf("MBTiles too large: estimated %.1f GB, maximum %d GB. Try reducing zoom levels or area size.", float64(estimatedSize)/(1024*1024*1024), maxMBTilesSize/(1024*1024*1024)), http.StatusRequestEntityTooLarge)
		return
	}

	// Check available disk space
	availableSpace := getAvailableDiskSpace(mbtilesQueue.outputDir)
	// Require 1.2x estimated size + 2GB free space minimum
	const minFreeSpace = 2 * 1024 * 1024 * 1024 // 2GB
	requiredSpace := uint64(float64(estimatedSize)*1.2) + minFreeSpace
	if requiredSpace > availableSpace {
		http.Error(w, fmt.Sprintf("Insufficient disk space: need %.1f GB free, only %.1f GB available. Ensure 2 GB remains after generation.",
			float64(requiredSpace)/(1024*1024*1024),
			float64(availableSpace)/(1024*1024*1024)),
			http.StatusInsufficientStorage)
		return
	}

	// Create job
	job := &MBTilesJob{
		ID:            fmt.Sprintf("%d", time.Now().UnixNano()),
		ParkID:        parkID,
		ParkName:      areaName,
		Source:        source,
		MinZoom:       minZoom,
		MaxZoom:       maxZoom,
		BufferKm:      bufferKm,
		BBox:          bbox,
		EstimatedSize: estimatedSize,
		Env:           RequestEnv(r),
	}

	// Estimate completion time (~100 tiles/second)
	tiles := calculateTiles(bbox, minZoom, maxZoom)
	estimatedSeconds := len(tiles) / 100
	if estimatedSeconds < 60 {
		estimatedSeconds = 60
	}

	if err := mbtilesQueue.AddJob(job); err != nil {
		http.Error(w, err.Error(), http.StatusInsufficientStorage)
		return
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]interface{}{
		"job_id":            job.ID,
		"park_id":           parkID,
		"source":            source,
		"total_tiles":       len(tiles),
		"estimated_size_mb": estimatedSize / (1024 * 1024),
		"estimated_seconds": estimatedSeconds,
		"status_url":        fmt.Sprintf("/api/mbtiles/%s/status", job.ID),
		"download_url":      fmt.Sprintf("/api/mbtiles/%s/download", job.ID),
	})
}

// HandleAPIMBTilesStatus returns job status
func (s *Server) HandleAPIMBTilesStatus(w http.ResponseWriter, r *http.Request) {
	jobID := r.PathValue("id")
	job := mbtilesQueue.GetJob(jobID)
	if job == nil {
		http.Error(w, "Job not found", http.StatusNotFound)
		return
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(job)
}

// HandleAPIMBTilesDownload serves the completed MBTiles file
func (s *Server) HandleAPIMBTilesDownload(w http.ResponseWriter, r *http.Request) {
	jobID := r.PathValue("id")
	job := mbtilesQueue.GetJob(jobID)
	if job == nil {
		http.Error(w, "Job not found", http.StatusNotFound)
		return
	}

	if job.Status != "completed" {
		http.Error(w, fmt.Sprintf("Job not ready: %s", job.Status), http.StatusBadRequest)
		return
	}

	if job.FilePath == "" || !fileExists(job.FilePath) {
		http.Error(w, "File not found", http.StatusNotFound)
		return
	}

	// Serve file
	filename := fmt.Sprintf("%s_%s.mbtiles", job.ParkID, job.Source)
	w.Header().Set("Content-Disposition", fmt.Sprintf("attachment; filename=%q", filename))
	w.Header().Set("Content-Type", "application/x-sqlite3")

	filePath := job.FilePath
	// ServeFile gives us Range support and a correct Content-Length. Do NOT
	// delete the file after the first request: a client in a low-bandwidth
	// region resumes with Range requests minutes or hours later, and a
	// one-shot delete turns every resume into a 404. The 2-hour auto-cleanup
	// scheduled at completion reclaims the space.
	http.ServeFile(longDownload(w), r, filePath)
}

// HandleAPIMBTilesList lists all jobs
func (s *Server) HandleAPIMBTilesList(w http.ResponseWriter, r *http.Request) {
	jobs := mbtilesQueue.ListJobs()
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(jobs)
}

// HandleAPIMBTilesEstimate returns size estimate without creating job
func (s *Server) HandleAPIMBTilesEstimate(w http.ResponseWriter, r *http.Request) {
	parkID := r.PathValue("id")
	if parkID == "" {
		http.Error(w, "Park ID required", http.StatusBadRequest)
		return
	}

	_, rawBBox, ok := s.resolveAreaBBox(parkID)
	if !ok {
		http.Error(w, "Park not found", http.StatusNotFound)
		return
	}

	bufferKm := 5.0
	bbox := bufferBBox(rawBBox, bufferKm)

	minZoom := 1
	maxZoom := 17

	// Get maxZoom from query parameter
	if maxZoomStr := r.URL.Query().Get("maxZoom"); maxZoomStr != "" {
		if mz, err := strconv.Atoi(maxZoomStr); err == nil && mz >= 1 && mz <= maxBuildZoom() {
			maxZoom = mz
		}
	}

	tiles := calculateTiles(bbox, minZoom, maxZoom)
	estimatedSize := estimateTileBytes(len(tiles))
	estimatedSeconds := len(tiles) / 100

	availableSpace := getAvailableDiskSpace(mbtilesQueue.outputDir)
	// Require 1.2x estimated size + 2GB minimum free space
	const minFreeSpace = 2 * 1024 * 1024 * 1024 // 2GB
	requiredSpace := uint64(float64(estimatedSize)*1.2) + minFreeSpace
	sufficient := requiredSpace <= availableSpace

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]interface{}{
		"park_id":            parkID,
		"total_tiles":        len(tiles),
		"estimated_size_mb":  estimatedSize / (1024 * 1024),
		"estimated_seconds":  estimatedSeconds,
		"bbox":               bbox,
		"min_zoom":           minZoom,
		"max_zoom":           maxZoom,
		"available_space_mb": availableSpace / (1024 * 1024),
		"required_space_mb":  requiredSpace / (1024 * 1024),
		"sufficient_space":   sufficient,
		"sources":            tileSourceNames(),
	})
}

// calculateBufferedBBox calculates bbox with buffer around park
func calculateBufferedBBox(area *areas.ProtectedArea, bufferKm float64) [4]float64 {
	// Get park bbox
	latMin, latMax, lonMin, lonMax := area.GetBoundingBox()
	return bufferBBox([4]float64{lonMin, latMin, lonMax, latMax}, bufferKm)
}

// bufferBBox pads a [minLon, minLat, maxLon, maxLat] box outward. Split out of
// calculateBufferedBBox so an AOI (which has a bbox but no
// areas.ProtectedArea, by design) can be tiled by the same code.
func bufferBBox(b [4]float64, bufferKm float64) [4]float64 {
	// Add buffer (~1 degree ≈ 111km)
	bufferDeg := bufferKm / 111.0

	return [4]float64{
		b[0] - bufferDeg,
		b[1] - bufferDeg,
		b[2] + bufferDeg,
		b[3] + bufferDeg,
	}
}

func fileExists(path string) bool {
	_, err := os.Stat(path)
	return err == nil
}
