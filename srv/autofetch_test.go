package srv

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"srv.exe.dev/db"
)

// The ledger key is computed in Python (fetch_earthranger_gpx.py:point_key)
// and read back in Go (autofetchKeyHash). If they drift, every point is "new"
// and the overlap is counted twice again -- silently.
func TestAutofetchKeyHashParity(t *testing.T) {
	if _, err := exec.LookPath("python3"); err != nil {
		t.Skip("python3 not available")
	}
	sid, ts := "0f1e2d3c-aaaa-bbbb-cccc-0123456789ab", "2026-09-18T05:35:42Z"
	out, err := exec.Command("python3", "-c",
		`import sys; sys.argv=['x']; sys.path.insert(0,'../scripts'); import fetch_earthranger_gpx as f; print(f.point_key(sys.argv[0] and "`+sid+`", "`+ts+`"))`).Output()
	if err != nil {
		t.Skipf("script import failed (requests missing?): %v", err)
	}
	py := strings.TrimSpace(string(out))
	if got := fmt.Sprintf("%016x", uint64(autofetchKeyHash(sid, ts))); got != py {
		t.Fatalf("key mismatch: go %s python %s", got, py)
	}
}

func TestAutofetchBackoff(t *testing.T) {
	h := time.Hour
	cases := []struct {
		fails int
		want  time.Duration
	}{{0, 6 * h}, {2, 6 * h}, {3, 12 * h}, {4, 24 * h}, {9, 24 * h}}
	for _, c := range cases {
		if got := autofetchBackoff(6*h, c.fails); got != c.want {
			t.Errorf("failures=%d: got %s want %s", c.fails, got, c.want)
		}
	}
}

func TestParseAutofetchOutput(t *testing.T) {
	if _, err := parseAutofetchOutput([]byte("Traceback...\nKeyError: x\n")); err == nil {
		t.Fatal("no summary must be an error, not ok/0")
	}
	res, err := parseAutofetchOutput([]byte("warn: something\n{\"ok\": true, \"points\": 3, \"partial\": true, \"errors\": 1}\n"))
	if err != nil || !res.OK || res.Points != 3 || !res.Partial {
		t.Fatalf("got %+v %v", res, err)
	}
}

// fakeER serves the four endpoints the script uses, with `points` fixed
// observations for one subject. It mimics a device syncing late: the second
// listing adds one point recorded BEFORE the others.
type fakeER struct {
	points [][3]interface{} // lon, lat, time
	calls  int
}

func (f *fakeER) handler() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("/oauth2/token", func(w http.ResponseWriter, r *http.Request) {
		if r.FormValue("password") != "pw" {
			w.WriteHeader(401)
			return
		}
		json.NewEncoder(w).Encode(map[string]string{"access_token": "tok"})
	})
	mux.HandleFunc("/api/v1.0/subjects", func(w http.ResponseWriter, r *http.Request) {
		json.NewEncoder(w).Encode(map[string]interface{}{"data": map[string]interface{}{
			"results": []map[string]string{{"id": "subj-1", "subject_type": "person", "subject_subtype": "ranger"},
				{"id": "subj-wild", "subject_type": "wildlife"}},
			"next": nil}})
	})
	mux.HandleFunc("/api/v1.0/activity/patrols", func(w http.ResponseWriter, r *http.Request) {
		json.NewEncoder(w).Encode(map[string]interface{}{"data": map[string]interface{}{"results": []interface{}{}}})
	})
	mux.HandleFunc("/api/v1.0/subject/subj-1/subjectsources", func(w http.ResponseWriter, r *http.Request) {
		json.NewEncoder(w).Encode(map[string]interface{}{"data": []map[string]string{{"source": "src-1"}}})
	})
	mux.HandleFunc("/api/v1.0/subject/subj-1/source/src-1/tracks", func(w http.ResponseWriter, r *http.Request) {
		f.calls++
		coords := [][]float64{}
		times := []string{}
		for _, p := range f.points {
			coords = append(coords, []float64{p[0].(float64), p[1].(float64)})
			times = append(times, p[2].(string))
		}
		json.NewEncoder(w).Encode(map[string]interface{}{"data": map[string]interface{}{"features": []map[string]interface{}{{
			"geometry":   map[string]interface{}{"type": "LineString", "coordinates": coords},
			"properties": map[string]interface{}{"coordinateProperties": map[string]interface{}{"times": times}},
		}}}})
	})
	return mux
}

// Two runs over overlapping windows must import each point once, and a
// point that arrives late (recorded before the window's other points) must
// still be imported by the second run.
func TestAutofetchLedgerDedupsOverlap(t *testing.T) {
	if _, err := exec.LookPath("python3"); err != nil {
		t.Skip("python3 not available")
	}
	if err := exec.Command("python3", "-c", "import requests").Run(); err != nil {
		t.Skip("python requests not installed")
	}
	er := &fakeER{points: [][3]interface{}{
		{37.0, -8.0, "2026-09-18T05:00:00+00:00"},
		{37.01, -8.01, "2026-09-18T05:10:00+00:00"},
		{37.02, -8.02, "2026-09-18T05:20:00+00:00"},
	}}
	ts := httptest.NewServer(er.handler())
	defer ts.Close()

	dir := t.TempDir()
	d, err := db.Open(filepath.Join(dir, "t.sqlite3"))
	if err != nil {
		t.Fatal(err)
	}
	defer d.Close()
	if _, err := d.Exec(`CREATE TABLE autofetch_seen (source_id INTEGER NOT NULL, key_hash INTEGER NOT NULL, recorded_at TEXT NOT NULL, PRIMARY KEY (source_id, key_hash)) WITHOUT ROWID`); err != nil {
		t.Fatal(err)
	}
	s := &Server{DB: d}
	ctx := context.Background()
	since := time.Date(2026, 9, 18, 0, 0, 0, 0, time.UTC)

	run := func() (autofetchScriptResult, string) {
		seen, _, err := s.writeAutofetchSeen(ctx, 7, since)
		if err != nil {
			t.Fatal(err)
		}
		defer os.Remove(seen)
		out := filepath.Join(dir, fmt.Sprintf("run-%d.gpx", er.calls))
		keys := out + ".keys"
		cmd := exec.Command("python3", "../scripts/fetch_earthranger_gpx.py", "--url", ts.URL, "--user", "u",
			"--out", out, "--since", since.Format(time.RFC3339), "--seen", seen, "--new-keys", keys)
		cmd.Env = append(os.Environ(), "EARTHRANGER_PASSWORD=pw")
		raw, _ := cmd.CombinedOutput()
		res, err := parseAutofetchOutput(raw)
		if err != nil {
			t.Fatalf("%v\n%s", err, raw)
		}
		if res.Points > 0 {
			if _, err := s.recordAutofetchSeen(ctx, 7, keys); err != nil {
				t.Fatal(err)
			}
		}
		return res, out
	}

	r1, gpx1 := run()
	if !r1.OK || r1.Points != 3 || r1.Dropped != 0 || r1.Partial {
		t.Fatalf("run 1: %+v", r1)
	}
	if b, _ := os.ReadFile(gpx1); !strings.Contains(string(b), "<trkpt") {
		t.Fatal("run 1 wrote no track points")
	}
	var n int
	d.QueryRow(`SELECT COUNT(*) FROM autofetch_seen WHERE source_id = 7`).Scan(&n)
	if n != 3 {
		t.Fatalf("ledger has %d keys, want 3", n)
	}

	// Same window again, plus one late-synced point recorded first.
	er.points = append([][3]interface{}{{36.99, -7.99, "2026-09-18T04:50:00+00:00"}}, er.points...)
	r2, _ := run()
	if !r2.OK || r2.Points != 1 || r2.Dropped != 3 {
		t.Fatalf("run 2 must import only the late point: %+v", r2)
	}
	r3, _ := run()
	if !r3.OK || r3.Points != 0 || r3.Dropped != 4 {
		t.Fatalf("run 3 must import nothing: %+v", r3)
	}

	// Wrong password is an auth verdict, not a generic error.
	cmd := exec.Command("python3", "../scripts/fetch_earthranger_gpx.py", "--url", ts.URL, "--user", "u", "--out", filepath.Join(dir, "x.gpx"), "--days", "1")
	cmd.Env = append(os.Environ(), "EARTHRANGER_PASSWORD=wrong")
	raw, _ := cmd.CombinedOutput()
	res, err := parseAutofetchOutput(raw)
	if err != nil || res.OK || !res.Auth {
		t.Fatalf("auth failure not reported: %+v %v\n%s", res, err, raw)
	}
}

func TestAutofetchHostAllowed(t *testing.T) {
	for _, h := range []string{"localhost", "127.0.0.1", "10.0.0.1:443", "169.254.169.254", "[::1]:443", ""} {
		if autofetchHostAllowed(h) == nil {
			t.Errorf("%q must be refused", h)
		}
	}
}
