package srv

import (
	"math"
	"strconv"
	"strings"
)

// How long will this take? — the one question a user drawing a 485,000 km²
// polygon actually has, and the one the UI could not answer until now.
//
// The AOI product decision (docs/PLAN_AOI_OVERLAY.md §0 rule 3) is that the
// answer arrives over days. That is only honest if we say so *before* the
// draw is committed, with a number, rather than showing a spinner that runs
// for two days. So the create dialog prices the polygon the same way the
// runner will work it: unit counts per dataset from the geometry, times a
// measured seconds-per-unit.
//
// The rates below are MEASURED, from the XSA_Study_Area ingest on 2026-08-06/07
// (logs/aoi.log) — not guesses:
//
//	fire_gap  536 FIRMS windows in ~34 min          -> ~3.8 s/window
//	gfw       252 alert tiles in ~20 min            -> ~4.8 s/tile
//	ghsl      ~4 min per 1000 km Mollweide tile (vectorising ~40k polygons)
//	fire_v5   ~30 min for 4.1M detections -> 38,725 groups
//	clip      ~4 s regardless of size
//
// Two things keep the estimate honest rather than optimistic:
//
//  1. It is wall-clock, not CPU: the runner deliberately takes one slice a day
//     (cron at 12:00) with a per-slice budget, so a big AOI is *days* even
//     though it is only hours of work. estimateAOI reports both, and the UI
//     leads with the days.
//  2. Blocked datasets (no runner yet) are priced at zero and labelled, rather
//     than silently dropped — otherwise the total quietly implies coverage we
//     do not have.

// Measured seconds per unit, per dataset.
const (
	secPerFIRMSWindow = 3.8
	secPerGFWTile     = 4.8
	secPerGHSLTile    = 240.0
	secPerOSMCountry  = 600.0 // Geofabrik download + osmium extract, dominated by the download
	secClipFlat       = 5.0
	secBasinFlat      = 60.0
	// fire_v5 is one long unit whose cost tracks the detection count, which in
	// turn tracks area: XSA is 485,150 km² and took ~30 min over 4.1M
	// detections. Expressed per km² so a small AOI is not quoted half an hour.
	secFireV5PerKm2 = 30 * 60.0 / 485150.0
	// Fire detections themselves: the same area/day scaling the FIRMS window
	// count already carries, so nothing extra here.
)

// A slice is one cron run. The runner stops on its budget or deadline and
// resumes the next day, so elapsed days = work / per-slice capacity.
const secPerDailySlice = 90 * 60.0 // --minutes default

// AOIEstimate is one dataset's predicted cost.
type AOIEstimate struct {
	Dataset string  `json:"dataset"`
	Units   int     `json:"units"`
	Seconds float64 `json:"seconds"`
	Blocked bool    `json:"blocked,omitempty"`
	Note    string  `json:"note,omitempty"`
}

// AOIEstimateResult is the whole prediction for a polygon.
type AOIEstimateResult struct {
	Datasets   []AOIEstimate `json:"datasets"`
	TotalSec   float64       `json:"total_seconds"`
	Days       int           `json:"days"`
	Human      string        `json:"human"`
	AreaKm2    float64       `json:"area_km2"`
	FIRMSCalls int           `json:"firms_calls"`
}

// aoiBlockedDatasets have no runner yet (docs/PLAN_AOI_OVERLAY.md §3a). Priced
// at zero and labelled rather than hidden.
var aoiBlockedDatasets = map[string]string{
	"gsw":   "surface water — needs occurrence tiles we do not hold yet",
	"hydro": "rivers & lakes — needs a HydroSHEDS download",
}

// estimateAOI prices a polygon. bbox is [minx,miny,maxx,maxy] in degrees,
// areaKm2 the geodesic area, days the length of the analysis window.
func estimateAOI(bbox [4]float64, areaKm2 float64, windowDays int, countries int) AOIEstimateResult {
	if windowDays <= 0 {
		windowDays = 365
	}
	w := math.Max(0, bbox[2]-bbox[0])
	h := math.Max(0, bbox[3]-bbox[1])

	// FIRMS: 5-day windows (the API's hard cap) x 3 VIIRS sensors. This is the
	// unit the runner actually loops over, so it is also the quota figure.
	firmsWindows := int(math.Ceil(float64(windowDays)/5.0)) * 3

	// GFW: 0.5-degree tiles over the bbox, exactly tiles_for_bbox().
	gfwTiles := int(math.Ceil(w/0.5)) * int(math.Ceil(h/0.5))

	// GHSL: 1000 km Mollweide tiles. ~9 degrees of longitude at the equator,
	// so this is a bbox-corner count, deliberately rounded up.
	ghslTiles := (int(w/9.0) + 2) * (int(h/9.0) + 2)

	if countries < 1 {
		countries = 1
	}

	est := []AOIEstimate{
		{Dataset: "clip", Units: 1, Seconds: secClipFlat,
			Note: "preview clipped from nearby parks — ready immediately"},
		{Dataset: "fire_gap", Units: firmsWindows, Seconds: float64(firmsWindows) * secPerFIRMSWindow},
		{Dataset: "fire_v5", Units: 1, Seconds: areaKm2 * secFireV5PerKm2},
		{Dataset: "gfw", Units: gfwTiles, Seconds: float64(gfwTiles) * secPerGFWTile},
		{Dataset: "deforestation", Units: 1, Seconds: 300},
		{Dataset: "ghsl", Units: ghslTiles, Seconds: float64(ghslTiles) * secPerGHSLTile},
		{Dataset: "osm", Units: countries, Seconds: float64(countries) * secPerOSMCountry},
		{Dataset: "gsw", Blocked: true},
		{Dataset: "hydro", Blocked: true},
		{Dataset: "basin", Units: 1, Seconds: secBasinFlat},
	}

	total := 0.0
	for i := range est {
		if note, blocked := aoiBlockedDatasets[est[i].Dataset]; blocked {
			est[i].Blocked = true
			est[i].Note = note
			est[i].Seconds = 0
			est[i].Units = 0
			continue
		}
		total += est[i].Seconds
	}

	days := int(math.Ceil(total / secPerDailySlice))
	if days < 1 {
		days = 1
	}
	return AOIEstimateResult{
		Datasets: est, TotalSec: total, Days: days,
		Human: humanETA(total, days), AreaKm2: areaKm2, FIRMSCalls: firmsWindows,
	}
}

// humanETA phrases the estimate the way the UI should say it: the elapsed
// calendar time first (that is what the user experiences), the machine time
// second (that is what makes the first number believable).
func humanETA(totalSec float64, days int) string {
	work := humanDuration(totalSec)
	if days <= 1 {
		return "about " + work + " of processing — most of it ready today"
	}
	return "about " + days2str(days) + " (" + work + " of processing, one batch a day)"
}

func humanDuration(sec float64) string {
	switch {
	case sec < 90:
		return "a minute"
	case sec < 3600:
		return itoa(int(math.Round(sec/60))) + " min"
	case sec < 36000:
		h := sec / 3600
		return trimFloat(h) + " h"
	default:
		return itoa(int(math.Round(sec/3600))) + " h"
	}
}

func days2str(d int) string {
	if d == 1 {
		return "1 day"
	}
	return itoa(d) + " days"
}

func itoa(n int) string { return strconv.Itoa(n) }

// trimFloat renders one decimal without a trailing ".0" — "2.5 h", "3 h".
func trimFloat(f float64) string {
	s := strconv.FormatFloat(f, 'f', 1, 64)
	return strings.TrimSuffix(s, ".0")
}
