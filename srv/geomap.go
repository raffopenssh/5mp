package srv

import (
	"database/sql"
	"encoding/json"
	"fmt"
	"log/slog"
	"net/http"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
)

// Geology overlays: two scanned sheets (Sudan GRAS 2004 1:2M, CAR BRGM 1964
// 1:1.5M) turned into vector polygons by scripts/geomaps/, served as vector
// tiles out of one MBTiles per sheet.
//
// Vector, not raster -- the difference from srv/histmap.go, which serves the
// 1:250k topographic scans as PNG. There the archive IS the picture. Here the
// units are data: the client has to recolour them, hide individual classes,
// isolate "everything that hosts gold" and set opacity. A raster tileset would
// need one build per combination of ~50 classes; a vector tileset needs one.
//
// Two things this file deliberately shares with histmap.go, because both were
// paid for once already:
//   - a tile MISS is 204, not 404. A sheet covers one country inside a
//     rectangular bounding box, so most tiles in range legitimately have no
//     data, and 404 fills the console and MapLibre's error path with noise.
//   - tiles are immutable+cached, so a REBUILD must change their URLs. The
//     revision (mtime+size) rides in the tile template as ?v=.
//
// The catalogue is read from data/geomaps/<sheet>_classes.json, NOT from
// legend_<sheet>.json. The legend is the sheet's printed unit list; what the
// tiles actually carry is the *class* list, which merges the units the print
// screen does not separate (see scripts/geomaps/vectorize.py resolve_classes)
// and drops any that never occur in the map body. Serving the legend would
// offer the user toggles for classes that cannot be drawn.

const geoMapDir = "data/geomaps"

// geoMapSheets is the order they appear in the UI; a sheet whose files are not
// installed is reported unavailable rather than omitted, so the panel can say
// why instead of silently showing one map.
var geoMapSheets = []string{"sudan", "car", "tanzania"}

type geoMapSheet struct {
	db      *sql.DB
	meta    map[string]string
	classes json.RawMessage // the catalogue file, passed through verbatim
	rev     string
	err     error
}

type geoMapStore struct {
	once   sync.Once
	sheets map[string]*geoMapSheet
	dir    string
}

var geoMaps = &geoMapStore{dir: geoMapDir}

// geoMapRev identifies this build of a sheet: see the histMapRev note. The
// same stale-tile trap applies, and it is worse here because a rebuild can
// change the *class list* (a merge the hold-out newly forces), so old tiles
// would carry class names the catalogue no longer describes.
func geoMapRev(path string) string {
	st, err := os.Stat(path)
	if err != nil {
		return "0"
	}
	return strconv.FormatInt(st.ModTime().Unix(), 36) + "-" + strconv.FormatInt(st.Size(), 36)
}

func (g *geoMapStore) load() map[string]*geoMapSheet {
	g.once.Do(func() {
		g.sheets = map[string]*geoMapSheet{}
		for _, id := range geoMapSheets {
			sh := &geoMapSheet{}
			g.sheets[id] = sh
			cat := filepath.Join(g.dir, id+"_classes.json")
			blob, err := os.ReadFile(cat)
			if err != nil {
				sh.err = fmt.Errorf("catalogue not built: %w", err)
				continue
			}
			if !json.Valid(blob) {
				sh.err = fmt.Errorf("catalogue %s is not valid JSON", cat)
				continue
			}
			sh.classes = json.RawMessage(geoMapStandardise(blob))

			tiles := filepath.Join(g.dir, id+".mbtiles")
			if _, err := os.Stat(tiles); err != nil {
				sh.err = fmt.Errorf("tiles not built: %w", err)
				continue
			}
			db, err := sql.Open("sqlite", "file:"+tiles+"?mode=ro&_pragma=busy_timeout(5000)")
			if err != nil {
				sh.err = err
				continue
			}
			meta := map[string]string{}
			rows, err := db.Query("SELECT name, value FROM metadata")
			if err != nil {
				sh.err = err
				db.Close()
				continue
			}
			for rows.Next() {
				var k, v string
				if err := rows.Scan(&k, &v); err == nil {
					meta[k] = v
				}
			}
			rows.Close()
			sh.db, sh.meta, sh.rev = db, meta, geoMapRev(tiles)
			slog.Info("geomap sheet opened", "sheet", id, "maxzoom", meta["maxzoom"], "rev", sh.rev)
		}
	})
	return g.sheets
}

// HandleAPIGeoMap is the catalogue: every sheet, its classes, its groups and
// its commodity index, in one request.
//
// One request on purpose. The panel cannot render anything useful until it
// knows the class list (a legend with 50 entries and colours), and the payload
// is ~40 KB for both sheets, so a per-sheet endpoint would only add a second
// round trip and a loading state.
func (s *Server) HandleAPIGeoMap(w http.ResponseWriter, r *http.Request) {
	sheets := geoMaps.load()
	out := make([]map[string]any, 0, len(sheets))
	for _, id := range geoMapSheets {
		sh := sheets[id]
		e := map[string]any{"id": id}
		if sh.err != nil {
			e["available"] = false
			e["reason"] = sh.err.Error()
			// The catalogue may exist while the tiles do not (vectorize.py ran,
			// tiles.sh has not). Serve it anyway: the panel can then name the
			// sheet and say what is missing instead of showing an empty list.
			if sh.classes != nil {
				e["catalogue"] = sh.classes
			}
			out = append(out, e)
			continue
		}
		e["available"] = true
		e["catalogue"] = sh.classes
		e["rev"] = sh.rev
		e["tiles"] = "/api/geomap/" + id + "/{z}/{x}/{y}.pbf?v=" + sh.rev
		for _, k := range []string{"minzoom", "maxzoom"} {
			if n, err := strconv.Atoi(sh.meta[k]); err == nil {
				e[k] = n
			}
		}
		if b := parseFloatCSV(sh.meta["bounds"], 4); b != nil {
			e["bounds"] = b
		}
		if c := parseFloatCSV(sh.meta["center"], 3); c != nil {
			e["center"] = c
		}
		if st, err := os.Stat(filepath.Join(geoMaps.dir, id+".mbtiles")); err == nil {
			e["size_bytes"] = st.Size()
			e["download"] = "/api/geomap/" + id + "/download"
		}
		out = append(out, e)
	}
	// The GeoPackage is ONE file covering every sheet, so it is a property of
	// the catalogue and not of a sheet -- the map is one layer, and the data
	// behind it must not arrive as a per-country jigsaw the user reassembles.
	// It is offered only when at least one sheet's units are on disk (the
	// input it is built from), otherwise the panel would show a button whose
	// only possible outcome is a 404. Its size is reported only once a build
	// exists; "(12 MB)" on a link that has not been built yet would be a
	// number nobody measured.
	res := map[string]any{"sheets": out}
	if built := geoMapGPKGSheets(); len(built) > 0 {
		res["geopackage"] = "/api/geomap/geopackage"
		res["geopackage_sheets"] = built
		if st, ok := geoMapGPKGReady(); ok {
			res["geopackage_bytes"] = st.Size()
		}
	}
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Cache-Control", "no-cache")
	json.NewEncoder(w).Encode(res)
}

// HandleAPIGeoMapTile serves one vector tile.
//
// The blobs are gzipped inside the MBTiles (tippecanoe's default and what the
// spec asks for), so they go out with Content-Encoding: gzip rather than being
// decompressed here -- MapLibre reads them as-is and the server does no work.
func (s *Server) HandleAPIGeoMapTile(w http.ResponseWriter, r *http.Request) {
	sh := geoMaps.load()[r.PathValue("sheet")]
	if sh == nil || sh.err != nil {
		http.Error(w, "geology sheet not installed", http.StatusNotFound)
		return
	}
	z, err1 := strconv.Atoi(r.PathValue("z"))
	x, err2 := strconv.Atoi(r.PathValue("x"))
	yStr := strings.TrimSuffix(strings.TrimSuffix(r.PathValue("y"), ".pbf"), ".mvt")
	y, err3 := strconv.Atoi(yStr)
	if err1 != nil || err2 != nil || err3 != nil || z < 0 || z > 24 {
		http.Error(w, "bad tile coordinate", http.StatusBadRequest)
		return
	}
	// MBTiles rows are TMS (y counts north from the bottom); every web client
	// asks in XYZ. Getting this wrong mirrors the map about the equator and
	// looks almost plausible at low zoom.
	yTMS := (1 << uint(z)) - 1 - y

	var blob []byte
	err := sh.db.QueryRow(
		`SELECT tile_data FROM tiles WHERE zoom_level=? AND tile_column=? AND tile_row=?`,
		z, x, yTMS).Scan(&blob)
	if err == sql.ErrNoRows {
		// Normal: a sheet maps one country inside a rectangular envelope.
		w.WriteHeader(http.StatusNoContent)
		return
	}
	if err != nil {
		slog.Warn("geomap tile read failed", "sheet", r.PathValue("sheet"), "z", z, "x", x, "y", y, "error", err)
		http.Error(w, "tile read failed", http.StatusInternalServerError)
		return
	}
	w.Header().Set("Content-Type", "application/vnd.mapbox-vector-tile")
	if len(blob) > 2 && blob[0] == 0x1f && blob[1] == 0x8b {
		w.Header().Set("Content-Encoding", "gzip")
	}
	w.Header().Set("Cache-Control", "public, max-age=604800, immutable")
	w.Write(blob)
}

// HandleAPIGeoMapDownload hands over the MBTiles for offline use (QGIS reads
// vector MBTiles directly). Range-capable via ServeContent, like the histmap
// archive, because these go over field links.
func (s *Server) HandleAPIGeoMapDownload(w http.ResponseWriter, r *http.Request) {
	id := r.PathValue("sheet")
	if geoMaps.load()[id] == nil {
		http.Error(w, "unknown sheet", http.StatusNotFound)
		return
	}
	path := filepath.Join(geoMaps.dir, id+".mbtiles")
	f, err := os.Open(path)
	if err != nil {
		http.Error(w, "geology sheet not installed", http.StatusNotFound)
		return
	}
	defer f.Close()
	st, err := f.Stat()
	if err != nil {
		http.Error(w, "stat failed", http.StatusInternalServerError)
		return
	}
	name := id + "_geology.mbtiles"
	w.Header().Set("Content-Type", "application/octet-stream")
	w.Header().Set("Content-Disposition", `attachment; filename="`+name+`"`)
	http.ServeContent(w, r, name, st.ModTime(), f)
}
