package srv

import (
	"database/sql"
	"encoding/json"
	"fmt"
	"log/slog"
	"net/http"
	"os"
	"strconv"
	"sync"
)

// Historical map overlay: the Sudan Survey 1:250,000 series (1908-1944), 76
// sheets, mosaicked into one MBTiles by scripts/histmaps/mosaic.sh.
//
// Served straight out of the MBTiles rather than exploded to ~226k files on
// disk: the archive is one 1.4 GB sqlite file, the same artefact that gets
// handed to a field device for offline use, so there is exactly one copy and
// no way for the online and offline maps to drift apart.
//
// The tiles are RGBA with a *transparent* background and near-black ink
// (see scripts/histmaps/ink.py), which is why no second white-ink tileset
// exists: over satellite the client sets raster-brightness-min: 1, which
// pushes every RGB channel to white and leaves alpha untouched. Recolouring
// on the GPU, not in the archive.

const histMapDefaultPath = "data/histmaps/sudan250k.mbtiles"

type histMapStore struct {
	once sync.Once
	db   *sql.DB
	meta map[string]string
	err  error
	path string
	rev  string // archive revision, see histMapRev
}

// histMapRev identifies *this build* of the archive.
//
// Tiles are served `immutable, max-age=7d` because within one build they can
// never change -- but the URL is a pure function of (z, x, y), so a *rebuild*
// leaves every client pinned to the previous mosaic for a week. That is not
// hypothetical: the 2026-08-06 truncated build (76 northern sheets) stayed on
// screen after the 187-sheet rebuild, and only at the zoom levels the browser
// happened to have cached -- the levels that had been a 204 refetched and
// filled in. The result reads as "gaps at some zoom levels", i.e. as a tiling
// bug, not as a stale cache.
//
// So the revision (mtime+size of the MBTiles) rides in the tile URL as ?v=.
// A rebuild changes every tile URL exactly once; immutable stays honest.
func histMapRev(st os.FileInfo) string {
	return strconv.FormatInt(st.ModTime().Unix(), 36) + "-" + strconv.FormatInt(st.Size(), 36)
}

var histMaps = &histMapStore{path: histMapDefaultPath}

func (h *histMapStore) open() (*sql.DB, map[string]string, error) {
	h.once.Do(func() {
		if _, err := os.Stat(h.path); err != nil {
			h.err = fmt.Errorf("historical map archive not installed: %w", err)
			return
		}
		// Read-only, shared cache: tile reads are the only access and the file
		// is rebuilt out-of-band by mosaic.sh, never written to here.
		db, err := sql.Open("sqlite", "file:"+h.path+"?mode=ro&_pragma=busy_timeout(5000)")
		if err != nil {
			h.err = err
			return
		}
		meta := map[string]string{}
		rows, err := db.Query("SELECT name, value FROM metadata")
		if err != nil {
			h.err = err
			db.Close()
			return
		}
		defer rows.Close()
		for rows.Next() {
			var k, v string
			if err := rows.Scan(&k, &v); err == nil {
				meta[k] = v
			}
		}
		if st, serr := os.Stat(h.path); serr == nil {
			h.rev = histMapRev(st)
		}
		h.db, h.meta = db, meta
		slog.Info("histmap archive opened", "path", h.path, "name", meta["name"], "maxzoom", meta["maxzoom"])
	})
	return h.db, h.meta, h.err
}

// HandleAPIHistMapMeta advertises the overlay to the client: whether it is
// installed at all, and its bounds/zoom range so the UI can grey the toggle
// out and offer a "zoom to coverage" jump instead of showing an empty map.
func (s *Server) HandleAPIHistMapMeta(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	enc := json.NewEncoder(w)

	_, meta, err := histMaps.open()
	if err != nil {
		enc.Encode(map[string]any{"available": false, "reason": err.Error()})
		return
	}
	out := map[string]any{
		"available":   true,
		"id":          "sudan250k",
		"name":        meta["name"],
		"description": meta["description"],
		"attribution": meta["attribution"],
		"format":      meta["format"],
		"tiles":       "/api/histmap/sudan250k/{z}/{x}/{y}.png?v=" + histMaps.rev,
		"rev":         histMaps.rev,
	}
	if b := parseFloatCSV(meta["bounds"], 4); b != nil {
		out["bounds"] = b
	}
	if c := parseFloatCSV(meta["center"], 3); c != nil {
		out["center"] = c
	}
	for _, k := range []string{"minzoom", "maxzoom"} {
		if n, err := strconv.Atoi(meta[k]); err == nil {
			out[k] = n
		}
	}
	if st, err := os.Stat(histMaps.path); err == nil {
		out["size_bytes"] = st.Size()
		out["download"] = "/api/histmap/sudan250k/download"
	}
	enc.Encode(out)
}

// HandleAPIHistMapDownload hands over the archive itself for offline use
// (Locus Map, OsmAnd, QGIS, anything that reads MBTiles).
//
// This is deliberately the *black-ink* original, not the whitened variant the
// globe shows: the whitening is a client-side raster-brightness-min applied
// over dark satellite imagery, so baking it into the file would produce tiles
// that are invisible on the white/paper backgrounds offline viewers default
// to. One archive, recoloured at the point of display.
//
// Served with http.ServeContent so the 1.4 GB transfer supports Range
// requests and can be resumed over a field link.
func (s *Server) HandleAPIHistMapDownload(w http.ResponseWriter, r *http.Request) {
	f, err := os.Open(histMaps.path)
	if err != nil {
		http.Error(w, "historical map not installed", http.StatusNotFound)
		return
	}
	defer f.Close()
	st, err := f.Stat()
	if err != nil {
		http.Error(w, "stat failed", http.StatusInternalServerError)
		return
	}
	w.Header().Set("Content-Type", "application/octet-stream")
	w.Header().Set("Content-Disposition", `attachment; filename="sudan250k_1908-1944.mbtiles"`)
	http.ServeContent(w, r, "sudan250k_1908-1944.mbtiles", st.ModTime(), f)
}

func parseFloatCSV(s string, want int) []float64 {
	if s == "" {
		return nil
	}
	var out []float64
	for _, part := range splitComma(s) {
		f, err := strconv.ParseFloat(part, 64)
		if err != nil {
			return nil
		}
		out = append(out, f)
	}
	if len(out) != want {
		return nil
	}
	return out
}

func splitComma(s string) []string {
	var out []string
	start := 0
	for i := 0; i < len(s); i++ {
		if s[i] == ',' {
			out = append(out, s[start:i])
			start = i + 1
		}
	}
	return append(out, s[start:])
}

// HandleAPIHistMapTile serves one XYZ tile out of the MBTiles archive.
//
// MBTiles stores rows in TMS order (y counts north from the equator-ish
// bottom) while every web client asks in XYZ (y counts south from the top).
// The flip is y_tms = 2^z - 1 - y_xyz; getting it wrong yields a map that is
// mirrored about the equator and looks *almost* plausible at low zoom.
func (s *Server) HandleAPIHistMapTile(w http.ResponseWriter, r *http.Request) {
	db, _, err := histMaps.open()
	if err != nil {
		http.Error(w, "historical map not installed", http.StatusNotFound)
		return
	}
	z, err1 := strconv.Atoi(r.PathValue("z"))
	x, err2 := strconv.Atoi(r.PathValue("x"))
	// The client asks for ".../{y}.png" because MapLibre and every offline
	// viewer expect an extension; the route captures it as part of {y}.
	yStr := r.PathValue("y")
	if i := len(yStr) - 4; i > 0 && yStr[i:] == ".png" {
		yStr = yStr[:i]
	}
	y, err3 := strconv.Atoi(yStr)
	if err1 != nil || err2 != nil || err3 != nil || z < 0 || z > 24 {
		http.Error(w, "bad tile coordinate", http.StatusBadRequest)
		return
	}
	yTMS := (1 << uint(z)) - 1 - y

	var blob []byte
	err = db.QueryRow(
		`SELECT tile_data FROM tiles WHERE zoom_level=? AND tile_column=? AND tile_row=?`,
		z, x, yTMS).Scan(&blob)
	if err == sql.ErrNoRows {
		// A miss is normal: the series covers 18 of 22 1:1M blocks, so parts of
		// the bounding box have no sheet. 204 keeps it out of the console and
		// out of MapLibre's error path.
		w.WriteHeader(http.StatusNoContent)
		return
	}
	if err != nil {
		slog.Warn("histmap tile read failed", "z", z, "x", x, "y", y, "error", err)
		http.Error(w, "tile read failed", http.StatusInternalServerError)
		return
	}
	w.Header().Set("Content-Type", "image/png")
	// Immutable content: the archive is only replaced by a rebuild, and the
	// client reloads on deploy anyway.
	w.Header().Set("Cache-Control", "public, max-age=604800, immutable")
	w.Write(blob)
}
