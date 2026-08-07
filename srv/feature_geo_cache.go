package srv

import (
	"bytes"
	"compress/gzip"
	"database/sql"
	"encoding/json"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"strconv"
	"strings"
	"time"
)

// Cached geography layers (rivers, roads, places, waterbodies).
//
// These four are *static geography*: they change only when an ingest runs, they
// carry no date filter, and they are the whole answer or nothing — a user who
// pins "rivers" wants the river network, not the 2,000 longest reaches. The old
// handlers capped at 500 (default) / 2,000 (max), which for a park is invisible
// and for XSA_Study_Area silently dropped 17k of 19k rivers and 11k of 13k
// roads. Same truncation existed for enriched parks: DZA_Ahaggar has 33k river
// reaches, DZA_Djurdjura 47k roads.
//
// Serving them whole means marshalling up to ~30 MB of GeoJSON, so it is done
// once and kept: gzipped into narrative_cache (kind "features:river", …),
// invalidated by the same COUNT+MAX(id) source_rev as every other entry there,
// and handed to the browser with an ETag so a re-pin is a 304.
//
// The payload is stored gzipped because the uncompressed corpus is ~210 MB
// across all areas versus ~55 MB compressed, in a 1.8 GB database.

// geoFeatureSources maps a feature type to the tables its rows come from. The
// tables are what source_rev fingerprints, so a re-ingest invalidates the entry
// without knowing this cache exists.
//
// "place" lists three: its output suppresses any place point whose name is
// already carried by a river or road line, so a roads re-ingest changes the
// places answer. A cache key that missed that would keep serving duplicate
// labels until osm_places itself happened to change.
var geoFeatureSources = map[string][]string{
	"river":     {"park_rivers_hydro"},
	"road":      {"roads_heigit"},
	"place":     {"osm_places", "park_rivers_hydro", "roads_heigit"},
	"waterbody": {"park_waterbodies", "osm_places"},
}

// geoFeatureAll is the row count that means "the whole layer".
const geoFeatureAll = 1000000

// geoFeatureWholeLimit: the UI's long-standing idiom for "give me everything"
// is &limit=5000 — it was never a real preference, just the old ceiling. Old
// share links and pinned-layer restores still carry it, so any limit at or
// above it is read as "whole layer" and takes the cached path. Only a
// deliberately small limit (a human poking the API) bypasses the cache.
const geoFeatureWholeLimit = 5000

// geoFeatureLimit parses ?limit= for the geography layers. Unlike the other
// feature types these default to EVERYTHING; a limit is only applied if the
// caller explicitly asks for a small one.
func geoFeatureLimit(limitStr string) (limit int, limited bool) {
	if limitStr == "" {
		return geoFeatureAll, false
	}
	l, err := strconv.Atoi(limitStr)
	if err != nil || l <= 0 || l >= geoFeatureWholeLimit {
		return geoFeatureAll, false
	}
	return l, true
}

// serveCachedGeoFeatures runs build() at most once per (area, type,
// source revision) and serves the result with an ETag.
//
// A caller-supplied limit bypasses the cache entirely: it is a debugging
// affordance, not the shape the UI asks for, and caching truncated answers
// under the same key would poison the full one.
func (s *Server) serveCachedGeoFeatures(w http.ResponseWriter, r *http.Request,
	parkID, featureType string, limited bool, build func() ([]byte, error)) {

	tables := geoFeatureSources[featureType]
	if len(tables) == 0 || limited {
		body, err := build()
		if err != nil {
			http.Error(w, "Database error", http.StatusInternalServerError)
			return
		}
		writeJSONBody(w, body, "")
		return
	}

	kind := "features:" + featureType
	revs := make([]string, 0, len(tables))
	for _, t := range tables {
		revs = append(revs, s.narrativeSourceRev(t, parkID))
	}
	rev := strings.Join(revs, "|")
	etag := `"` + featureType + "-" + strings.NewReplacer(":", "-", "|", "_").Replace(rev) + `"`

	// A revalidation costs one indexed aggregate and no marshalling at all.
	if match := r.Header.Get("If-None-Match"); match != "" && strings.Contains(match, etag) {
		w.Header().Set("ETag", etag)
		w.Header().Set("Cache-Control", "private, max-age=0, must-revalidate")
		w.WriteHeader(http.StatusNotModified)
		return
	}

	if payload, ok := s.getCachedGz(parkID, kind, rev); ok {
		writeJSONBody(w, payload, etag)
		return
	}

	body, err := build()
	if err != nil {
		http.Error(w, "Database error", http.StatusInternalServerError)
		return
	}
	s.putCachedGz(parkID, kind, rev, body)
	writeJSONBody(w, body, etag)
}

func writeJSONBody(w http.ResponseWriter, body []byte, etag string) {
	w.Header().Set("Content-Type", "application/json")
	if etag != "" {
		w.Header().Set("ETag", etag)
		w.Header().Set("Cache-Control", "private, max-age=0, must-revalidate")
	}
	w.Write(body)
}

// getCachedGz reads a gzipped narrative_cache payload written by putCachedGz.
func (s *Server) getCachedGz(parkID, kind, rev string) ([]byte, bool) {
	var blob []byte
	var storedRev string
	err := s.DB.QueryRow(`
		SELECT payload, source_rev FROM narrative_cache
		WHERE park_id = ? AND kind = ? AND params = ''
	`, parkID, kind).Scan(&blob, &storedRev)
	if err != nil || storedRev != rev {
		if err != nil && err != sql.ErrNoRows {
			slog.Warn("geo feature cache read failed", "park", parkID, "kind", kind, "error", err)
		}
		return nil, false
	}
	zr, err := gzip.NewReader(bytes.NewReader(blob))
	if err != nil {
		return nil, false
	}
	defer zr.Close()
	out, err := io.ReadAll(zr)
	if err != nil {
		return nil, false
	}
	return out, true
}

// putCachedGz is best-effort, like every other write to this table: it is a
// cache, so a locked database must slow the next request, never fail this one.
func (s *Server) putCachedGz(parkID, kind, rev string, body []byte) {
	var buf bytes.Buffer
	zw, _ := gzip.NewWriterLevel(&buf, gzip.BestSpeed)
	if _, err := zw.Write(body); err != nil {
		return
	}
	zw.Close()
	if _, err := s.DB.Exec(`
		INSERT OR REPLACE INTO narrative_cache
			(park_id, kind, params, payload, source_rev, computed_at)
		VALUES (?, ?, '', ?, ?, ?)
	`, parkID, kind, buf.Bytes(), rev, time.Now().UTC().Format(time.RFC3339)); err != nil {
		slog.Warn("geo feature cache write failed", "park", parkID, "kind", kind, "error", err)
	}
}

// geoFC is the GeoJSON shape all four builders emit.
type geoFC struct {
	Type     string         `json:"type"`
	Features []geoFCFeature `json:"features"`
}

type geoFCFeature struct {
	Type       string                 `json:"type"`
	Geometry   json.RawMessage        `json:"geometry"`
	Properties map[string]interface{} `json:"properties"`
}

func newGeoFC() *geoFC {
	return &geoFC{Type: "FeatureCollection", Features: []geoFCFeature{}}
}

func (fc *geoFC) add(geom json.RawMessage, props map[string]interface{}) {
	fc.Features = append(fc.Features, geoFCFeature{Type: "Feature", Geometry: geom, Properties: props})
}

func pointGeom(lon, lat float64) json.RawMessage {
	return json.RawMessage(fmt.Sprintf(`{"type":"Point","coordinates":[%f,%f]}`, lon, lat))
}
