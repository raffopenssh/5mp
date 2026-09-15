package srv

// Exports of the early-burn ground (srv/fire_early_ground.go): KML folder,
// GeoPackage layer, Locus group — the SAME cells the map draws as rose
// squares, one polygon per cell with its true footprint, every row naming
// its basis (rule, thresholds, seasons early / held, days ahead). Follows
// srv/fire_vanguard_export.go: nothing is written when the area has no
// cells, and an area the rule could not judge (< 2 complete seasons) says so
// in the folder description rather than exporting an empty layer that would
// read as "no early ground here" (invariant 1).

import (
	"fmt"
	"strings"
)

// earlyGroundGrade is the three-step confidence word the map's fill opacity
// encodes (fireseason.js entryAlpha: 40 % faint → 70 % solid). Exports carry
// the word AND the share, so a reader can re-grade.
func earlyGroundGrade(share float64) string {
	switch {
	case share >= 0.7:
		return "solid"
	case share >= 0.55:
		return "firm"
	default:
		return "faint"
	}
}

func earlyGroundShare(c earlyGroundCell) float64 {
	if c.Held <= 0 {
		return 0
	}
	return float64(c.Early) / float64(c.Held)
}

// earlyGroundPolygon is the cell's footprint as a GeoJSON Polygon.
func earlyGroundPolygon(c earlyGroundCell) string {
	return fmt.Sprintf(`{"type":"Polygon","coordinates":[[[%.5f,%.5f],[%.5f,%.5f],[%.5f,%.5f],[%.5f,%.5f],[%.5f,%.5f]]]}`,
		c.W, c.S, c.E, c.S, c.E, c.N, c.W, c.N, c.W, c.S)
}

func earlyGroundRing(c earlyGroundCell) [][2]float64 {
	return [][2]float64{{c.W, c.S}, {c.E, c.S}, {c.E, c.N}, {c.W, c.N}, {c.W, c.S}}
}

// earlyGroundFolderDesc: what the layer is, what it predicts, what it does
// not, with the area's own numbers (seasons held, cells, chance).
func earlyGroundFolderDesc(r *earlyGroundRow) string {
	var d []string
	d = append(d, fmt.Sprintf("Early-burn ground (scripts/fire_front.py, table fire_early_ground): %.0f km² cells whose FIRST burn of the season came at least %d days before the local season front in at least %.0f %% of the complete seasons held (and in at least %d of them). Where the burning season ENTERS year after year — the routes herds come in on, boundary burns, village rings — and so where the first flights go. It is not this year's herds: the vanguard chains are that.",
		r.cellKm2(), r.AheadDays, r.MinShare*100, r.MinEarly))
	if len(r.Seasons) > 0 {
		d = append(d, fmt.Sprintf("Seasons held: %d (%s).", r.SeasonsHeld, strings.Join(r.Seasons, ", ")))
	}
	if ch, ok := r.Stats["chance_cells"].(float64); ok {
		d = append(d, fmt.Sprintf("%d cells; independence over the same seasons would give about %.0f.", len(r.Cells), ch))
	}
	d = append(d, "Chosen over the early-season fire density by scripts/eval_fire_baseline.py on the leading-edge target (docs/agents/fire.md 'Early-burn ground').")
	return strings.Join(d, " ")
}

// writeEarlyGroundKML writes the "Early-burn ground" folder (hidden by
// default; one sub-folder per grade so a reader can switch the faint cells
// off) into a KML document that declares the early-ground-{faint,firm,solid}
// styles. Returns the number of cells written. An area the rule could not
// judge gets a folder with the reason and no placemarks.
func (s *Server) writeEarlyGroundKML(kml *strings.Builder, areaID string) int {
	r := s.earlyGround(areaID, "")
	if r.Status == "not yet computed" {
		return 0
	}
	if r.Status != "ok" {
		reason, _ := r.Stats["reason"].(string)
		kml.WriteString("<Folder><name>Early-burn ground (not judged)</name><visibility>0</visibility>\n")
		kml.WriteString("<description><![CDATA[Early-burn ground could not be judged for this area: " + reason + ".]]></description>\n</Folder>\n")
		return 0
	}
	cells := r.footprints()
	if len(cells) == 0 {
		return 0
	}
	byGrade := map[string][]earlyGroundCell{}
	for _, c := range cells {
		g := earlyGroundGrade(earlyGroundShare(c))
		byGrade[g] = append(byGrade[g], c)
	}
	kml.WriteString(fmt.Sprintf("<Folder><name>Early-burn ground (%d cells, %d seasons)</name><visibility>0</visibility>\n", len(cells), r.SeasonsHeld))
	kml.WriteString("<description><![CDATA[" + earlyGroundFolderDesc(r) + "]]></description>\n")
	for _, g := range []struct{ key, label string }{{"solid", "early in ≥70 % of seasons"}, {"firm", "early in 55–70 %"}, {"faint", "early in 40–55 %"}} {
		list := byGrade[g.key]
		if len(list) == 0 {
			continue
		}
		kml.WriteString(fmt.Sprintf("<Folder><name>%s (%d cells)</name><visibility>0</visibility>\n", g.label, len(list)))
		for _, c := range list {
			name := fmt.Sprintf("Early in %d of %d seasons", c.Early, c.Held)
			writeGeoJSONToKMLWithDesc(kml, earlyGroundPolygon(c), "early-ground-"+g.key, name, earlyGroundWords(c, r.AheadDays), "", "")
		}
		kml.WriteString("</Folder>\n")
	}
	kml.WriteString("</Folder>\n")
	return len(cells)
}

// earlyGroundKMLStyles are the three graded fills (KML colour is aabbggrr;
// the map's rose ramp: faint rose-800 #9f1239 → 39129f, firm rose-600
// #e11d48 → 481de1, solid rose-400 #fb7185 → 8571fb).
const earlyGroundKMLStyles = "<Style id=\"early-ground-faint\"><LineStyle><color>8039129f</color><width>0.5</width></LineStyle><PolyStyle><color>8039129f</color></PolyStyle></Style>\n" +
	"<Style id=\"early-ground-firm\"><LineStyle><color>a0481de1</color><width>0.5</width></LineStyle><PolyStyle><color>b3481de1</color></PolyStyle></Style>\n" +
	"<Style id=\"early-ground-solid\"><LineStyle><color>c08571fb</color><width>0.5</width></LineStyle><PolyStyle><color>eb8571fb</color></PolyStyle></Style>\n"

// gpkgFireEarlyGround writes the fire_early_ground layer: one polygon per
// cell with its basis columns. Writes nothing for an area without cells.
func (s *Server) gpkgFireEarlyGround(w *gpkgWriter, o gpkgExportOpts) error {
	r := s.earlyGround(o.AreaID, "")
	if r.Status != "ok" {
		return nil
	}
	cells := r.footprints()
	if len(cells) == 0 {
		return nil
	}
	l, err := w.AddLayer("fire_early_ground", "POLYGON", earlyGroundFolderDesc(r), []gpkgCol{
		{"rule", "TEXT"},
		{"ahead_days", "INTEGER"},
		{"min_share", "REAL"},
		{"seasons_early", "INTEGER"},
		{"seasons_held", "INTEGER"},
		{"share_early", "REAL"},
		{"grade", "TEXT"},
		{"days_ahead_median", "INTEGER"},
		{"first_burn_month", "INTEGER"},
		{"usual_front_day_of_season", "INTEGER"},
		{"seasons", "TEXT"},
		{"cell_km2", "REAL"},
		{"basis", "TEXT"},
	})
	if err != nil {
		return err
	}
	seasons := strings.Join(r.Seasons, ", ")
	km2 := r.cellKm2()
	for _, c := range cells {
		share := earlyGroundShare(c)
		var month, usual interface{}
		if c.MonthMode >= 1 {
			month = c.MonthMode
		}
		if c.UsualDos >= 0 {
			usual = c.UsualDos
		}
		l.Add(earlyGroundPolygon(c), r.Rule, r.AheadDays, r.MinShare, c.Early, c.Held, share, earlyGroundGrade(share),
			c.DaysAhead, month, usual, seasons, km2, earlyGroundWords(c, r.AheadDays))
	}
	w.SetStyle("fire_early_ground", styleFireEarlyGround(), "Early-burn ground cells, graded by the share of seasons early")
	return nil
}

// styleFireEarlyGround: the map's rose ramp, three grades like the map.
func styleFireEarlyGround() string {
	return qmlDoc(`<renderer-v2 type="categorizedSymbol" attr="grade" forceraster="0" symbollevels="0" enableorderby="0">
  <categories>
<category render="1" value="solid" label="Early in ≥70 % of seasons" symbol="0"/>
<category render="1" value="firm" label="Early in 55–70 %" symbol="1"/>
<category render="1" value="faint" label="Early in 40–55 %" symbol="2"/>
  </categories>
  <symbols>
` + qmlFillSymbol("0", "251,113,133", 235, 0.1) + "\n" + qmlFillSymbol("1", "225,29,72", 179, 0.1) + "\n" + qmlFillSymbol("2", "159,18,57", 128, 0.1) + `
  </symbols>
</renderer-v2>`)
}

// locusEarlyGround adds the cells as closed-ring tracks in one hidden group
// under the mission folder. Returns the number of cells written.
func (s *Server) locusEarlyGround(tdb *locusDB, areaID string, parent int64) int {
	r := s.earlyGround(areaID, "")
	if r.Status != "ok" {
		return 0
	}
	cells := r.footprints()
	if len(cells) == 0 {
		return 0
	}
	gid, err := tdb.addGroup(fmt.Sprintf("EARLY-BURN GROUND (%d seasons)", r.SeasonsHeld), 0, "ic_tracks", locusGroupStyle(0xFFFB7185, 1), parent)
	if err != nil {
		return 0
	}
	for _, c := range cells {
		name := fmt.Sprintf("Early %d/%d seasons, ~%d d ahead", c.Early, c.Held, c.DaysAhead)
		tdb.addTrack(gid, name, earlyGroundRing(c), locusTrackStyle(0xFF22D3EE, 1), false)
	}
	return len(cells)
}
