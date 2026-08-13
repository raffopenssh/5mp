package srv

// One legend for every geology sheet: international chronostratigraphic
// colours + FGDC-style lithology patterns.
//
// WHY THIS EXISTS
//
// The two sheets we hold were printed 40 years apart by different surveys, and
// each was digitised in *its own* ink: Sudan's Quaternary alluvium is a pale
// olive, CAR's a pale cream, and neither is what a geologist expects. Worse,
// several of both sheets' inks are a desaturated blue-grey that is, on a dark
// basemap, indistinguishable from our waterbody layer — which is exactly the
// confusion the screenshots showed. Two sheets also meant two cards, two
// legends and two opacity sliders in the panel for one question ("what rock is
// under here?"), and a colour that means Cretaceous on one and basement on the
// other.
//
// So the sheets are *presented* as one layer with one legend, and the legend is
// the industry standard rather than ours to invent:
//
//   COLOUR = AGE. The ICS/CGMW International Chronostratigraphic Chart colours
//   (v2023), the same ones on every national geological map, in GeoSciML and in
//   the USGS/BGS map series. Anyone who has read a geological map already knows
//   this legend, and two sheets meeting at a border now agree.
//
//   PATTERN = LITHOLOGY. FGDC Digital Cartographic Standard for Geologic Map
//   Symbolization (FGDC-STD-013-2006) §37: dots for sand and sandstone, bricks
//   for carbonate, dashes for mudrock, plus-signs for intrusive rock, "v" for
//   volcanics, wavy dashes for schist/gneiss, cross-hatch for ultramafics.
//
// The pattern is not decoration. It is what makes a geology polygon
// unmistakably geology at a glance: no water body, no park fill and no fire
// grid cell is hatched, so a hatched patch is always the rock map — which is
// the point the user made. It also carries real information (age and lithology
// are the two axes of every printed legend) and it survives the one thing a
// colour cannot: being drawn at 55% opacity over an arbitrary basemap.
//
// THE PRINTED INK IS NOT THROWN AWAY. `color` stays on every class, the map has
// an "as printed" mode, and the GeoPackage keeps `ink_color` alongside
// `ics_color`. The scan is the primary source; this is a rendering of it.
//
// One source of truth: this file feeds the catalogue API (so geomap.js and the
// admin legend render from it), and srv/geomap_gpkg.go styles the QGIS export
// from the same keys. A QGIS user opening the export and a user looking at the
// web map must not be shown two different legends for one polygon.

import (
	"encoding/json"
	"fmt"
	"strings"
)

// geoAge is one entry of the chronostratigraphic legend.
type geoAge struct {
	Key   string // stable id, used by share links, the QML and the pattern code
	Label string // what the legend prints
	Color string // ICS/CGMW hex
	Rank  int    // youngest = 0; the legend is ordered by it, as a chart is
}

// ICS/CGMW colours, hex as published on the International Chronostratigraphic
// Chart. Deliberately the SYSTEM/PERIOD level (plus the Precambrian eras),
// because that is the resolution both sheets legend at — subdividing further
// would invent precision the scans do not carry.
var geoAges = []geoAge{
	{"quaternary", "Quaternary", "#F9F97F", 0},
	{"neogene", "Neogene", "#FFE619", 1},
	{"paleogene", "Paleogene", "#FD9A52", 2},
	{"tertiary", "Tertiary (undivided)", "#FDB46C", 3},
	{"cretaceous", "Cretaceous", "#7FC64E", 4},
	{"jurassic", "Jurassic", "#34B2C9", 5},
	{"triassic", "Triassic", "#812B92", 6},
	{"mesozoic", "Mesozoic (undivided)", "#67C5CA", 7},
	{"permian", "Permian", "#F04028", 8},
	{"carboniferous", "Carboniferous", "#67A599", 9},
	{"devonian", "Devonian", "#CB8C37", 10},
	{"silurian", "Silurian", "#B3E1B6", 11},
	{"ordovician", "Ordovician", "#009270", 12},
	{"cambrian", "Cambrian", "#7FA056", 13},
	{"paleozoic", "Palaeozoic (undivided)", "#99C08D", 14},
	{"neoproterozoic", "Neoproterozoic", "#FEB342", 15},
	{"mesoproterozoic", "Mesoproterozoic", "#FDB462", 16},
	{"paleoproterozoic", "Palaeoproterozoic", "#F74370", 17},
	{"proterozoic", "Proterozoic (undivided)", "#F04390", 18},
	{"archean", "Archaean", "#F0047F", 19},
	{"unknown", "Age not stated", "#BDBDBD", 99},
}

var geoAgeByKey = func() map[string]geoAge {
	m := map[string]geoAge{}
	for _, a := range geoAges {
		m[a.Key] = a
	}
	return m
}()

// geoAgeRules maps the words the two sheets actually print — English (GRAS
// 2004) and French (BRGM 1964) — onto a chart period. Order matters: the first
// match wins, so the more specific string must come first ("permo-triassic"
// before "trias").
//
// A sheet's own stratigraphic group string is the input, not the unit name: the
// name describes rock, the group describes time, and conflating them is how
// "Umm Ruwaba Formation" ends up dated by the word "Formation".
// A hyphenated SPAN ("Mesoproterozoic - Neoproterozoic", "Uppermost
// Carboniferous - Lower Jurassic") is one unit the survey dates across a
// boundary. Each such term is curated to one period BELOW, above both of its
// endpoints; `geoVocabAudit` reports any span a sheet prints that we have not.
var geoAgeRules = []struct{ needle, key string }{
	{"tertiary-quaternary", "quaternary"},  // span; see above
	{"neogene - quaternary", "quaternary"}, // span; see the span note below
	{"quaternai", "quaternary"}, {"quaternar", "quaternary"}, {"neo-tchadien", "quaternary"},
	{"neogene", "neogene"}, {"miocene", "neogene"}, {"pliocene", "neogene"},
	{"paleogene", "paleogene"}, {"eocene", "paleogene"}, {"oligocene", "paleogene"},
	{"tertiaire", "tertiary"}, {"tertiary", "tertiary"},
	// The Karoo, as the GST's own chronostratigraphy words it. Dated Triassic:
	// the span's own middle, and where the bulk of the succession sits. Either
	// endpoint alone would be a worse answer than the middle, and the tip keeps
	// printing the sheet's full string, so the colour is a summary of a span the
	// reader can still see whole.
	{"uppermost carboniferous - lower jurassic", "triassic"},
	{"jurassic-cretaceous", "cretaceous"},
	{"cretace", "cretaceous"}, {"cretaceous", "cretaceous"},
	// The GST sheet's own spelling. Kept as the survey prints it rather than
	// corrected in the data: the catalogue is a record of what the sheet says.
	{"cretacous", "cretaceous"},
	{"jurassic", "jurassic"}, {"jurassique", "jurassic"},
	{"permo-trias", "triassic"}, {"trias", "triassic"},
	{"secondaire", "mesozoic"}, {"mesozoic", "mesozoic"},
	{"permian", "permian"}, {"permien", "permian"},
	{"carbonifer", "carboniferous"},
	{"devonian", "devonian"}, {"devonien", "devonian"},
	{"silurian", "silurian"}, {"silurien", "silurian"},
	// A hyphenated SPAN ("Cambro-Ordovician", "Jurassic-Cretaceous") is the
	// sheet's own compound term for one unit, not a merged class, so it is
	// curated to a single period here rather than left to the scan — and it
	// must come BEFORE its endpoints or the generic rule fires first and the
	// curated line is dead code that reads like a decision. `geoVocabAudit`
	// reports any span we have not curated, so a third sheet's compound term
	// cannot be silently resolved to whichever endpoint happens to sort first.
	{"cambro-ordovician", "ordovician"},
	{"ordovician", "ordovician"}, {"ordovicien", "ordovician"},
	{"palaeozoic", "paleozoic"}, {"paleozoic", "paleozoic"}, {"primaire", "paleozoic"},
	// Pan-African is the Neoproterozoic-to-earliest-Cambrian orogenic cycle;
	// on the Sudan sheet every "Pan-African …" group is Neoproterozoic ground.
	// Precambrian spans, curated ABOVE their endpoints for the reason given at
	// the Cambro-Ordovician note: a reworked basement unit that the survey dates
	// across two eras must not be dated by which of the two rules sits higher in
	// this list. Each is dated at its YOUNGER endpoint, which for every one of
	// them is the orogeny the survey names as having made the rock what it is
	// (Neoarchaean protoliths in a Neoproterozoic granulite belt are mapped as
	// the belt). The full span stays in the group string the tip prints.
	{"neoproterozoic - cambrian", "neoproterozoic"}, // Bukoban; the sheet marks the Cambrian top "(?)"
	{"neoarchaean - neoproterozoic", "neoproterozoic"},
	{"mesoproterozoic - neoproterozoic", "neoproterozoic"},
	{"paleoproterozoic - mesoproterozoic", "mesoproterozoic"},
	{"neoarchaean - paleoproterozoic", "paleoproterozoic"},
	{"pan-african", "neoproterozoic"},
	{"neoproterozoic", "neoproterozoic"},
	{"neoprozerozoic", "neoproterozoic"}, // the GST sheet's own spelling, again
	{"precambrien a", "neoproterozoic"},
	{"upper proterozoic", "neoproterozoic"},
	{"middle proterozoic", "mesoproterozoic"}, {"mesoproterozoic", "mesoproterozoic"},
	{"lower proterozoic", "paleoproterozoic"}, {"paleoproterozoic", "paleoproterozoic"},
	{"palaeoproterozoic", "paleoproterozoic"}, // British spelling; the GST sheet uses both
	// The BRGM sheet's "Précambrien D" is its crystalline basement — the
	// Congo craton's Palaeoproterozoic-and-older core. Dated no finer here
	// than the sheet dates it.
	{"precambrien d", "paleoproterozoic"},
	{"precambrien", "proterozoic"}, {"proterozoic", "proterozoic"},
	{"archaean", "archean"}, {"archean", "archean"}, {"archeen", "archean"},
	{"craton", "archean"},
	// Cambrian LAST of the periods: "precambrien"/"precambrian" contains
	// "cambrien"/"cambrian", so a first-match scan that met it earlier dated
	// every crystalline basement unit on the CAR sheet as Cambrian.
	{"cambrian", "cambrian"}, {"cambrien", "cambrian"},
}

// geoAgeMatches returns the indices of EVERY rule whose needle occurs in one
// group part, in rule order — so index 0 is the one geoAgeOf actually uses.
// The extra matches are what the audit reads: a part that matches two rules
// with different keys and no curated compound term for the pair got its answer
// from the order of this list, which is a coin toss wearing a decision's face.
func geoAgeMatches(part string) []int {
	var out []int
	for i, r := range geoAgeRules {
		if strings.Contains(part, r.needle) {
			out = append(out, i)
		}
	}
	return out
}

// geoAgeOf resolves a sheet's group string to a chart period.
//
// A group can name SEVERAL ages ("Tertiary / Lower Proterozoic basement") —
// that is a merged class, where the print screen does not separate two units
// the sheet legends separately, and the map genuinely does not say which one a
// patch is. Same rule as everywhere else in this overlay: never pick one.
// `mixed` is true then, the map hatches such a class differently, and the tip
// keeps saying so in words.
func geoAgeOf(group string) (key string, mixed bool) {
	g := strings.ToLower(strings.TrimSpace(group))
	if g == "" {
		return "unknown", false
	}
	seen := []string{}
	for _, part := range strings.Split(g, "/") {
		part = strings.TrimSpace(part)
		if part == "" {
			continue
		}
		for _, r := range geoAgeRules {
			if strings.Contains(part, r.needle) {
				key := r.key
				dup := false
				for _, s := range seen {
					if s == key {
						dup = true
					}
				}
				if !dup {
					seen = append(seen, key)
				}
				break
			}
		}
	}
	if len(seen) == 0 {
		return "unknown", false
	}
	if len(seen) == 1 {
		return seen[0], false
	}
	// Several ages: colour it as the OLDEST, which is the convention on a
	// printed sheet for an undifferentiated basement-plus-cover unit, and say
	// it is mixed.
	best := seen[0]
	for _, k := range seen[1:] {
		if geoAgeByKey[k].Rank > geoAgeByKey[best].Rank {
			best = k
		}
	}
	return best, true
}

// ---- lithology (FGDC-STD-013-2006 §37 pattern families) -------------------

// geoLithologies is the pattern legend, in the order the panel prints it.
var geoLithologies = []struct{ Key, Label, Desc string }{
	{"alluvium", "Unconsolidated sediment", "sand, gravel, alluvium, dunes"},
	{"sandstone", "Sandstone", "sandstone, quartzite, arenite"},
	{"mudrock", "Mudrock", "shale, siltstone, claystone, marl"},
	{"carbonate", "Carbonate", "limestone, dolomite, marble, chert"},
	{"intrusive", "Intrusive igneous", "granite, granodiorite, syenite, gabbro"},
	{"volcanic", "Volcanic", "lava, tuff, metavolcanics"},
	{"metamorphic", "Metamorphic", "schist, gneiss, migmatite, amphibolite"},
	{"ultramafic", "Ultramafic / ophiolite", "peridotite, pyroxenite, ophiolite"},
	{"ironstone", "Iron formation", "BIF, ferruginous chert, laterite"},
	{"mixed", "Mixed / undifferentiated", "the sheet does not separate these"},
}

// geoLithRules reads the unit's NAME (English and French) — the name is what
// describes rock. Order matters: the first hit wins, so a distinctive rock
// beats a generic one ("banded iron" before "iron", "ophiolite" before "mafic").
var geoLithRules = []struct{ needle, key string }{
	{"argilit", "mudrock"},
	{"banded iron", "ironstone"}, {"ferruginous", "ironstone"}, {"laterite", "ironstone"},
	{"ophiolite", "ultramafic"}, {"ultramafic", "ultramafic"}, {"peridotit", "ultramafic"},
	{"pyroxenit", "ultramafic"}, {"anorthosit", "ultramafic"},
	// ABOVE {"sediment", "mixed"} at the bottom of this list, and that placement
	// is the rule. A meta-sediment is a METAMORPHIC rock — the sheet has told us
	// what happened to it — whereas the "sediment" rule down there means "the
	// sheet declines to say which sediment". Read in the other order, five of
	// the GST's belt units ("Meta-sediment - meta-igneous complex") came back
	// with the undifferentiated hatch while the sheet was being perfectly
	// specific.
	{"meta-sediment", "metamorphic"}, {"metasediment", "metamorphic"},
	{"granulite", "metamorphic"}, {"charnockit", "metamorphic"}, {"migmatit", "metamorphic"},
	{"anatexite", "metamorphic"}, {"embrechite", "metamorphic"},
	// BEFORE the bare "schist": every one of these contains it, so a rule
	// placed after it can never fire. "Volcano-sedimentary greenschist
	// assemblage" is a metavolcanic pile, not a schist belt, and until
	// 2026-08-12 it came back `metamorphic` while the {"greenschist",
	// "volcanic"} rule sat lower down the list looking like a decision.
	// TestGeoLithRuleOrderHasNoDeadRules now fails on that shape.
	{"greenschist", "volcanic"}, {"volcano-sediment", "volcanic"},
	{"gneiss", "metamorphic"}, {"schist", "metamorphic"}, {"schiste", "metamorphic"},
	{"micaschist", "metamorphic"}, {"amphibolit", "metamorphic"}, {"leptynite", "metamorphic"},
	{"phyllite", "metamorphic"}, {"slate", "metamorphic"}, {"metamorphic", "metamorphic"},
	{"cristallophyllien", "metamorphic"},
	{"metavolcanic", "volcanic"}, {"volcanic", "volcanic"},
	{"volcanique", "volcanic"}, {"basalt", "volcanic"}, {"rhyolit", "volcanic"},
	{"tuff", "volcanic"},
	{"granite", "intrusive"}, {"granodiorit", "intrusive"}, {"syenit", "intrusive"},
	{"gabbro", "intrusive"}, {"intrusion", "intrusive"}, {"intrusive", "intrusive"},
	{"diorit", "intrusive"}, {"basique", "intrusive"}, {"dolerit", "intrusive"},
	// "Granitoid" is the survey's word for a granite-family plutonic rock it
	// declines to name more precisely, and "felsic igneous" likewise — both are
	// still statements that the rock is felsic and igneous, so they resolve
	// rather than falling off the end into the mixed hatch. Neither infers an
	// intrusive setting the sheet did not give: the GST prints these on plutons
	// ("Synorogenic (foliated) granitoides"), and where it means lava it says
	// rhyolite, nephelinite or pyroclastics, all of which match above.
	{"granitoid", "intrusive"}, {"granitoide", "intrusive"}, {"felsic igneous", "intrusive"},
	{"marble", "carbonate"}, {"marbre", "carbonate"}, {"limestone", "carbonate"},
	{"calcaire", "carbonate"}, {"dolomit", "carbonate"}, {"calc-silicate", "carbonate"},
	{"chert", "carbonate"}, {"coral", "carbonate"}, {"carbonate", "carbonate"},
	{"quartzit", "sandstone"}, {"sandstone", "sandstone"}, {"gres", "sandstone"},
	{"greywacke", "sandstone"}, {"arenite", "sandstone"},
	{"shale", "mudrock"}, {"siltstone", "mudrock"}, {"mudstone", "mudrock"},
	{"claystone", "mudrock"}, {"argilit", "mudrock"}, {"argile", "mudrock"},
	{"marl", "mudrock"}, {"clay", "mudrock"}, {"gypsum", "mudrock"},
	{"alluvi", "alluvium"}, {"dune", "alluvium"}, {"sand sheet", "alluvium"},
	{"colluvium", "alluvium"}, {"gravel", "alluvium"}, {"sable", "alluvium"},
	{"tillit", "alluvium"}, {"glacial", "alluvium"}, {"fluvio-glaciaire", "alluvium"},
	{"lacustrine", "mudrock"}, {"lacustre", "mudrock"}, {"conglomerat", "sandstone"},
	{"sand", "alluvium"}, {"silt", "mudrock"},
	// LAST, and deliberately: "mixed" here is a RULE, not the fallback. A unit
	// the sheet itself calls undifferentiated sediment has an answer — "the
	// sheet does not separate these" — and it must be distinguishable from a
	// unit whose vocabulary we simply never added, which lands on the same
	// key by falling off the end of this list. geoVocabAudit reports the
	// second and not the first.
	{"sediment", "mixed"}, {"sedimentaire", "mixed"},
}

// geoLithHit returns the lithology key for one string and the index of the
// rule that produced it, or ("", -1) if no rule fires. The audit and the
// classifier MUST share this: an audit that re-implements the scan would drift
// from it and start certifying a mapping nobody uses.
func geoLithHit(s string) (string, int) {
	s = strings.ToLower(s)
	for i, r := range geoLithRules {
		if strings.Contains(s, r.needle) {
			return r.key, i
		}
	}
	return "", -1
}

// geoLithOf classifies a unit by name, falling back to its group.
func geoLithOf(name, group string, codes []string) string {
	key, _ := geoLithResolve(name, group, codes)
	return key
}

// geoLithOfHint is geoLithOf for a sheet that ships a lithology column of its
// own; see geoLithResolveHint for why the column is consulted LAST.
func geoLithOfHint(name, group, lithHint string, codes []string) string {
	key, _ := geoLithResolveHint(name, group, lithHint, codes)
	return key
}

// geoLithResolve is geoLithOf plus the one bit geoLithOf throws away: whether
// any rule fired at all.
//
// "mixed" has two completely different meanings and they must not be confused.
// It is an ANSWER when the sheet itself declines to separate the rock (a
// merged class spanning two lithologies, or a unit the sheet calls
// undifferentiated), and it is a GAP when nothing in geoLithRules matched the
// sheet's words — which on screen is the same hatch, i.e. exactly the failure
// shape this codebase keeps paying for: a no-op that reads as an answer.
// `resolved` is false only in the second case, and geoVocabAudit reports it.
func geoLithResolve(name, group string, codes []string) (key string, resolved bool) {
	return geoLithResolveHint(name, group, "", codes)
}

// geoLithResolveHint is the scan proper: name, then group, then the sheet's own
// lithology column if it has one.
//
// A class that merges units of DIFFERENT lithologies is "mixed" and gets the
// mixed hatch — the same rule as a mixed age, for the same reason: the sheet
// does not say, so the map must not either.
//
// THE HINT IS CONSULTED LAST, and that order is the whole design. A vector
// sheet like the GST's carries a `lithology` field listing EVERY constituent of
// a unit in no particular order ("Syenite, gabbro, pyroxenite, nepheline
// syenite"), so a first-match scan over it answers with whichever minor phase
// happens to be spelled first — the Mbozi syenite-gabbro ring complex comes
// back `ultramafic` off the word "pyroxenite". The unit's NAME is the survey's
// own summary of what the rock is, and it wins for the same reason it wins on
// the scanned sheets. The hint only rescues names that are pure geography or
// pure structure ("Mafic complex Nyabuyonza"), where the column is the only
// place the survey states a rock at all.
func geoLithResolveHint(name, group, lithHint string, codes []string) (key string, resolved bool) {
	hit := func(s string) string { k, _ := geoLithHit(s); return k }
	// A merged class prints its members separated by "/" in the name too.
	if len(codes) > 1 && strings.Contains(name, "/") {
		keys := map[string]bool{}
		for _, part := range strings.Split(name, "/") {
			if k := hit(part); k != "" {
				keys[k] = true
			}
		}
		if len(keys) > 1 {
			return "mixed", true
		}
		for k := range keys {
			return k, true
		}
	}
	if k := hit(name); k != "" {
		return k, true
	}
	if k := hit(group); k != "" {
		return k, true
	}
	if k := hit(lithHint); k != "" {
		return k, true
	}
	return "mixed", false
}

// ---- the vocabulary's own gaps, reported out loud -------------------------
//
// geoAgeRules and geoLithRules are a hand-written vocabulary of the words TWO
// sheets happen to print. A third sheet — a Portuguese one, a USGS one, a
// re-vectorized legend with new wording — does not fail: it comes back age
// "unknown" (a flat grey polygon) and lithology "mixed" (the generic sparse
// hatch), which on screen is indistinguishable from the deliberate, correct
// rendering of a genuinely undated, genuinely undifferentiated unit. Nobody
// gets an error. Nobody gets a log line. The map just quietly says less than
// it knows, in the visual language it uses for saying something.
//
// So the gap reports itself. `geoVocabAudit` walks a sheet's catalogue and
// hands back the strings that produced no rule — and the raw group/name string
// IS the deliverable, because it is the exact thing a maintainer pastes into
// geoAgeRules. A Go test over the shipped catalogues fails with that list, and
// `/api/geomap` ships the same summary under `unmapped` so the admin legend
// can say "3 classes on this sheet have no age rule" instead of drawing three
// grey polygons.
//
// What it must NOT do is guess. There is no rule here that infers an age from
// a unit's name, and there must not be: "Age not stated" is an honest answer
// the sheets really do give (Sudan's PZs is undifferentiated Palaeozoic
// because the survey could not date it), and papering over a missing rule with
// a plausible age would destroy the only distinction that matters here —
// between what the sheet does not say and what we have not taught it to read.

// geoVocabGap is one class whose wording the vocabulary does not cover.
type geoVocabGap struct {
	Code string `json:"code"`
	// "age" — no rule matched the group string, the class renders grey.
	// "lithology" — no rule matched name or group, it renders as mixed.
	// "age_ambiguous" — the group names two unrelated periods and the answer
	// came from the order of geoAgeRules rather than from a curated rule.
	Kind string `json:"kind"`
	// Text is the sheet's own string, verbatim: the thing to write a rule for.
	Text string `json:"text"`
	// Note says what happened instead, in words a maintainer can act on.
	Note string `json:"note,omitempty"`
}

// geoVocabReport is one sheet's audit. It is small enough to ride in the
// catalogue API on every request.
type geoVocabReport struct {
	Sheet     string        `json:"sheet,omitempty"`
	Classes   int           `json:"classes"`
	Age       int           `json:"age"`           // classes with no age rule
	Lith      int           `json:"lithology"`     // classes with no lithology rule
	Ambiguous int           `json:"age_ambiguous"` // answered by rule order alone
	Gaps      []geoVocabGap `json:"gaps,omitempty"`
}

func (r geoVocabReport) clean() bool { return r.Age == 0 && r.Lith == 0 && r.Ambiguous == 0 }

// String is the maintainer-facing form: one line per gap, each ending in the
// string to write a rule for.
func (r geoVocabReport) String() string {
	if r.clean() {
		return fmt.Sprintf("%s: %d classes, vocabulary complete", r.Sheet, r.Classes)
	}
	var b strings.Builder
	fmt.Fprintf(&b, "%s: %d of %d classes have no age rule, %d no lithology rule, %d answered by rule order",
		r.Sheet, r.Age, r.Classes, r.Lith, r.Ambiguous)
	for _, g := range r.Gaps {
		fmt.Fprintf(&b, "\n  %-13s %-10s %q", g.Code, g.Kind, g.Text)
		if g.Note != "" {
			fmt.Fprintf(&b, "  — %s", g.Note)
		}
	}
	return b.String()
}

// geoVocabClass is the slice of a catalogue entry the audit needs.
type geoVocabClass struct {
	Code  string
	Name  string
	Group string
	Codes []string
	// Lithology is the sheet's own rock-description column, where it has one.
	// Empty for a scanned sheet, which has only what is printed on it.
	Lithology string
}

// geoVocabAuditClasses is the audit proper. It calls the SAME functions the
// renderer calls — an audit that re-implemented the scan would slowly certify
// a mapping nobody uses.
func geoVocabAuditClasses(sheet string, classes []geoVocabClass) geoVocabReport {
	rep := geoVocabReport{Sheet: sheet, Classes: len(classes)}
	for _, c := range classes {
		code := c.Code
		if code == "" {
			code = "(no code)"
		}
		if key, _ := geoAgeOf(c.Group); key == "unknown" {
			rep.Age++
			text := strings.TrimSpace(c.Group)
			note := "renders grey as 'Age not stated'"
			if text == "" {
				text = "(empty group)"
				note = "the catalogue carries no group string for this class; " +
					"fix the vectorizer, not the vocabulary"
			}
			rep.Gaps = append(rep.Gaps, geoVocabGap{code, "age", text, note})
		} else {
			// Ambiguity, checked per group part so a merged class's "A / B"
			// (which legitimately names two ages) is not reported as one.
			for _, part := range strings.Split(strings.ToLower(c.Group), "/") {
				part = strings.TrimSpace(part)
				m := geoAgeMatches(part)
				if len(m) < 2 {
					continue
				}
				won := geoAgeRules[m[0]]
				for _, i := range m[1:] {
					lost := geoAgeRules[i]
					if lost.key == won.key {
						continue // two spellings of one period
					}
					// A winning needle that CONTAINS the loser is the curated
					// specific-beats-generic case this file is built on
					// ("precambrien d" over "precambrien" over "cambrien").
					// Two DISJOINT needles is the trap: the string names two
					// unrelated periods and nothing decided between them
					// except where the rules happen to sit in the list.
					if strings.Contains(won.needle, lost.needle) {
						continue
					}
					rep.Ambiguous++
					rep.Gaps = append(rep.Gaps, geoVocabGap{code, "age_ambiguous", part,
						fmt.Sprintf("matches %q (%s) and %q (%s); rule order picked the first. "+
							"Add a rule for the compound term above both.",
							won.needle, won.key, lost.needle, lost.key)})
					break
				}
			}
		}
		if _, ok := geoLithResolveHint(c.Name, c.Group, c.Lithology, c.Codes); !ok {
			rep.Lith++
			text := strings.TrimSpace(c.Name)
			if text == "" {
				text = strings.TrimSpace(c.Group)
			}
			if text == "" {
				text = "(empty name and group)"
			}
			rep.Gaps = append(rep.Gaps, geoVocabGap{code, "lithology", text,
				"no rock word matched; renders with the 'mixed' hatch, " +
					"which is also what a genuinely undifferentiated unit gets"})
		}
	}
	return rep
}

// geoVocabAudit audits a raw <sheet>_classes.json blob.
func geoVocabAudit(sheet string, blob []byte) (geoVocabReport, error) {
	var doc struct {
		Classes []struct {
			Code      string   `json:"code"`
			Name      string   `json:"name"`
			Group     string   `json:"group"`
			Codes     []string `json:"codes"`
			Lithology string   `json:"lithology"`
		} `json:"classes"`
	}
	if err := json.Unmarshal(blob, &doc); err != nil {
		return geoVocabReport{Sheet: sheet}, err
	}
	cs := make([]geoVocabClass, 0, len(doc.Classes))
	for _, c := range doc.Classes {
		cs = append(cs, geoVocabClass{c.Code, c.Name, c.Group, c.Codes, c.Lithology})
	}
	return geoVocabAuditClasses(sheet, cs), nil
}

// geoStdLegend is what the API ships alongside the classes so the client and
// the admin panel never hard-code a colour or a pattern name.
func geoStdLegend() map[string]any {
	ages := make([]map[string]any, 0, len(geoAges))
	for _, a := range geoAges {
		ages = append(ages, map[string]any{
			"key": a.Key, "label": a.Label, "color": a.Color, "rank": a.Rank,
		})
	}
	liths := make([]map[string]any, 0, len(geoLithologies))
	for _, l := range geoLithologies {
		liths = append(liths, map[string]any{"key": l.Key, "label": l.Label, "desc": l.Desc})
	}
	return map[string]any{
		"ages":       ages,
		"lithology":  liths,
		"color_std":  "ICS/CGMW International Chronostratigraphic Chart (v2023)",
		"patternstd": "FGDC-STD-013-2006 §37 geologic map symbolization",
		// The contact model, shipped ONCE rather than per pair: a junction's
		// meaning is a property of the two LITHOLOGIES, so the client indexes
		// these by lithology pair and every contact on every sheet reads its
		// grade out of the same table. See srv/geomap_contacts.go.
		"contact_rules":       geoContactRulesJSON(),
		"contact_commodities": geoContactCommodities(),
		// What the model is WORTH, measured, beside what it claims. See
		// srv/geomap_scores.go: a grade with no score beside it reads as a
		// ranking, because three dots cannot read as anything else.
		"affinity_skill": geoAffinityScoresJSON(),
	}
}

// geoMapStandardise decorates a sheet's catalogue with the shared legend keys.
//
// Done here, on the served copy, rather than in scripts/geomaps/vectorize.py,
// on purpose: the mapping is a *presentation* decision over the sheet's own
// words, and it must be changeable without re-running a 40-minute vectorize or
// invalidating a single tile. The tiles carry `code`; everything below is
// derived from the catalogue the code indexes into, and the client joins them.
//
// It never removes anything. `color` (the printed ink) stays exactly as it was
// — see the "as printed" mode in geomap.js.
func geoMapStandardise(blob []byte) []byte {
	var doc map[string]any
	if err := json.Unmarshal(blob, &doc); err != nil {
		return blob
	}
	classes, ok := doc["classes"].([]any)
	if !ok {
		return blob
	}
	str := func(m map[string]any, k string) string {
		s, _ := m[k].(string)
		return s
	}
	vocab := make([]geoVocabClass, 0, len(classes))
	for _, raw := range classes {
		c, ok := raw.(map[string]any)
		if !ok {
			continue
		}
		codes := []string{}
		if arr, ok := c["codes"].([]any); ok {
			for _, v := range arr {
				if s, ok := v.(string); ok {
					codes = append(codes, s)
				}
			}
		}
		ageKey, mixed := geoAgeOf(str(c, "group"))
		age := geoAgeByKey[ageKey]
		c["age"] = ageKey
		c["age_label"] = age.Label
		c["age_rank"] = age.Rank
		c["age_mixed"] = mixed
		c["ics_color"] = age.Color
		lith, lithOK := geoLithResolveHint(str(c, "name"), str(c, "group"), str(c, "lithology"), codes)
		c["lith"] = lith
		// Per class, so a tip or a legend row can mark ITSELF as unrendered
		// rather than only the sheet totalling its gaps. Absent (not false)
		// when the vocabulary covered the class: this flags a defect, and a
		// `"lith_unmapped": false` on 63 healthy classes is noise.
		if ageKey == "unknown" {
			c["age_unmapped"] = true
		}
		if !lithOK {
			c["lith_unmapped"] = true
		}
		vocab = append(vocab, geoVocabClass{str(c, "code"), str(c, "name"), str(c, "group"), codes, str(c, "lithology")})
	}
	doc["std"] = geoStdLegend()
	// The gap ships with the data it is a gap in. A maintainer looking at the
	// catalogue must be able to see "3 classes on this sheet have no age rule"
	// without diffing the grey polygons against the printed sheet by eye.
	// Sheet name comes from the catalogue itself, so it is right even when the
	// caller does not know it.
	sheet, _ := doc["sheet"].(string)
	rep := geoVocabAuditClasses(sheet, vocab)
	rep.Sheet = "" // already the document's own `sheet`
	doc["unmapped"] = rep
	out, err := json.Marshal(doc)
	if err != nil {
		return blob
	}
	return out
}
