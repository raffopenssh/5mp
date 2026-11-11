package srv

import (
	"context"
	"database/sql"
	"testing"
)

func TestLearnedContext(t *testing.T) {
	db, err := sql.Open("sqlite", ":memory:")
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	db.SetMaxOpenConns(1)
	for _, q := range []string{
		`CREATE TABLE osm_places (id INTEGER PRIMARY KEY, park_id TEXT, place_type TEXT, name TEXT, lat REAL, lon REAL)`,
		`CREATE TABLE park_rivers_hydro (id INTEGER PRIMARY KEY, park_id TEXT, hyriv_id INTEGER, name TEXT, lat REAL, lon REAL, geojson TEXT)`,
		// The nearest village belongs to ANOTHER park's extract: still found.
		`INSERT INTO osm_places VALUES (1,'B','village','Near',0.00,0.02), (2,'A','town','Far',0.00,0.08),
			(3,'A','river','NotAVillage',0.0,0.0), (4,'A','mountain','Peak',0.0,0.001)`,
		`INSERT INTO park_rivers_hydro VALUES
			(1,'A',1,'Ruaha',0.005,0.0,'{"type":"LineString","coordinates":[[-0.02,0.005],[0.02,0.005]]}'),
			(2,'A',2,'',0.0,0.0,'{"type":"LineString","coordinates":[[-0.02,0.0],[0.02,0.0]]}'),
			(3,'B',3,'Other',0.0,0.0,'{"type":"LineString","coordinates":[[-0.02,0.0],[0.02,0.0]]}')`,
	} {
		if _, err := db.Exec(q); err != nil {
			t.Fatal(err)
		}
	}
	s := &Server{DB: db}
	c := s.learnedContextAt(context.Background(), "A", 0, 0)
	if c.Village != "Near" || c.VillageDir != "W" || c.VillageKm < 2.1 || c.VillageKm > 2.3 {
		t.Errorf("village = %+v, want Near, ~2.2 km, feature W of it", c)
	}
	// The unnamed reach on top of the point and the other park's river are
	// not answers; the named one 550 m north is.
	if c.River != "Ruaha" || c.RiverM < 500 || c.RiverM > 600 {
		t.Errorf("river = %q %.0f m, want Ruaha ~553 m", c.River, c.RiverM)
	}
	if c := s.learnedContextAt(context.Background(), "A", 1, 1); c.Village != "" || c.River != "" {
		t.Errorf("nothing in range must be absent, got %+v", c)
	}
	p := map[string]any{}
	learnedContext{}.apply(p)
	if len(p) != 0 {
		t.Errorf("empty context wrote %v", p)
	}
}

func TestCorridorIndexCovers(t *testing.T) {
	idx := newCorridorIndex(0)
	if idx.covers([]float64{0, 0}) {
		t.Error("an empty index covers nothing")
	}
	idx.addLine([][]float64{{10, -5}, {11, -4}})
	if !idx.covers([]float64{10.5, -4.2}) || idx.covers([]float64{12, -4.5}) {
		t.Error("covers must be the bbox of the indexed lines")
	}
}
