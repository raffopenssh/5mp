package srv

// Offline tiles (MBTiles) — built here, stored here, encrypted.
//
// HISTORY. The first builder wrote to data/mbtiles_output and deleted after
// two hours; the second uploaded to Zenodo because disk was scarce. Disk is
// no longer scarce and Zenodo made every file a public-ish deposition on an
// academic archive, which is the wrong place for imagery a user may only
// hold privately. Both are gone (2026-09). A build is now a SHARED FILE
// (srv/shared_files.go): encrypted on disk, owned by the login that asked,
// listed under Admin → Access & Sharing → Shared files, shareable with an
// expiring guest key like any other file, swept after mbtilesTTL days or
// mbtilesMaxDownloads downloads, whichever comes first.
//
// TWO KINDS OF SOURCE, ONE RULE. TileSources (below) is what THIS SERVER may
// copy and hand out: one open-licence mosaic, attribution written into the
// file. A "src:<id>" key is a private source the user added (tile_sources.go),
// under their own agreement with its provider: the file is marked private —
// the download answers only the owner or a guest key they minted — and the
// attribution is whatever they typed. The builder does not know or care who
// the provider is.
//
// CAPACITY IS CHECKED THREE TIMES, WITH THREE DIFFERENT NUMBERS: the file
// cap (maxMBTilesSize), the login's storage budget (sharedFileBudgetBytes,
// what they already hold + this estimate), and the disk (2.2× the estimate —
// the build writes a plaintext temp then an encrypted copy — plus 2 GB that
// must remain). Each refusal names which one and by how much.

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
	"sort"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"syscall"
	"time"
)

// TileSource is one imagery service the builder can copy from.
type TileSource struct {
	Name      string
	URLFormat string // template with {z}/{x}/{y}, {-y}, {q}, {s}
	Scheme    string // xyz | tms | quadkey (informational; tileURL handles all)
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
	// Private: a user-added source. The resulting file is owner-only.
	Private bool
}

// TileSources: ONLY IMAGERY WE MAY COPY AND HAND OUT. EOX's Sentinel-2
// cloudless is CC BY-NC-SA 4.0: copying and redistribution are permitted for
// non-commercial use with the attribution below, which is written into every
// MBTiles' metadata. The register in license.go is the source of truth. A
// user's own sources are not listed here; see tile_sources.go.
var TileSources = map[string]TileSource{
	"s2cloudless": {
		Name:        "Sentinel-2 cloudless 2024 (EOX)",
		URLFormat:   "https://tiles.maps.eox.at/wmts/1.0.0/s2cloudless-2024_3857/default/g/{z}/{y}/{x}.jpg",
		Scheme:      "xyz",
		MaxZoom:     18,
		UpscaleTo:   18,
		Headers:     map[string]string{"User-Agent": "5MP Conservation Monitoring (https://github.com/raffopenssh/5mp)"},
		Attribution: "Sentinel-2 cloudless - https://s2maps.eu by EOX IT Services GmbH (Contains modified Copernicus Sentinel data 2024)",
		Licence:     "CC BY-NC-SA 4.0",
		LicenceURL:  "https://creativecommons.org/licenses/by-nc-sa/4.0/",
	},
}

const defaultTileSource = "s2cloudless"

// Limits.
const (
	maxMBTilesSize      = 8 << 30 // per-file cap
	mbtilesDiskMargin   = 2.2     // plaintext temp + encrypted copy, with slack
	mbtilesMinFree      = 2 << 30 // always leave 2 GB
	mbtilesTTL          = 14 * 24 * time.Hour
	mbtilesMaxDownloads = 3
	mbtilesWorkers      = 6
	mbtilesJobKeep      = 24 * time.Hour // how long a finished job stays listable
)

func tileSourceNames() []string {
	names := make([]string, 0, len(TileSources))
	for k := range TileSources {
		names = append(names, k)
	}
	sort.Strings(names)
	return names
}

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

// MBTilesJob is one build. JSON shape is what the bell card and the dialog
// poll; file_id/download_url point at the shared file once completed.
type MBTilesJob struct {
	ID              string     `json:"id"`
	ParkID          string     `json:"park_id"`
	ParkName        string     `json:"park_name"`
	Source          string     `json:"source"`
	SourceName      string     `json:"source_name"`
	Private         bool       `json:"private"`
	MinZoom         int        `json:"min_zoom"`
	MaxZoom         int        `json:"max_zoom"`
	BufferKm        float64    `json:"buffer_km"`
	BBox            [4]float64 `json:"bbox"`
	Status          string     `json:"status"` // pending, processing, encrypting, completed, failed, cancelled
	Phase           string     `json:"phase"`  // tiles | encrypt
	Progress        float64    `json:"progress"`
	TotalTiles      int64      `json:"total_tiles"`
	DownloadedTiles int64      `json:"downloaded_tiles"`
	FailedTiles     int64      `json:"failed_tiles"`
	EstimatedSize   int64      `json:"estimated_size_bytes"`
	FileSize        int64      `json:"file_size_bytes"`
	FileID          string     `json:"file_id,omitempty"`
	DownloadURL     string     `json:"download_url,omitempty"`
	ExpiresAt       string     `json:"expires_at,omitempty"`
	Error           string     `json:"error,omitempty"`
	CreatedAt       time.Time  `json:"created_at"`
	CompletedAt     *time.Time `json:"completed_at,omitempty"`
	Env             string     `json:"env,omitempty"`

	ref    string             // owner login
	src    TileSource         // resolved at create time (private URL never leaves the process)
	cancel context.CancelFunc // set by the executor
}

type MBTilesQueue struct {
	jobs       map[string]*MBTilesJob
	mu         sync.RWMutex
	processing atomic.Bool
	ctx        context.Context
	cancelAll  context.CancelFunc
	srv        *Server
}

var mbtilesQueue *MBTilesQueue

// InitMBTilesQueue starts the single build worker.
func InitMBTilesQueue(s *Server) {
	ctx, cancel := context.WithCancel(context.Background())
	mbtilesQueue = &MBTilesQueue{jobs: map[string]*MBTilesJob{}, ctx: ctx, cancelAll: cancel, srv: s}
	os.MkdirAll(sharedFileDir, 0o755)
	go mbtilesQueue.processJobs()
	slog.Info("MBTiles queue initialized", "workers", mbtilesWorkers, "ttl_days", int(mbtilesTTL.Hours()/24), "max_downloads", mbtilesMaxDownloads)
}

func (q *MBTilesQueue) addJob(job *MBTilesJob) {
	q.mu.Lock()
	defer q.mu.Unlock()
	job.Status = "pending"
	job.Phase = "tiles"
	job.CreatedAt = time.Now()
	q.jobs[job.ID] = job
}

func (q *MBTilesQueue) getJob(id string) *MBTilesJob {
	q.mu.RLock()
	defer q.mu.RUnlock()
	return q.jobs[id]
}

// listJobs returns the caller's jobs (by owner ref), newest first.
func (q *MBTilesQueue) listJobs(ref string) []*MBTilesJob {
	q.mu.RLock()
	defer q.mu.RUnlock()
	out := []*MBTilesJob{}
	for _, j := range q.jobs {
		if j.ref == ref && ref != "" {
			out = append(out, j)
		}
	}
	sort.Slice(out, func(i, k int) bool { return out[i].CreatedAt.After(out[k].CreatedAt) })
	return out
}

// activeFor: the running/pending job on this area for this owner, if any —
// so a second click adopts instead of duplicating.
func (q *MBTilesQueue) activeFor(ref, areaID string) *MBTilesJob {
	q.mu.RLock()
	defer q.mu.RUnlock()
	for _, j := range q.jobs {
		if j.ref == ref && j.ParkID == areaID && (j.Status == "pending" || j.Status == "processing" || j.Status == "encrypting") {
			return j
		}
	}
	return nil
}

func (q *MBTilesQueue) processJobs() {
	ticker := time.NewTicker(2 * time.Second)
	defer ticker.Stop()
	for {
		select {
		case <-q.ctx.Done():
			return
		case <-ticker.C:
			if q.processing.Load() {
				continue
			}
			q.mu.Lock()
			var next *MBTilesJob
			for _, j := range q.jobs {
				if j.Status == "pending" && (next == nil || j.CreatedAt.Before(next.CreatedAt)) {
					next = j
				}
				// Forget finished jobs after a day; the file has its own row.
				if j.CompletedAt != nil && time.Since(*j.CompletedAt) > mbtilesJobKeep {
					delete(q.jobs, j.ID)
				}
			}
			q.mu.Unlock()
			if next != nil {
				q.processing.Store(true)
				q.executeJob(next)
				q.processing.Store(false)
			}
		}
	}
}

func (q *MBTilesQueue) executeJob(job *MBTilesJob) {
	ctx, cancel := context.WithCancel(q.ctx)
	q.mu.Lock()
	job.Status = "processing"
	job.cancel = cancel
	q.mu.Unlock()
	defer cancel()

	slog.Info("MBTiles job start", "id", job.ID, "area", job.ParkID, "source", job.SourceName, "private", job.Private, "zoom", job.MaxZoom)
	fileID, size, err := q.build(ctx, job)

	q.mu.Lock()
	defer q.mu.Unlock()
	now := time.Now()
	job.CompletedAt = &now
	if err != nil {
		if ctx.Err() != nil || job.Status == "cancelled" {
			job.Status = "cancelled"
			slog.Info("MBTiles job cancelled", "id", job.ID)
			return
		}
		job.Status = "failed"
		job.Error = err.Error()
		slog.Error("MBTiles job failed", "id", job.ID, "error", err)
		q.srv.notify(job.ParkID, "mbtiles_failed", "Offline tiles failed: "+job.ParkName,
			fmt.Sprintf("Tile build for %s (%s) failed: %s", job.ParkName, job.SourceName, err.Error()), "", job.Env)
		return
	}
	job.Status = "completed"
	job.Progress = 100
	job.FileID = fileID
	job.FileSize = size
	job.DownloadURL = "/api/files/" + fileID + "/download"
	job.ExpiresAt = now.Add(mbtilesTTL).UTC().Format(time.RFC3339)
	q.srv.notify(job.ParkID, "mbtiles_complete", "Offline tiles ready: "+job.ParkName,
		fmt.Sprintf("%s · %s · %d MB · kept %d days or %d downloads", job.ParkName, job.SourceName,
			size>>20, int(mbtilesTTL.Hours()/24), mbtilesMaxDownloads), job.DownloadURL, job.Env)
}

func (s *Server) notify(parkID, typ, title, msg, link, env string) {
	if s == nil || s.DB == nil {
		return
	}
	if env == "" {
		env = clientTenant
	}
	if _, err := s.DB.Exec(`INSERT INTO notifications (park_id, notification_type, title, message, reference_url, env, created_at)
		VALUES (?, ?, ?, ?, ?, ?, datetime('now'))`, parkID, typ, title, msg, link, env); err != nil {
		slog.Warn("notification insert failed", "type", typ, "error", err)
	}
}

// build downloads the tiles into a plaintext temp MBTiles, then encrypts it
// into a shared_files row. Returns the file id and its size.
func (q *MBTilesQueue) build(ctx context.Context, job *MBTilesJob) (string, int64, error) {
	fileID := gpkgToken()
	dir := filepath.Join(sharedFileDir, fileID)
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return "", 0, err
	}
	tmpPath := filepath.Join(dir, ".build.mbtiles")
	cleanup := func() { os.RemoveAll(dir) }

	db, err := sql.Open("sqlite", tmpPath)
	if err != nil {
		cleanup()
		return "", 0, fmt.Errorf("create database: %w", err)
	}
	db.Exec("PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF;")
	if err := initMBTilesSchema(db, job, job.src); err != nil {
		db.Close()
		cleanup()
		return "", 0, fmt.Errorf("init schema: %w", err)
	}

	tiles := calculateTiles(job.BBox, job.MinZoom, job.MaxZoom)
	job.TotalTiles = int64(len(tiles))
	if job.TotalTiles == 0 {
		db.Close()
		cleanup()
		return "", 0, fmt.Errorf("the area covers no tiles at these zooms")
	}

	type result struct {
		t    Tile
		data []byte
	}
	results := make(chan result, mbtilesWorkers*4)
	work := make(chan Tile)
	var wg sync.WaitGroup
	var done, failed atomic.Int64
	for i := 0; i < mbtilesWorkers; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for t := range work {
				if ctx.Err() != nil {
					return
				}
				data, err := downloadTile(job.src, t)
				if err != nil {
					failed.Add(1)
					job.FailedTiles = failed.Load()
					continue
				}
				results <- result{t, data}
			}
		}()
	}
	go func() {
		for _, t := range tiles {
			select {
			case <-ctx.Done():
				close(work)
				return
			case work <- t:
			}
		}
		close(work)
	}()
	go func() { wg.Wait(); close(results) }()

	// Single writer, batched: 200 tiles per transaction.
	tx, err := db.Begin()
	if err != nil {
		db.Close()
		cleanup()
		return "", 0, err
	}
	stmt, _ := tx.Prepare("INSERT OR REPLACE INTO tiles (zoom_level, tile_column, tile_row, tile_data) VALUES (?, ?, ?, ?)")
	n := 0
	for r := range results {
		tmsY := (1 << uint(r.t.Z)) - 1 - r.t.Y
		stmt.Exec(r.t.Z, r.t.X, tmsY, r.data)
		n++
		c := done.Add(1)
		job.DownloadedTiles = c
		job.Progress = float64(c+failed.Load()) / float64(job.TotalTiles) * 95
		if n%200 == 0 {
			stmt.Close()
			tx.Commit()
			tx, _ = db.Begin()
			stmt, _ = tx.Prepare("INSERT OR REPLACE INTO tiles (zoom_level, tile_column, tile_row, tile_data) VALUES (?, ?, ?, ?)")
		}
	}
	stmt.Close()
	tx.Commit()
	db.Close()

	if ctx.Err() != nil {
		cleanup()
		return "", 0, ctx.Err()
	}
	// A half-empty file is not an offline map: refuse if a third failed, and
	// refuse outright if nothing came (invariant 1 — a no-op is not an answer).
	if done.Load() == 0 {
		cleanup()
		return "", 0, fmt.Errorf("no tiles could be fetched from %s", job.SourceName)
	}
	if failed.Load()*3 > job.TotalTiles {
		cleanup()
		return "", 0, fmt.Errorf("%d of %d tiles failed to download", failed.Load(), job.TotalTiles)
	}

	// Encrypt into the final name; the plaintext temp is deleted whatever happens.
	q.mu.Lock()
	job.Status, job.Phase = "encrypting", "encrypt"
	q.mu.Unlock()
	name := sharedFileName(fmt.Sprintf("%s_%s_z%d-%d.mbtiles", job.ParkID, slugify(job.SourceName), job.MinZoom, job.MaxZoom))
	nonce, err := newFileNonce()
	if err != nil {
		cleanup()
		return "", 0, err
	}
	in, err := os.Open(tmpPath)
	if err != nil {
		cleanup()
		return "", 0, err
	}
	out, err := os.Create(sharedFilePath(fileID, name))
	if err != nil {
		in.Close()
		cleanup()
		return "", 0, err
	}
	ew, err := encryptingWriter(out, nonce)
	if err != nil {
		in.Close()
		out.Close()
		cleanup()
		return "", 0, err
	}
	size, err := io.Copy(ew, in)
	in.Close()
	out.Close()
	os.Remove(tmpPath)
	if err != nil {
		cleanup()
		return "", 0, fmt.Errorf("encrypt: %w", err)
	}
	job.Progress = 99

	now := time.Now().UTC()
	private := 0
	if job.Private {
		private = 1
	}
	if _, err := q.srv.DB.Exec(`INSERT INTO shared_files
		(id, pwd_ref, env, name, size_bytes, created_at, expires_at, enc, nonce, max_downloads, kind, private)
		VALUES (?,?,?,?,?,?,?,1,?,?,'mbtiles',?)`,
		fileID, job.ref, job.Env, name, size, now.Format(time.RFC3339),
		now.Add(mbtilesTTL).Format(time.RFC3339), nonce, mbtilesMaxDownloads, private); err != nil {
		cleanup()
		return "", 0, fmt.Errorf("register file: %w", err)
	}
	return fileID, size, nil
}

func slugify(s string) string {
	var b strings.Builder
	for _, r := range strings.ToLower(s) {
		switch {
		case r >= 'a' && r <= 'z', r >= '0' && r <= '9':
			b.WriteRune(r)
		case b.Len() > 0 && b.String()[b.Len()-1] != '-':
			b.WriteByte('-')
		}
	}
	return strings.Trim(b.String(), "-")
}

// ---- tile math ----------------------------------------------------------------

type Tile struct{ X, Y, Z int }

func calculateTiles(bbox [4]float64, minZoom, maxZoom int) []Tile {
	var tiles []Tile
	for z := minZoom; z <= maxZoom; z++ {
		minX, minY := lonLatToTile(bbox[0], bbox[3], z)
		maxX, maxY := lonLatToTile(bbox[2], bbox[1], z)
		for x := minX; x <= maxX; x++ {
			for y := minY; y <= maxY; y++ {
				tiles = append(tiles, Tile{X: x, Y: y, Z: z})
			}
		}
	}
	return tiles
}

func lonLatToTile(lon, lat float64, zoom int) (int, int) {
	n := math.Pow(2, float64(zoom))
	x := int((lon + 180) / 360 * n)
	latRad := lat * math.Pi / 180
	y := int((1 - math.Log(math.Tan(latRad)+1/math.Cos(latRad))/math.Pi) / 2 * n)
	max := int(n) - 1
	if x < 0 {
		x = 0
	}
	if x > max {
		x = max
	}
	if y < 0 {
		y = 0
	}
	if y > max {
		y = max
	}
	return x, y
}

func tileToQuadKey(x, y, z int) string {
	var b strings.Builder
	for i := z; i > 0; i-- {
		d := 0
		mask := 1 << uint(i-1)
		if x&mask != 0 {
			d++
		}
		if y&mask != 0 {
			d += 2
		}
		b.WriteByte(byte('0' + d))
	}
	return b.String()
}

// countTiles is calculateTiles without the allocation, for estimates.
func countTiles(bbox [4]float64, minZoom, maxZoom int) int64 {
	var n int64
	for z := minZoom; z <= maxZoom; z++ {
		minX, minY := lonLatToTile(bbox[0], bbox[3], z)
		maxX, maxY := lonLatToTile(bbox[2], bbox[1], z)
		n += int64(maxX-minX+1) * int64(maxY-minY+1)
	}
	return n
}

// ---- fetching -------------------------------------------------------------------

func downloadTile(source TileSource, tile Tile) ([]byte, error) {
	if source.MaxZoom > 0 && tile.Z > source.MaxZoom {
		if source.UpscaleTo > 0 && tile.Z > source.UpscaleTo {
			return nil, fmt.Errorf("zoom %d beyond upscale limit %d", tile.Z, source.UpscaleTo)
		}
		return upscaledTile(source, tile)
	}
	return fetchTile(source, tile)
}

func fetchTile(source TileSource, tile Tile) ([]byte, error) {
	url := tileURL(source.URLFormat, tile.Z, tile.X, tile.Y)
	var lastErr error
	for attempt := 0; attempt < 3; attempt++ {
		req, err := http.NewRequest("GET", url, nil)
		if err != nil {
			return nil, err
		}
		for k, v := range source.Headers {
			req.Header.Set(k, v)
		}
		resp, err := tileHTTPClient.Do(req)
		if err != nil {
			lastErr = err
			time.Sleep(time.Duration(attempt+1) * 500 * time.Millisecond)
			continue
		}
		b, rerr := io.ReadAll(io.LimitReader(resp.Body, 8<<20))
		resp.Body.Close()
		if resp.StatusCode == 404 || resp.StatusCode == 204 {
			return nil, fmt.Errorf("HTTP %d", resp.StatusCode) // no such tile: don't retry
		}
		if resp.StatusCode != 200 || rerr != nil {
			lastErr = fmt.Errorf("HTTP %d", resp.StatusCode)
			if resp.StatusCode == 429 || resp.StatusCode >= 500 {
				time.Sleep(time.Duration(attempt+1) * time.Second)
				continue
			}
			return nil, lastErr
		}
		if !looksLikeImage(b, resp.Header.Get("Content-Type")) {
			return nil, fmt.Errorf("not an image")
		}
		return b, nil
	}
	return nil, lastErr
}

func upscaledTile(source TileSource, tile Tile) ([]byte, error) {
	dz := tile.Z - source.MaxZoom
	ax, ay := tile.X>>uint(dz), tile.Y>>uint(dz)
	anc := Tile{Z: source.MaxZoom, X: ax, Y: ay}
	img, err := ancestorImage(source, anc)
	if err != nil {
		return nil, err
	}
	n := 1 << uint(dz)
	b := img.Bounds()
	w, h := b.Dx()/n, b.Dy()/n
	if w == 0 || h == 0 {
		return nil, fmt.Errorf("upscale factor %d exceeds tile size", n)
	}
	sx := b.Min.X + (tile.X-ax<<uint(dz))*w
	sy := b.Min.Y + (tile.Y-ay<<uint(dz))*h
	out := image.NewRGBA(image.Rect(0, 0, b.Dx(), b.Dy()))
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

func initMBTilesSchema(db *sql.DB, job *MBTilesJob, source TileSource) error {
	if _, err := db.Exec(`
		CREATE TABLE IF NOT EXISTS metadata (name TEXT, value TEXT);
		CREATE TABLE IF NOT EXISTS tiles (zoom_level INTEGER, tile_column INTEGER, tile_row INTEGER, tile_data BLOB);
		CREATE UNIQUE INDEX IF NOT EXISTS tile_index ON tiles (zoom_level, tile_column, tile_row);`); err != nil {
		return err
	}
	desc := fmt.Sprintf("Imagery for %s from %s", job.ParkName, source.Name)
	if source.Private {
		desc += ". Built from a tile source the user configured; for that user's private use under the provider's terms."
	}
	metadata := map[string]string{
		"name":        fmt.Sprintf("%s - %s", job.ParkName, source.Name),
		"type":        "baselayer",
		"version":     "1.0",
		"description": desc,
		"format":      "jpg",
		"bounds":      fmt.Sprintf("%f,%f,%f,%f", job.BBox[0], job.BBox[1], job.BBox[2], job.BBox[3]),
		"minzoom":     strconv.Itoa(job.MinZoom),
		"maxzoom":     strconv.Itoa(job.MaxZoom),
		"attribution": source.Attribution,
		"licence":     source.Licence,
	}
	if source.LicenceURL != "" {
		metadata["licence_url"] = source.LicenceURL
	}
	if job.MaxZoom > source.MaxZoom {
		metadata["upscaled_from_zoom"] = strconv.Itoa(source.MaxZoom)
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

func getAvailableDiskSpace(path string) uint64 {
	var stat syscall.Statfs_t
	if err := syscall.Statfs(path, &stat); err != nil {
		slog.Warn("failed to get disk space", "path", path, "error", err)
		return 10 << 30
	}
	return stat.Bavail * uint64(stat.Bsize)
}

// estimateTileBytes: ~10 KB/tile measured on satellite JPEG builds; a private
// source's probe tile refines it when known.
func estimateTileBytes(nTiles int64, perTile int64) int64 {
	if perTile <= 0 {
		perTile = 10 * 1024
	}
	return nTiles * perTile
}

// ---- capacity ---------------------------------------------------------------------

type mbtilesCapacity struct {
	OK         bool   `json:"sufficient_space"`
	Reason     string `json:"capacity_reason,omitempty"`
	EstBytes   int64  `json:"estimated_size_bytes"`
	BudgetUsed int64  `json:"budget_used_bytes"`
	BudgetMax  int64  `json:"budget_max_bytes"`
	DiskFree   int64  `json:"disk_free_bytes"`
}

func gb(b int64) string { return fmt.Sprintf("%.1f GB", float64(b)/float64(1<<30)) }

// checkCapacity applies the three limits in the order a user can act on them.
func (s *Server) checkCapacity(ref string, est int64) mbtilesCapacity {
	c := mbtilesCapacity{EstBytes: est, BudgetMax: sharedFileBudgetBytes(), DiskFree: int64(getAvailableDiskSpace(sharedFileDir))}
	c.BudgetUsed = s.sharedFileUsage(ref)
	switch {
	case est > maxMBTilesSize:
		c.Reason = fmt.Sprintf("Estimated %s exceeds the %s per-file limit — lower the zoom level.", gb(est), gb(maxMBTilesSize))
	case c.BudgetUsed+est > c.BudgetMax:
		c.Reason = fmt.Sprintf("Your storage budget is %s; %s is in use and this build needs ~%s. Delete a shared file or lower the zoom.", gb(c.BudgetMax), gb(c.BudgetUsed), gb(est))
	case uint64(float64(est)*mbtilesDiskMargin)+mbtilesMinFree > uint64(c.DiskFree):
		c.Reason = fmt.Sprintf("The server has %s free and this build needs ~%s during construction. Lower the zoom or try later.", gb(c.DiskFree), gb(int64(float64(est)*mbtilesDiskMargin)+mbtilesMinFree))
	default:
		c.OK = true
	}
	return c
}

// resolveBuildSource turns a ?source= key into a TileSource for this caller.
func (s *Server) resolveBuildSource(r *http.Request, key string) (TileSource, string, int64, error) {
	if key == "" {
		key = defaultTileSource
	}
	if src, ok := TileSources[key]; ok {
		return src, key, 0, nil
	}
	if strings.HasPrefix(key, tileSourcePrefix) {
		ref, ok := tileSourcesAllowed(r)
		if !ok {
			return TileSource{}, "", 0, fmt.Errorf("private sources are not available for this session")
		}
		src, row, ok := s.tileSourceForBuilder(key, ref)
		if !ok {
			return TileSource{}, "", 0, fmt.Errorf("unknown tile source")
		}
		return src, key, row.ProbeBytes, nil
	}
	return TileSource{}, "", 0, fmt.Errorf("invalid source; use one of %s or a private source id", strings.Join(tileSourceNames(), ", "))
}

func parseMaxZoom(r *http.Request, src TileSource) int {
	mz := 17
	if v, err := strconv.Atoi(r.URL.Query().Get("maxZoom")); err == nil {
		mz = v
	}
	cap := src.MaxZoom
	if src.UpscaleTo > cap {
		cap = src.UpscaleTo
	}
	if cap == 0 {
		cap = 19
	}
	if mz > cap {
		mz = cap
	}
	if mz < 1 {
		mz = 1
	}
	return mz
}

// ---- handlers ---------------------------------------------------------------------

// HandleAPIMBTilesCreate — POST /api/{parks|aois}/{id}/mbtiles?source=&maxZoom=
func (s *Server) HandleAPIMBTilesCreate(w http.ResponseWriter, r *http.Request) {
	if GuestFromRequest(r) != nil {
		http.Error(w, "read-only link", http.StatusForbidden)
		return
	}
	ref := shortCallerRef(r)
	if ref == "" {
		http.Error(w, "sign in to build offline tiles", http.StatusForbidden)
		return
	}
	areaID := r.PathValue("id")
	areaName, rawBBox, ok := s.resolveAreaBBox(areaID)
	if !ok {
		http.Error(w, "Area not found", http.StatusNotFound)
		return
	}
	src, key, perTile, err := s.resolveBuildSource(r, r.URL.Query().Get("source"))
	if err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}
	if existing := mbtilesQueue.activeFor(ref, areaID); existing != nil {
		writeJSON(w, http.StatusOK, map[string]interface{}{"job_id": existing.ID, "adopted": true,
			"status_url": "/api/mbtiles/" + existing.ID + "/status"})
		return
	}
	maxZoom := parseMaxZoom(r, src)
	bbox := bufferBBox(rawBBox, 5.0)
	n := countTiles(bbox, 1, maxZoom)
	est := estimateTileBytes(n, perTile)
	cap := s.checkCapacity(ref, est)
	if !cap.OK {
		writeJSON(w, http.StatusInsufficientStorage, map[string]interface{}{"error": cap.Reason, "capacity": cap})
		return
	}
	job := &MBTilesJob{
		ID: fmt.Sprintf("%d", time.Now().UnixNano()), ParkID: areaID, ParkName: areaName,
		Source: key, SourceName: src.Name, Private: src.Private, MinZoom: 1, MaxZoom: maxZoom,
		BufferKm: 5, BBox: bbox, EstimatedSize: est, Env: RequestEnv(r), ref: ref, src: src,
	}
	mbtilesQueue.addJob(job)
	estSeconds := n / 60
	if estSeconds < 30 {
		estSeconds = 30
	}
	writeJSON(w, http.StatusOK, map[string]interface{}{
		"job_id": job.ID, "park_id": areaID, "source": key, "source_name": src.Name, "private": src.Private,
		"total_tiles": n, "estimated_size_mb": est >> 20, "estimated_seconds": estSeconds,
		"status_url": "/api/mbtiles/" + job.ID + "/status",
	})
}

// HandleAPIMBTilesEstimate — GET /api/{parks|aois}/{id}/mbtiles/estimate
func (s *Server) HandleAPIMBTilesEstimate(w http.ResponseWriter, r *http.Request) {
	areaID := r.PathValue("id")
	_, rawBBox, ok := s.resolveAreaBBox(areaID)
	if !ok {
		http.Error(w, "Area not found", http.StatusNotFound)
		return
	}
	src, key, perTile, err := s.resolveBuildSource(r, r.URL.Query().Get("source"))
	if err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}
	maxZoom := parseMaxZoom(r, src)
	bbox := bufferBBox(rawBBox, 5.0)
	n := countTiles(bbox, 1, maxZoom)
	est := estimateTileBytes(n, perTile)
	cap := s.checkCapacity(shortCallerRef(r), est)
	srcMax := src.MaxZoom
	if src.UpscaleTo > srcMax {
		srcMax = src.UpscaleTo
	}
	writeJSON(w, http.StatusOK, map[string]interface{}{
		"park_id": areaID, "source": key, "source_name": src.Name, "private": src.Private,
		"total_tiles": n, "estimated_size_mb": est >> 20, "estimated_seconds": n / 60,
		"bbox": bbox, "min_zoom": 1, "max_zoom": maxZoom, "source_max_zoom": srcMax,
		"sufficient_space": cap.OK, "capacity_reason": cap.Reason,
		"budget_used_bytes": cap.BudgetUsed, "budget_max_bytes": cap.BudgetMax, "disk_free_bytes": cap.DiskFree,
		"max_file_gb": maxMBTilesSize >> 30, "storage": "local-encrypted",
		"ttl_days": int(mbtilesTTL.Hours() / 24), "max_downloads": mbtilesMaxDownloads,
		"sources": tileSourceNames(),
	})
}

// HandleAPIMBTilesStatus — GET /api/mbtiles/{id}/status (owner only, 404 otherwise)
func (s *Server) HandleAPIMBTilesStatus(w http.ResponseWriter, r *http.Request) {
	job := mbtilesQueue.getJob(r.PathValue("id"))
	if job == nil || job.ref != shortCallerRef(r) {
		http.Error(w, "Job not found", http.StatusNotFound)
		return
	}
	w.Header().Set("Cache-Control", "no-store")
	writeJSON(w, http.StatusOK, job)
}

// HandleAPIMBTilesCancel — DELETE /api/mbtiles/{id}: cancel a pending/running
// build (202) or forget a finished job card (200). The file, if any, stays.
func (s *Server) HandleAPIMBTilesCancel(w http.ResponseWriter, r *http.Request) {
	if GuestFromRequest(r) != nil {
		http.Error(w, "read-only link", http.StatusForbidden)
		return
	}
	q := mbtilesQueue
	q.mu.Lock()
	job := q.jobs[r.PathValue("id")]
	if job == nil || job.ref != shortCallerRef(r) {
		q.mu.Unlock()
		http.Error(w, "Job not found", http.StatusNotFound)
		return
	}
	switch job.Status {
	case "pending":
		delete(q.jobs, job.ID)
		q.mu.Unlock()
		writeJSON(w, http.StatusOK, map[string]interface{}{"cancelled": job.ID})
	case "processing", "encrypting":
		job.Status = "cancelled"
		if job.cancel != nil {
			job.cancel()
		}
		q.mu.Unlock()
		writeJSON(w, http.StatusAccepted, map[string]interface{}{"cancelling": job.ID})
	default:
		delete(q.jobs, job.ID)
		q.mu.Unlock()
		writeJSON(w, http.StatusOK, map[string]interface{}{"removed": job.ID})
	}
}

// HandleAPIMBTilesDownload — GET /api/mbtiles/{id}/download: redirect to the
// shared file (kept so old bell cards and links keep working).
func (s *Server) HandleAPIMBTilesDownload(w http.ResponseWriter, r *http.Request) {
	job := mbtilesQueue.getJob(r.PathValue("id"))
	if job == nil || job.ref != shortCallerRef(r) || job.FileID == "" {
		http.Error(w, "Job not found", http.StatusNotFound)
		return
	}
	http.Redirect(w, r, job.DownloadURL, http.StatusFound)
}

// HandleAPIMBTilesList — GET /api/mbtiles: the caller's jobs.
func (s *Server) HandleAPIMBTilesList(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Cache-Control", "no-store")
	json.NewEncoder(w).Encode(mbtilesQueue.listJobs(shortCallerRef(r)))
}

func bufferBBox(b [4]float64, bufferKm float64) [4]float64 {
	d := bufferKm / 111.0
	return [4]float64{b[0] - d, b[1] - d, b[2] + d, b[3] + d}
}
