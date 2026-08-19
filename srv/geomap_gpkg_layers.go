package srv

// The other two layers of the geology GeoPackage: the CONTACTS the reader is
// being pointed at, and the published mine sites the model was SCORED against.
//
// WHY THEY ARE IN THE SAME FILE AS THE UNITS
//
// The measurement (docs/agents/overlays.md, "Four lists, three sheets, and a
// disagreement") says the junctions carry the signal on CAR and the units do
// not: gold junctions 2.18-2.53x, gold units 0.63x. A download that shipped
// only the polygons would therefore be the half of the model that scores worse
// than random ground, handed over as "the geology" — the picture on screen
// includes the hairlines, and a file that quietly drops them is a different
// answer with the same name.
//
// The anchors ride along for the reason stated in srv/geomap_anchors.go: an
// inference is only worth having because somebody checked it, and a lift
// printed in a tooltip is not checkable. Five fields per point, every list
// including the ones whose publishers granted no licence, `terms` on every row,
// ACLED named in the notice rather than silently absent.
//
// THREE RULES THAT ARE NOT NEGOTIABLE HERE
//
//  1. The contacts are styled by GRADE, the same 1-3 scale and the same amber
//     ramp the map paints (geomap.js paintContacts), because someone who
//     filters junctions on screen and opens the file in QGIS is looking at the
//     same lines. A second colour language would make the export look like
//     different data — the trap styleGeoUnits was written to avoid.
//  2. The anchors are NEVER filtered to the reader's selection. A file in which
//     every anchor agrees with the layer is a picture of our own filter and
//     reads as a prediction that came true.
//  3. Every layer says out loud what it is: an affinity is an inference over
//     lithology, an anchor is somebody else's observation, and a filtered
//     export is a VIEW rather than the catalogue. All three go into the layer
//     description, which is what GDAL and QGIS show.

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"
)

// geoContactsGeoJSONPath is the geometry the tiles are built from
// (scripts/geomaps/contacts.py). Gitignored and 12-60 MB per sheet, so it is
// streamed per sheet and never held for all three at once.
func geoContactsGeoJSONPath(sheet string) string {
	return filepath.Join(geoMaps.dir, sheet+"_contacts.geojson")
}

// geoSheetLithIndex is code -> lithology for one sheet, read from the same
// standardised catalogue the client gets. It is how a PAIR OF CODES becomes a
// PAIR OF ROCK TYPES, which is the only thing geoContactRules can grade — the
// split described at the top of srv/geomap_contacts.go, kept on this side of
// the build so a grading change never needs a re-vectorize.
func geoSheetLithIndex(sheet string) map[string]string {
	out := map[string]string{}
	sh := geoMaps.load()[sheet]
	var blob []byte
	if sh != nil && sh.classes != nil {
		blob = sh.classes
	} else {
		b, err := os.ReadFile(filepath.Join(geoMaps.dir, sheet+"_classes.json"))
		if err != nil {
			return out
		}
		blob = geoMapStandardise(b)
	}
	var cat struct {
		Classes []struct {
			Code string `json:"code"`
			Lith string `json:"lith"`
		} `json:"classes"`
	}
	if json.Unmarshal(blob, &cat) != nil {
		return out
	}
	for _, c := range cat.Classes {
		out[c.Code] = c.Lith
	}
	return out
}

// geoContactGrade is the word for a weight, and the four words are the ones the
// panel already uses. `ungraded` is not "weight 0": it is "the model says
// nothing about this junction", which is a different statement from "this
// junction is worthless" and must not arrive as a number.
func geoContactGrade(best int) string {
	switch {
	case best >= 3:
		return "classic"
	case best == 2:
		return "likely"
	case best == 1:
		return "weak"
	}
	return "ungraded"
}

// geoContactColor is the ramp paintContacts() uses on the map, so the two
// surfaces cannot disagree about what amber means.
var geoContactColor = map[string]string{
	"classic":  "245,158,11",
	"likely":   "251,191,36",
	"weak":     "252,211,77",
	"ungraded": "156,163,175",
}

var geoContactGradeOrder = []string{"classic", "likely", "weak", "ungraded"}

// styleGeoContacts categorises the lines on `grade` — the reader's own filter,
// and the only field on this layer that changes how it should be drawn. Width
// follows grade too (a classic contact is the thing being pointed at), same as
// the map.
func styleGeoContacts() string {
	cats := make([]qmlCat, 0, len(geoContactGradeOrder))
	var symXML, catXML strings.Builder
	labels := map[string]string{
		"classic":  "classic setting (3)",
		"likely":   "plausible setting (2)",
		"weak":     "weak or derived (1)",
		"ungraded": "not graded by the model",
	}
	widths := map[string]float64{"classic": 0.9, "likely": 0.66, "weak": 0.5, "ungraded": 0.4}
	for i, g := range geoContactGradeOrder {
		fmt.Fprintf(&catXML, `<category render="1" value=%q label=%q symbol="%d"/>`+"\n", g, labels[g], i)
		symXML.WriteString(qmlLineSymbol(fmt.Sprint(i), geoContactColor[g], widths[g]) + "\n")
		cats = append(cats, qmlCat{Value: g, Label: labels[g], RGB: geoContactColor[g]})
	}
	fmt.Fprintf(&catXML, `<category render="1" value="" label="other" symbol="%d"/>`+"\n", len(cats))
	symXML.WriteString(qmlLineSymbol(fmt.Sprint(len(cats)), "156,163,175", 0.4) + "\n")
	return qmlDoc(fmt.Sprintf(`<renderer-v2 type="categorizedSymbol" attr="grade" forceraster="0" symbollevels="0" enableorderby="0">
  <categories>
%s  </categories>
  <symbols>
%s  </symbols>
</renderer-v2>`, catXML.String(), symXML.String()))
}

// styleGeoAnchors draws one symbol per SOURCE, because whose observation a
// point is is the whole reason it is in the file. Not per commodity: half the
// rows record none, and a legend whose largest class is "NULL" teaches the
// reader nothing about provenance.
//
// The symbol is built here rather than via qmlCategorized's marker default
// because of what these points sit ON. Every other point layer in the export
// lands on a basemap or on nothing; these land on a saturated FGDC pattern
// fill covering the entire canvas, and the shared default (1.6 mm, outline
// 0,0,0,80) is a 68%-transparent hairline that dissolves into a cross-hatch.
// An evidence layer you cannot see over the layer it is evidence ABOUT is the
// same nothing as a style QGIS ignored. Checked by rendering the markers over
// real unit fills cloned off the units renderer
// (scripts/geomaps/render_gpkg.py, anchor_symbols pass).
func styleGeoAnchors(sources []string) string {
	// A fixed palette by index rather than a hash of the name: nine lists, and
	// two of them must not come out the same colour because their names happen
	// to collide. Two entries used to break that rule from the other side:
	// index 0 was pure WHITE (invisible on paper, and the lowest-contrast dot
	// there is over a pale fill), and index 8 was byte-identical to the
	// fallback below, so the ninth real list and the "anything else" catch-all
	// were one symbol. Every colour here is now distinct from every other AND
	// from the fallback.
	pal := []string{"239,68,68", "59,130,246", "34,197,94", "168,85,247",
		"234,179,8", "236,72,153", "20,184,166", "249,115,22", "120,53,15"}
	const fallback = "203,213,225" // grey-blue: no list claims it
	const outline = "17,24,39,255" // opaque near-black, not the 80-alpha default
	const size = 1.8

	var catXML, symXML strings.Builder
	for i, s := range sources {
		fmt.Fprintf(&catXML, `<category render="1" value=%q label=%q symbol="%d"/>`+"\n", s, s, i)
		symXML.WriteString(qmlPointSymbol(fmt.Sprint(i), pal[i%len(pal)], size, outline) + "\n")
	}
	fmt.Fprintf(&catXML, `<category render="1" value="" label="other" symbol="%d"/>`+"\n", len(sources))
	symXML.WriteString(qmlPointSymbol(fmt.Sprint(len(sources)), fallback, size, outline) + "\n")
	return qmlDoc(fmt.Sprintf(`<renderer-v2 type="categorizedSymbol" attr="source" forceraster="0" symbollevels="0" enableorderby="0">
  <categories>
%s  </categories>
  <symbols>
%s  </symbols>
</renderer-v2>`, catXML.String(), symXML.String()))
}

// addGeoContactLayer writes the contact geometry for `sheets`, filtered to the
// reader's pairs when there is a selection.
//
// The RETURN is the QML plus how many lines landed, because the caller needs
// both for the QGIS project and for the "or say you truncated" rule. A nil
// layer means the geometry is not built on this server, which is NOT an error:
// contacts.py is optional and a sheet without them still exports its units.
// The distinction is the one geoLoadContacts makes — "not built" and "these
// units do not touch" are different statements.
func addGeoContactLayer(w *gpkgWriter, sheets []string, sel *geoMapSelection) (*gpkgLayer, string, error) {
	pairs := sel.pairSet()
	// A contacts-OFF view must not ship 882 lines nobody asked for. `nil` (no
	// selection at all) is the whole-catalogue export and takes everything;
	// an EMPTY pair set on a real selection means the layer was off.
	if sel != nil && len(pairs) == 0 {
		return nil, "", nil
	}

	table := "geology_contacts"
	desc := "Mapped contacts between geological units — where two units meet. " +
		"`grade` is this app's own inference from the two rock types either side " +
		"(3 = the classic ore setting for `commodity`, 2 = plausible, 1 = weak or derived, " +
		"ungraded = the model says nothing), NOT a record of any deposit. " +
		"Measured against published workings the junctions score better than the units on some " +
		"sheets and worse on others; see the `mining_anchors` layer and the app's Geology panel."
	l, err := w.AddLayer(table, "MULTILINESTRING", desc, []gpkgCol{
		{"sheet", "TEXT"},
		{"pair", "TEXT"},
		{"code_a", "TEXT"},
		{"code_b", "TEXT"},
		{"lith_a", "TEXT"},
		{"lith_b", "TEXT"},
		{"km", "REAL"},
		{"grade", "TEXT"},
		{"weight", "INTEGER"},
		{"commodity", "TEXT"},
		{"commodities", "TEXT"},
		{"why", "TEXT"},
	})
	if err != nil {
		return nil, "", err
	}

	found := 0
	for _, sheet := range sheets {
		// A sheet the selection has no pair on is not read at all. These files
		// are 12-60 MB each and parsing three of them to write none of one is
		// the difference between a 2 s and an 8 s download.
		if pairs != nil && !sheetHasPair(pairs, sheet) {
			found++ // it IS built; we simply want none of it
			continue
		}
		blob, err := os.ReadFile(geoContactsGeoJSONPath(sheet))
		if err != nil {
			continue // not built for this sheet; see the note above
		}
		found++
		var fc struct {
			Features []struct {
				Properties struct {
					Sheet string  `json:"sheet"`
					CodeA string  `json:"code_a"`
					CodeB string  `json:"code_b"`
					Pair  string  `json:"pair"`
					KM    float64 `json:"km"`
				} `json:"properties"`
				Geometry json.RawMessage `json:"geometry"`
			} `json:"features"`
		}
		if err := json.Unmarshal(blob, &fc); err != nil {
			return nil, "", fmt.Errorf("contacts geojson %s: %w", sheet, err)
		}
		if len(fc.Features) == 0 {
			// Invariant 1 again: a file that parsed and holds nothing is a
			// broken build, not a sheet whose units never touch.
			return nil, "", fmt.Errorf("contacts geojson for %s has no features", sheet)
		}
		lith := geoSheetLithIndex(sheet)
		for _, f := range fc.Features {
			p := f.Properties
			if p.Sheet == "" {
				p.Sheet = sheet
			}
			pair := p.Pair
			if pair == "" {
				pair = p.CodeA + "|" + p.CodeB
			}
			if pairs != nil && !pairs[p.Sheet+":"+pair] {
				continue
			}
			la, lb := lith[p.CodeA], lith[p.CodeB]
			if la == "" {
				la = "mixed"
			}
			if lb == "" {
				lb = "mixed"
			}
			rules := geoContactRuleIndex[geoLithPairKey(la, lb)]
			best := 0
			commodity, why := "", ""
			comms := make([]string, 0, len(rules))
			for _, r := range rules {
				// Rules are sorted strongest-first, so the first one is the
				// grade — but `commodity`/`why` name only THAT one, and
				// `commodities` carries the rest with their own weights. A
				// junction graded 3 for gold and 2 for copper must not read
				// as "classic for copper too".
				if r.Weight > best {
					best, commodity, why = r.Weight, r.Commodity, r.Why
				}
				comms = append(comms, fmt.Sprintf("%s (%d)", r.Commodity, r.Weight))
			}
			l.Add(string(f.Geometry),
				p.Sheet, pair, p.CodeA, p.CodeB, la, lb, p.KM,
				geoContactGrade(best), gpkgInt(best),
				gpkgStr(commodity), gpkgStr(strings.Join(comms, ", ")),
				gpkgStr(why))
		}
	}
	if found == 0 {
		return nil, "", nil // contacts.py has not run on this server
	}
	// The geometry IS there and the filter matched none of it. That is the
	// no-op-reads-as-an-answer failure: the reader asked for the view they can
	// see hundreds of lines in, and would receive an empty layer.
	if l.Count() == 0 {
		if pairs != nil {
			return nil, "", fmt.Errorf("the selection names %d contact pair(s) and none of them is in these sheets; reload the map and try again", len(pairs))
		}
		return nil, "", fmt.Errorf("no contact geometry could be written")
	}
	return l, styleGeoContacts(), nil
}

// addGeoAnchorLayer writes the published workings, whole, every time.
//
// Never filtered — not by the reader's commodity (rule 2 above) and not by the
// sheets in the file: an anchor 40 km outside the CAR cutline is exactly the
// kind of thing that shows a reader where the evidence stops.
//
// A missing anchor file is not an error. A server that does not have it simply
// cannot offer the evidence, and the caller says so in the project title rather
// than shipping a geology file that looks like it never had any.
func addGeoAnchorLayer(w *gpkgWriter) (*gpkgLayer, string, *geoAnchorDoc, error) {
	d, err := loadGeoAnchors()
	if err != nil {
		return nil, "", nil, nil
	}
	srcs := make([]string, 0, len(d.Sources))
	for _, s := range d.Sources {
		srcs = append(srcs, s.Source)
	}
	sort.Strings(srcs)

	// The notice rides in the DESCRIPTION, not only in a sidecar README: GDAL
	// and QGIS show this string, and a reader who opens the layer in ten
	// months will not have the download page in front of them.
	withheld := make([]string, 0, len(d.Withheld))
	for _, x := range d.Withheld {
		withheld = append(withheld, x.Label+" ("+x.Terms+"): "+x.Why)
	}
	desc := "Published mine and working locations the geology affinity model was scored " +
		"against — OTHER ORGANISATIONS' observations, five fields each (coordinate, year, " +
		"resource, the publisher's own id, and where it resolves). " + d.Notice
	if len(withheld) > 0 {
		desc += " Withheld from this file but used in the scoring: " + strings.Join(withheld, " ")
	}

	l, err := w.AddLayer("mining_anchors", "POINT", desc, []gpkgCol{
		{"source", "TEXT"},
		{"source_id", "TEXT"},
		{"source_url", "TEXT"},
		{"iso3", "TEXT"},
		{"year", "INTEGER"},
		{"resource", "TEXT"},
		{"observed", "TEXT"},
		{"licence", "TEXT"},
		{"terms", "TEXT"},
		{"attribution", "TEXT"},
	})
	if err != nil {
		return nil, "", nil, err
	}
	for _, f := range d.Features {
		p := f.Props
		var year interface{}
		if p.Year != nil {
			year = *p.Year
		}
		l.Add(string(f.Geometry),
			p.Source, gpkgStr(p.SourceID), gpkgStr(p.SourceURL), gpkgStr(p.ISO3),
			year, gpkgStr(p.Resource), gpkgStr(p.Observed),
			gpkgStr(p.Licence), p.Terms, gpkgStr(p.Attribution))
	}
	// loadGeoAnchors already refuses an empty file, so zero here means every
	// geometry was unrepresentable — a broken file, not an empty world.
	if l.Count() == 0 {
		return nil, "", nil, fmt.Errorf("no anchor geometry could be written")
	}
	return l, styleGeoAnchors(srcs), d, nil
}

// sheetHasPair asks whether a selection names any contact on this sheet, so a
// 60 MB file that can only contribute zero rows is never parsed.
func sheetHasPair(pairs map[string]bool, sheet string) bool {
	p := sheet + ":"
	for k := range pairs {
		if strings.HasPrefix(k, p) {
			return true
		}
	}
	return false
}

// gpkgInt keeps 0 as NULL where 0 is not a measurement. A contact the model
// says nothing about has NO weight; writing 0 would put it on the same scale as
// 1-3 and invite a reader to graduate on it.
func gpkgInt(n int) interface{} {
	if n == 0 {
		return nil
	}
	return n
}

// addGeoStructuralLayer writes ONE continental structural layer (faults or
// craton margins) as its own table. Two tables rather than one with a `layer`
// column: the two carry different attributes (a fault has a type, a craton
// margin has a name), and a union table would be half NULLs with one legend.
//
// Filtered only by the reader's SWITCH, never by their commodity or viewport:
// like the anchors, a continental context line clipped to a selection would
// stop exactly where it becomes informative. sel != nil and the layer not in
// sel.Structural means the reader had it off, and off means off.
//
// The description carries the artefact's own notice and citation (R7): the
// craton file's notice is the sentence explaining why there is no craton
// FILL, and a QGIS reader ten months from now needs it more than we do.
func addGeoStructuralLayer(w *gpkgWriter, id string) (*gpkgLayer, string, error) {
	spec := geoStructuralLayers[id]
	sl := loadGeoStructural()[id]
	if sl == nil || sl.err != nil {
		return nil, "", nil // not installed on this server; the caller names it
	}
	blob, err := os.ReadFile(geoStructuralPath(spec.File))
	if err != nil {
		return nil, "", nil
	}
	var doc struct {
		Features []struct {
			Properties map[string]string `json:"properties"`
			Geometry   json.RawMessage   `json:"geometry"`
		} `json:"features"`
	}
	if err := json.Unmarshal(blob, &doc); err != nil {
		return nil, "", fmt.Errorf("%s: %w", spec.File, err)
	}
	desc := spec.Label + ". " + sl.Notice + " Source: " + sl.Source +
		" (accessed " + sl.Accessed + "). " + sl.Citation
	if s, ok := geoStructuralSkill[spec.SkillKey]; ok {
		parts := make([]string, 0, len(s))
		for _, c := range []string{"gold", "cassiterite", "coltan"} {
			if m, ok := s[c]; ok {
				parts = append(parts, fmt.Sprintf("%s %.1fx", c, m.Lift))
			}
		}
		if len(parts) > 0 {
			desc += " Measured proximity lift (" + geoStructuralSkillScope + "): " +
				strings.Join(parts, ", ") + "."
		}
	} else {
		desc += " Skill: unmeasured."
	}
	l, err := w.AddLayer("structural_"+id, "MULTILINESTRING", desc, []gpkgCol{
		{"name", "TEXT"},       // craton name; NULL on faults
		{"fault_type", "TEXT"}, // fault kinematics; NULL on craton margins
		{"reference", "TEXT"},
	})
	if err != nil {
		return nil, "", err
	}
	for _, f := range doc.Features {
		p := f.Properties
		ref := p["reference"]
		if ref == "" {
			ref = p["source"]
		}
		l.Add(string(f.Geometry), gpkgStr(p["name"]), gpkgStr(p["type"]), gpkgStr(ref))
	}
	// Invariant 1, same as contacts: the file loaded, so zero written rows is
	// a conversion failure and must not ship as an empty layer.
	if l.Count() == 0 {
		return nil, "", fmt.Errorf("no %s geometry could be written", id)
	}
	return l, styleGeoStructural(id), nil
}

// styleGeoStructural draws the same ink the web map uses (see geomap.js
// paintStructural): fault red-brown short dash; craton margin a soft violet
// BAND — solid, wide, translucent. Not dashed, on purpose: a dashed violet
// line reads as "a boundary somebody drew" (the AOI outline is a dashed
// line), and a tectonic margin is a zone, not a claim. Deliberately nowhere
// near the contact amber ramp — these lines are ungraded context, and graded
// ink would claim a grade nobody computed.
func styleGeoStructural(id string) string {
	if id == "craton_edges" {
		return qmlSingle(qmlSymbol("line", "0",
			qmlOpt("line_color", "139,92,246,90"),
			qmlOpt("line_width", "1.8"),
			qmlOpt("line_width_unit", "MM"),
			qmlOpt("line_style", "solid"),
			qmlOpt("capstyle", "flat"),
			qmlOpt("joinstyle", "round")))
	}
	return qmlSingle(qmlSymbol("line", "0",
		qmlOpt("line_color", "185,60,40,255"),
		qmlOpt("line_width", "0.4"),
		qmlOpt("line_width_unit", "MM"),
		qmlOpt("line_style", "dash"),
		qmlOpt("use_custom_dash", "1"),
		qmlOpt("customdash", "2;1.5"),
		qmlOpt("customdash_unit", "MM"),
		qmlOpt("capstyle", "flat"),
		qmlOpt("joinstyle", "round")))
}
