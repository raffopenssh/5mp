package srv

import (
	"encoding/json"
	"fmt"
	"strings"
)

// vanguardChain is one chain of an area's vanguard population as the
// exports (KML, Locus, GeoPackage) read it. The population is whatever
// vanguardRowsSQL selects for the area — Kalman seed-ahead chains where
// scripts/fire_vanguard_kf.py has run, plain trajectories flagged vanguard=1
// elsewhere, never both (docs/agents/fire.md "Kalman seed-ahead chains";
// invariant 7). Tracker says which, per chain.
type vanguardChain struct {
	FeatureID string
	ParkID    string
	GeoJSON   string
	Start     string
	End       string
	Tracker   string // "kf" | "groups"
	Props     map[string]interface{}
}

// vanguardChains reads the area's vanguard chains overlapping [from, to]
// (empty = unbounded), oldest first, through the partial idx_fg_vanguard
// (≤ 60k rows in the whole table, so a per-area read is ~0.05 s — the same
// plan vanguardLeads and the parks CSV use). limit 0 = whole population;
// otherwise the LAST `limit` are dropped and truncated says so, so a caller
// can tell a complete folder from a cut one (invariant 8).
func (s *Server) vanguardChains(areaID, from, to string, limit int) (chains []vanguardChain, truncated bool) {
	return s.vanguardChainsWhere(" AND park_id = ?", []interface{}{areaID}, from, to, limit)
}

// vanguardChainsWhere is vanguardChains with an arbitrary extra predicate
// (bbox + scope for the view export); `where` starts with " AND".
func (s *Server) vanguardChainsWhere(where string, args []interface{}, from, to string, limit int) (chains []vanguardChain, truncated bool) {
	q := `SELECT feature_id, park_id, feature_type, geojson, COALESCE(properties_json,'{}'), COALESCE(start_date,''), COALESCE(end_date,'')
		FROM feature_geometries INDEXED BY idx_fg_vanguard
		WHERE` + vanguardRowsSQL + where
	if from != "" {
		q += " AND (end_date IS NULL OR end_date >= ?)"
		args = append(args, from)
	}
	if to != "" {
		q += " AND (start_date IS NULL OR start_date <= ?)"
		args = append(args, to)
	}
	q += " ORDER BY start_date"
	if limit > 0 {
		q += fmt.Sprintf(" LIMIT %d", limit+1)
	}
	rows, err := s.DB.Query(q, args...)
	if err != nil {
		return nil, false
	}
	defer rows.Close()
	for rows.Next() {
		var c vanguardChain
		var ft, props string
		if rows.Scan(&c.FeatureID, &c.ParkID, &ft, &c.GeoJSON, &props, &c.Start, &c.End) != nil {
			continue
		}
		// Bad rows (lon 0) are skipped the way the trajectory exports skip them.
		if strings.Contains(c.GeoJSON, `"coordinates": [0.0,`) || strings.Contains(c.GeoJSON, `"coordinates": [0,`) || strings.Contains(c.GeoJSON, `[0.0, `) {
			continue
		}
		json.Unmarshal([]byte(props), &c.Props)
		if c.Props == nil {
			c.Props = map[string]interface{}{}
		}
		c.Tracker = "groups"
		if ft == "fire_vanguard" {
			c.Tracker = "kf"
		}
		if limit > 0 && len(chains) == limit {
			return chains, true
		}
		chains = append(chains, c)
	}
	return chains, false
}

// headingWord turns a heading in degrees (0 = N, clockwise) into an 8-point
// compass word (compass8 in histmap_lines.go takes a displacement instead).
func headingWord(deg float64) string {
	pts := []string{"N", "NE", "E", "SE", "S", "SW", "W", "NW"}
	i := int((deg+22.5)/45) % 8
	if i < 0 {
		i += 8
	}
	return pts[i]
}

// vanguardEndWords says how a Kalman-tracked chain ended, in the tip's own
// words (srv/static/fireseason.js): still moving (with the filter's heading
// and speed at the end), followed until the season arrived, or trail lost.
// Empty for a plain chain — the plain tracker records no cause, and "" is
// not "lost".
func vanguardEndWords(p map[string]interface{}) string {
	switch p["end_cause"] {
	case "ongoing":
		w := "Still moving"
		if ed, _ := p["end_date"].(string); ed != "" {
			w += " — last seen " + ed
		}
		if hd, ok := p["heading_deg"].(float64); ok {
			w += " heading " + headingWord(hd)
			if sp, ok := p["speed_kmd"].(float64); ok && sp > 0 {
				w += fmt.Sprintf(" at ~%.0f km/d", sp)
			}
		}
		return w
	case "season":
		return "Followed until the season arrived"
	case "lost":
		return "Trail lost: no fire within reach for 3 days"
	}
	return ""
}

// vanguardChainName is the placemark/track name shared by the KML and Locus
// exports: ▲, the group's short id, the start date.
func vanguardChainName(c vanguardChain) string {
	name := "Fire"
	if parts := strings.Split(c.FeatureID, "_grp_"); len(parts) == 2 && len(parts[1]) >= 8 {
		name = "Fire " + parts[1][:8]
	}
	if gn, _ := c.Props["group_name"].(string); gn != "" {
		name = gn
	}
	if c.Start != "" {
		name += " (" + c.Start + ")"
	}
	return "\u25B2 " + name
}

// vanguardChainDesc is the chain's description: its season words
// (fireSeasonWords), how it ended (vanguardEndWords), the day-order tier,
// and the narrative if it has one.
func vanguardChainDesc(c vanguardChain) string {
	var d []string
	if w := fireSeasonWords(c.Props); w != "" {
		d = append(d, w)
	}
	if w := vanguardEndWords(c.Props); w != "" {
		d = append(d, w)
	}
	if t, _ := c.Props["evidence_tier"].(string); t != "" {
		d = append(d, "Day-order evidence: "+t)
	}
	if c.Tracker == "kf" {
		d = append(d, "Kalman-tracked (seeded 10–60 d ahead of the front, followed into the season)")
	}
	if n, _ := c.Props["narrative"].(string); n != "" {
		d = append(d, n)
	}
	return strings.Join(d, " | ")
}

// vanguardFolderDesc is the words a folder of vanguard chains opens with:
// which population, how many, how they ended, whether the list was cut.
func vanguardFolderDesc(chains []vanguardChain, truncated bool, limit int) string {
	if len(chains) == 0 {
		return ""
	}
	kf, ends := 0, map[string]int{}
	for _, c := range chains {
		if c.Tracker == "kf" {
			kf++
		}
		if e, _ := c.Props["end_cause"].(string); e != "" {
			ends[e]++
		}
	}
	var d []string
	if kf == len(chains) {
		d = append(d, fmt.Sprintf("%d vanguard chains: fire lines that began 10–60 days ahead of the season front, tracked with a Kalman filter (scripts/fire_vanguard_kf.py) and followed into the arriving season — the one population whose day-to-day order is measurably better than chance. Drawn yellow and wider (\u25B2).", len(chains)))
		if ends["ongoing"] > 0 || ends["season"] > 0 || ends["lost"] > 0 {
			d = append(d, fmt.Sprintf("How they ended: %d still moving, %d followed until the season arrived, %d lost (no fire within reach for 3 days).", ends["ongoing"], ends["season"], ends["lost"]))
		}
	} else {
		d = append(d, fmt.Sprintf("%d vanguard chains: fire trajectories that began 10–60 days ahead of the season front (the Kalman seed-ahead tracker has not run for this area, so these are the plain chains flagged vanguard). Drawn yellow and wider (\u25B2).", len(chains)))
	}
	// Basis (invariant 7): the folder holds every chain that OVERLAPS the
	// window, as the map does; the season summary / ★ report count chains
	// that BEGAN in it, so the two can differ by the chains running into it.
	d = append(d, "Counted as every chain overlapping the date window, as the map draws them; the season summary counts only chains that began within it.")
	if truncated {
		d = append(d, fmt.Sprintf("Truncated: this folder holds the first %d chains of the window, oldest first. Narrow the date range for the rest.", limit))
	}
	return strings.Join(d, " ")
}

// writeVanguardKML writes the "Fire vanguard" folder (one subfolder per year,
// only the latest visible) for an area into a KML document that already
// declares the fire-vanguard style. Shared by the park KML and the merged
// KML; limit caps the read (0 = all). Writes nothing when the area has no
// vanguard chains in the window — an empty folder would read as "no early
// fire", which is a claim the front may not support.
func (s *Server) writeVanguardKML(kml *strings.Builder, areaID, from, to string, limit int) int {
	chains, truncated := s.vanguardChains(areaID, from, to, limit)
	if len(chains) == 0 {
		return 0
	}
	byYear := map[string][]vanguardChain{}
	var years []string
	for _, c := range chains {
		y := "Undated"
		if len(c.Start) >= 4 {
			y = c.Start[:4]
		}
		if _, ok := byYear[y]; !ok {
			years = append(years, y)
		}
		byYear[y] = append(byYear[y], c)
	}
	kml.WriteString("<Folder><name>Fire vanguard</name>\n")
	kml.WriteString("<description><![CDATA[" + vanguardFolderDesc(chains, truncated, limit) + "]]></description>\n")
	for i := len(years) - 1; i >= 0; i-- { // newest year first in the tree, and the only one visible
		y := years[i]
		vis := ""
		if i != len(years)-1 {
			vis = "<visibility>0</visibility>"
		}
		kml.WriteString(fmt.Sprintf("<Folder><name>%s (%d chains)</name>%s\n", y, len(byYear[y]), vis))
		for _, c := range byYear[y] {
			writeGeoJSONToKMLWithDesc(kml, c.GeoJSON, "fire-vanguard", vanguardChainName(c), vanguardChainDesc(c), c.Start, c.End)
		}
		kml.WriteString("</Folder>\n")
	}
	kml.WriteString("</Folder>\n")
	return len(chains)
}
