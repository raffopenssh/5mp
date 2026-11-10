package srv

import (
	"encoding/json"
	"net/http/httptest"
	"regexp"
	"strings"
	"testing"
)

// Regressions from the 2026-11-10 security audit. Each case was a working
// request against the live server before the fix.

func TestPublicPathIsOnlyStaticAssets(t *testing.T) {
	for p, want := range map[string]bool{
		"/static/anim.js":                true,
		"/static/vendor/x.css":           true,
		"/api/legal/CAF/x.js":            false, // gate bypass: wildcard last segment
		"/api/legal/pa/CAF_Chinko/x.css": false,
		"/api/aois/aoi_1.svg":            false,
		"/park/1.js":                     false,
		"/static/../api/version.js":      false,
		"/healthz":                       true,
	} {
		if got := isPublicPath(p); got != want {
			t.Errorf("isPublicPath(%q) = %v, want %v", p, got, want)
		}
	}
}

func TestLocalPathCannotLeaveTheSite(t *testing.T) {
	for in, want := range map[string]string{
		"//evil.example/":   "/evil.example/",
		`/\evil.example/`:   "/evil.example/",
		`\\evil.example`:    "/evil.example",
		"/":                 "/",
		"/park/COD_Virunga": "/park/COD_Virunga",
	} {
		if got := localPath(in); got != want {
			t.Errorf("localPath(%q) = %q, want %q", in, got, want)
		}
	}
}

func TestShortLinkRefusesOffSiteTargets(t *testing.T) {
	req := httptest.NewRequest("GET", "http://five.example/", nil)
	for _, raw := range []string{`/\example.org/x`, "//example.org/x", "/%5Cexample.org/x", "/x\r\nSet-Cookie: a=b"} {
		if out, ok := shortNormalizeURL(raw, req); ok {
			t.Errorf("shortNormalizeURL(%q) accepted as %q", raw, out)
		}
	}
	if _, ok := shortNormalizeURL("/?park=CAF_Chinko", nil); !ok {
		t.Error("an ordinary in-site target was refused")
	}
}

func TestGuestClampCoversDateAliases(t *testing.T) {
	g := &GuestSession{DateFrom: "2025-01-01", DateTo: "2025-01-31"}
	for _, q := range []string{"year=2026", "start=2026-01-01&end=2026-10-31", "from_year=2020&to_year=2026", ""} {
		r := clampGuestQuery(httptest.NewRequest("GET", "/api/parks/CAF_Chinko/export.kml?"+q, nil), g)
		got := r.URL.Query()
		if got.Get("from") != "2025-01-01" && q != "start=2026-01-01&end=2026-10-31" {
			t.Errorf("%q: from = %q", q, got.Get("from"))
		}
		if f, to := got.Get("from"), got.Get("to"); f < g.DateFrom || to > g.DateTo || f == "" || to == "" {
			t.Errorf("%q: window %q..%q escapes the lock", q, f, to)
		}
		for _, k := range []string{"year", "start", "end", "from_year", "to_year"} {
			if got.Has(k) {
				t.Errorf("%q: %s survived the clamp", q, k)
			}
		}
	}
}

func TestPublicHandleIsNotTheHashPrefix(t *testing.T) {
	ref := principalRef("test2026")
	if h := publicHandle(ref); h != "" && h == ref[:8] {
		t.Fatal("handle is the raw sha256 prefix of the password")
	}
}

// A date-locked guest link gets the fire narrative rebuilt for its window:
// no date outside it, no all-time total, and a straddling group dropped whole.
func TestFireNarrativeHonoursGuestWindow(t *testing.T) {
	mk := func(start, end string, fires int, pos string) FireGroupStory {
		return FireGroupStory{GroupNum: 99, FeatureID: "x_" + start, StartDate: start, EndDate: end, Days: 3,
			FiresTotal: fires, TotalFRP: 10, Position: pos, TrajectoryType: "clean", Direction: "N",
			Narrative: "Fire " + start + " to " + end}
	}
	full := func() *FireNarrative {
		return &FireNarrative{
			ParkID: "CAF_Chinko", ParkName: "Chinko", Year: 2026,
			Summary: "From 2020-2026, Chinko experienced 380,956 fire detections across 3923 fire groups.",
			Narratives: []FireGroupStory{
				mk("2024-12-28", "2025-01-03", 1000, "transits"),      // straddles the start
				mk("2025-01-05", "2025-01-09", 7, "ends_inside"),      // inside
				mk("2025-01-20", "2025-01-31", 5, "entirely_outside"), // inside, ends on the edge
				mk("2025-01-30", "2025-02-02", 2000, "transits"),      // straddles the end
				mk("2026-03-01", "2026-03-02", 4000, "transits"),      // outside
			},
			Hotspots:   []FireHotspot{{FireCount: 9}},
			TotalFires: 380956, TotalGroups: 3923, TotalFRP: 3107309.5, PeakMonth: "March",
			Trend: &FireTrendAnalysis{
				Years:     []FireYearSummary{{Year: 2024, TotalGroups: 964}, {Year: 2026, TotalGroups: 502}},
				Months:    []FireMonthSummary{{Month: "2026-03", Groups: 1}},
				Weeks:     []FireWeekSummary{{Week: "2026-W09", Groups: 1}},
				WorstYear: 2024, WorstYearGroups: 964, BestYear: 2020, BestYearRate: 6.5,
				Narrative: "Worst year: 2024 (964 groups).", Seasonality: "dry season peaks Dec-Feb",
				LatitudeComparison: &LatitudeComparison{ComparedParks: 12},
			},
		}
	}
	n := full()
	filterFireNarrativeToWindow(n, "2025-01-01", "2025-01-31", 19769.6)
	if len(n.Narratives) != 2 || n.TotalGroups != 2 || n.TotalFires != 12 || n.TotalFRP != 20 {
		t.Fatalf("kept %d groups, total_groups %d, total_fires %d, frp %v; want 2, 2, 12, 20",
			len(n.Narratives), n.TotalGroups, n.TotalFires, n.TotalFRP)
	}
	if n.Narratives[0].GroupNum != 1 || n.Narratives[1].GroupNum != 2 {
		t.Error("group_num still an all-history ordinal")
	}
	if n.StoppedInsideGroups != 1 || n.OutsideParkGroups != 1 || n.TransitedGroups != 0 {
		t.Errorf("position counts not recomputed: %+v", n)
	}
	tr := n.Trend
	if len(tr.Years) != 1 || tr.Years[0].Year != 2025 || tr.Years[0].TotalFires != 12 {
		t.Errorf("years = %+v", tr.Years)
	}
	if tr.WorstYear != 0 || tr.BestYear != 0 || tr.TrendDirection != "insufficient_data" || tr.Narrative != "" {
		t.Errorf("a between-year comparison survived a one-month window: %+v", tr)
	}
	if tr.Months != nil || tr.LatitudeComparison != nil || n.Hotspots != nil {
		t.Error("an all-time element that cannot be windowed was left in")
	}
	if len(tr.Weeks) != 2 || tr.Weeks[0].Week != "2025-W00" || tr.Weeks[1].Week != "2025-W03" {
		t.Errorf("weeks = %+v", tr.Weeks)
	}
	if n.Window == nil || n.Window.From != "2025-01-01" || n.PeakMonth != "January" {
		t.Errorf("window/peak = %+v / %q", n.Window, n.PeakMonth)
	}
	body, _ := json.Marshal(n)
	for _, d := range regexp.MustCompile(`\d{4}-\d{2}-\d{2}`).FindAllString(string(body), -1) {
		if d < "2025-01-01" || d > "2025-01-31" {
			t.Errorf("date %s outside the window in %s", d, body)
		}
	}
	for _, leak := range []string{"380956", "380,956", "3923", "964", "2026", "2024"} {
		if strings.Contains(string(body), leak) {
			t.Errorf("all-time figure %q survived", leak)
		}
	}

	// Wholly outside: a valid empty shape, arrays not null.
	n = full()
	filterFireNarrativeToWindow(n, "2019-01-01", "2019-01-31", 0)
	body, _ = json.Marshal(n)
	if n.TotalFires != 0 || !strings.Contains(string(body), `"narratives":[]`) || !strings.Contains(string(body), `"years":[]`) {
		t.Errorf("empty window body: %s", body)
	}

	// Whole years in the window: the comparison is made, over those years only.
	n = full()
	filterFireNarrativeToWindow(n, "2025-01-01", "2026-12-31", 0)
	if n.Trend.WorstYear != 2026 && n.Trend.WorstYear != 2025 {
		t.Errorf("worst year %d not from the window", n.Trend.WorstYear)
	}

	// The middleware no longer refuses it, the map applies the same edge rule,
	// and the response cache keeps a windowed body in the window's own slot.
	g := &GuestSession{Slug: "chinko-jan", DateFrom: "2025-01-01", DateTo: "2025-01-31"}
	r := withGuest(httptest.NewRequest("GET", "/api/parks/CAF_Chinko/fire-narrative", nil), g)
	rec := httptest.NewRecorder()
	if guestWindowRefuses(rec, r) {
		t.Errorf("fire-narrative refused (%d) for a locked guest", rec.Code)
	}
	if sql, args := guestTrajectoryEndSQL("2025-01-01", "2025-01-31", true, "fire_trajectory"); !strings.Contains(sql, "end_date <= ?") || len(args) != 2 {
		t.Errorf("map edge rule = %q %v", sql, args)
	}
	if sql, _ := guestTrajectoryEndSQL("", "", false, "fire_trajectory"); sql != "" {
		t.Error("an unlocked request got the end-date bound")
	}
	other := withGuest(httptest.NewRequest("GET", "/api/parks/CAF_Chinko/fire-narrative", nil),
		&GuestSession{Slug: "chinko-feb", DateFrom: "2025-02-01", DateTo: "2025-02-28"})
	unlocked := withGuest(httptest.NewRequest("GET", "/api/parks/CAF_Chinko/fire-narrative", nil), &GuestSession{Slug: "chinko-jan"})
	if cacheKey(r) == cacheKey(other) || cacheKey(r) == cacheKey(unlocked) {
		t.Error("two guest links with different windows share a response-cache slot")
	}
}
