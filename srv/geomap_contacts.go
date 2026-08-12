package srv

// Contacts: where two mapped units MEET, and what that junction can host.
//
// WHY A CONTACT IS NOT A UNIT
//
// The affinity model in scripts/geomaps/legend.py answers "which rock can host
// gold". The next question a geologist asks is "where do two of them meet":
// granite against a greenstone belt is the classic orogenic-gold setting, an
// intrusive against a carbonate is the skarn setting, listwaenite gold sits on
// an ophiolite thrust. That prospectivity belongs to the BOUNDARY, not to
// either polygon — the matrix can say "greenstone hosts gold" and "late
// granite hosts gold" and still cannot say "the line between them is worth
// more than either". Nothing keyed on a single unit can state it, which is why
// this is its own model and not another commodity row.
//
// WHY THE GRADING LIVES HERE AND NOT IN legend.py
//
// A unit affinity is keyed by (sheet, code), which is the sheet's own
// vocabulary, so it belongs with the sheet in legend.py. A CONTACT affinity is
// keyed by a pair of LITHOLOGIES — "intrusive against carbonate" — and the
// lithology of a class is decided here (geoLithResolveHint, geomap_std.go)
// from the FGDC rules, not by the Python build. Keying a Python table on
// lithology would mean re-implementing that classifier on the other side of
// the build, i.e. a second copy of the vocabulary that drifts. So the geometry
// is derived in the build (scripts/geomaps/contacts.py, which knows nothing
// about commodities) and the MEANING is attached here, where the lithologies
// are decided — the same split, and for the same reason, as the ICS colours
// and the FGDC ornament in geomap_std.go: a grading change must never
// invalidate a tile.
//
// SAME SCALE, NEVER A SECOND ONE. Weight is the catalogue's 1-3: 3 = the
// classic setting, 2 = plausible, 1 = weak or derived. The matrix, the grade
// floor, the key strip and the map tip all already render that scale; a
// contact that graded 0-100 would make the reader learn a second one.
//
// AND IT IS STILL AN INFERENCE. "Rocks of these two kinds, in contact, are the
// setting for X" is a statement about lithology exactly like the unit-level
// one. Nothing here counts, ranks or locates a deposit, and every surface that
// shows it says so.

import (
	"encoding/json"
	"fmt"
	"math"
	"os"
	"path/filepath"
	"sort"
	"strings"
)

// geoContactRule is one (lithology, lithology) -> commodity statement.
// The pair is UNORDERED: it is normalised on load, so "intrusive/carbonate"
// and "carbonate/intrusive" cannot disagree.
type geoContactRule struct {
	A, B      string
	Commodity string
	Weight    int
	Why       string
}

// geoContactRules is the model. Every line is a textbook ore-deposit setting
// stated as a pair of rock types; none of them is an occurrence.
var geoContactRules = []geoContactRule{
	// --- Intrusive against everything: the hydrothermal engine ------------
	{"intrusive", "volcanic", "gold", 3,
		"granite against a greenstone/metavolcanic belt: the classic orogenic-gold contact, " +
			"where the pluton drives fluid through the belt's structures"},
	{"intrusive", "volcanic", "copper", 2,
		"intrusion against volcanics: the porphyry and skarn-copper setting"},
	{"intrusive", "metamorphic", "gold", 2,
		"pluton against a schist belt: contact-parallel shears carry lode gold"},
	{"intrusive", "carbonate", "copper", 3,
		"intrusive against carbonate: the classic skarn setting for copper and polymetallic ore"},
	{"intrusive", "carbonate", "iron", 2,
		"carbonate skarn: magnetite is the common iron form of the same contact"},
	{"intrusive", "carbonate", "gold", 2,
		"gold skarn and carbonate replacement along the intrusive front"},
	{"intrusive", "mudrock", "lithium", 2,
		"granite margin into pelite: where pegmatite and greisen fields sit"},
	{"intrusive", "mudrock", "uranium", 1,
		"hydrothermal uranium at a granite/pelite front"},
	{"intrusive", "sandstone", "uranium", 2,
		"granite against sandstone: a uranium source rock against a host aquifer"},
	{"intrusive", "metamorphic", "lithium", 2,
		"pegmatite fields sit in the metamorphic aureole of an evolved granite"},
	{"intrusive", "intrusive", "rare_earth", 2,
		"the margin of a younger alkaline intrusion against older granite: " +
			"the carbonatite and rare-metal association"},
	{"intrusive", "ultramafic", "cobalt", 2,
		"felsic intrusion against ultramafics: nickel-cobalt sulphide remobilisation"},

	// --- Ultramafic contacts: the mafic ore suite ------------------------
	{"ultramafic", "metamorphic", "gold", 3,
		"ophiolite thrust against the metamorphic belt: listwaenite alteration " +
			"along the sole is gold-bearing"},
	{"ultramafic", "metamorphic", "cobalt", 2,
		"serpentinised ultramafics against the belt: chromite, nickel and cobalt"},
	{"ultramafic", "volcanic", "cobalt", 3,
		"komatiite/ultramafic against volcanics: the nickel-cobalt sulphide contact"},
	{"ultramafic", "volcanic", "gold", 2,
		"ultramafic-volcanic contacts within a greenstone belt carry lode gold"},
	{"ultramafic", "sandstone", "cobalt", 1,
		"weathered ultramafic under cover: lateritic nickel-cobalt"},
	{"ultramafic", "alluvium", "cobalt", 1,
		"ultramafic shedding into alluvium: lateritic and eluvial nickel-cobalt"},

	// --- Belt-internal contacts ------------------------------------------
	{"volcanic", "metamorphic", "gold", 3,
		"metavolcanics against metasediments: the sheared belt contact that hosts " +
			"most Nubian- and Birimian-style lode gold"},
	{"volcanic", "metamorphic", "copper", 2,
		"volcanic-sedimentary contact: the volcanogenic massive-sulphide horizon"},
	{"volcanic", "ironstone", "iron", 3,
		"banded iron formation against its volcanic pile: the Algoman iron contact"},
	{"volcanic", "ironstone", "gold", 2,
		"BIF against volcanics: the sulphidised iron-formation gold host"},
	{"volcanic", "carbonate", "copper", 2,
		"volcanics against carbonate: replacement copper at the contact"},
	{"metamorphic", "ironstone", "iron", 2,
		"iron formation within the metamorphic belt"},
	{"metamorphic", "carbonate", "gold", 2,
		"marble against schist: carbonate replacement along the belt"},
	{"metamorphic", "metamorphic", "lithium", 1,
		"pegmatite swarms follow contacts inside the high-grade basement"},

	// --- Cover against basement: the unconformity ------------------------
	{"sandstone", "metamorphic", "uranium", 3,
		"sandstone on crystalline basement: the unconformity-related uranium contact"},
	{"sandstone", "intrusive", "gold", 1,
		"cover onlapping a mineralised pluton: eluvial ground at the edge"},
	{"sandstone", "mudrock", "uranium", 2,
		"sandstone against an impermeable pelite: where a roll front is pinned"},
	{"alluvium", "metamorphic", "gold", 2,
		"alluvium shedding directly off a lode belt: the placer contact an " +
			"artisanal rush works first"},
	{"alluvium", "volcanic", "gold", 2,
		"alluvium against a greenstone belt: proximal placer ground"},
	{"alluvium", "intrusive", "gold", 1,
		"alluvium off a granite: weaker, but the eluvial edge of a lode system"},
	{"alluvium", "sandstone", "diamond", 2,
		"recent alluvium cutting a diamondiferous sandstone reservoir: " +
			"the working secondary ground"},
	{"sandstone", "sandstone", "diamond", 1,
		"one clastic reservoir against another: the reworked diamond contact"},
	{"alluvium", "ultramafic", "gold", 1,
		"placer ground below a listwaenite-bearing ophiolite"},
	{"carbonate", "mudrock", "copper", 1,
		"carbonate against shale: sediment-hosted stratiform copper"},
	{"ironstone", "sandstone", "iron", 1,
		"iron formation under clastic cover: the enriched supergene contact"},
}

// geoContactRuleIndex is "lithA|lithB" (sorted) -> the rules for that junction,
// strongest first. Built once; the pair is normalised so a caller cannot get a
// different answer by asking in the other order.
var geoContactRuleIndex = func() map[string][]geoContactRule {
	m := map[string][]geoContactRule{}
	for _, r := range geoContactRules {
		m[geoLithPairKey(r.A, r.B)] = append(m[geoLithPairKey(r.A, r.B)], r)
	}
	for k := range m {
		rs := m[k]
		sort.SliceStable(rs, func(i, j int) bool { return rs[i].Weight > rs[j].Weight })
	}
	return m
}()

func geoLithPairKey(a, b string) string {
	if a > b {
		a, b = b, a
	}
	return a + "|" + b
}

// geoContactAffinity grades one junction of two lithologies.
func geoContactAffinity(lithA, lithB string) []map[string]any {
	rules := geoContactRuleIndex[geoLithPairKey(lithA, lithB)]
	if len(rules) == 0 {
		return nil
	}
	out := make([]map[string]any, 0, len(rules))
	for _, r := range rules {
		out = append(out, map[string]any{
			"commodity": r.Commodity, "weight": r.Weight, "why": r.Why,
		})
	}
	return out
}

// geoContactBest is the strongest weight any commodity gets at this junction,
// 0 if the model says nothing about it. Rules are sorted strongest-first, so
// this is a lookup and a read, not a scan.
func geoContactBest(lithA, lithB string) int {
	rules := geoContactRuleIndex[geoLithPairKey(lithA, lithB)]
	if len(rules) == 0 {
		return 0
	}
	return rules[0].Weight
}

// geoContactRulesJSON is the model as the client receives it: one entry per
// graded lithology junction, carrying its commodities and its reasons.
//
// It ships ONCE, in the shared legend — not per pair and not per sheet. ~40
// rules against 882 unit pairs across three sheets: attaching the prose to
// every pair would repeat one sentence hundreds of times, and a rule change
// would then be a change to every sheet's payload.
func geoContactRulesJSON() []map[string]any {
	keys := make([]string, 0, len(geoContactRuleIndex))
	for k := range geoContactRuleIndex {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	out := make([]map[string]any, 0, len(keys))
	for _, k := range keys {
		rs := geoContactRuleIndex[k]
		aff := make([]map[string]any, 0, len(rs))
		for _, r := range rs {
			aff = append(aff, map[string]any{
				"commodity": r.Commodity, "weight": r.Weight, "why": r.Why,
			})
		}
		liths := strings.SplitN(k, "|", 2)
		out = append(out, map[string]any{
			"pair": k, "lith_a": liths[0], "lith_b": liths[1],
			"best": rs[0].Weight, "affinity": aff,
		})
	}
	return out
}

// geoContactPair is one unit pair as served.
//
// DELIBERATELY THIN. An earlier cut carried both names, both ages, both
// lithologies and the graded affinity WITH its reasons on every pair — 563
// pairs x ~200 bytes of prose that is the same sentence over and over, because
// the reason belongs to the pair of ROCK TYPES, not to the pair of units. The
// catalogue already tells the client the age, lithology and name of a code, so
// a contact repeats none of it: it carries the two codes, the measured length,
// and nothing else. The grading rules ride ONCE in the shared legend
// (`std.contact_rules`) and the client indexes them by lithology pair, which
// is one map build at load and an O(1) lookup thereafter.
type geoContactPair struct {
	CodeA string  `json:"a"`
	CodeB string  `json:"b"`
	KM    float64 `json:"km"`
}

// geoContactDoc is a sheet's contact catalogue, as served.
//
// The counts are computed here rather than left to the client: `n_graded` and
// `graded_km` are what the UI says out loud ("381 of 563 junctions are graded"),
// and a number the reader is shown must not depend on the client having
// rebuilt the rule index correctly.
type geoContactDoc struct {
	Sheet    string           `json:"sheet"`
	N        int              `json:"n_contacts"`
	NGraded  int              `json:"n_graded"`
	TotalKM  float64          `json:"total_km"`
	GradedKM float64          `json:"graded_km"`
	Quality  json.RawMessage  `json:"quality,omitempty"`
	Pairs    []geoContactPair `json:"pairs"`

	// graded is the server's own view of which pairs have an affinity, kept
	// out of the payload: the client re-derives it from the rules. It exists
	// so the counts above and the GeoPackage agree with the map.
	graded map[string]int `json:"-"`
}

// geoLoadContacts reads <sheet>_contacts.json and grades every pair against
// the sheet's own classes.
//
// The file carries geometry-free pairs only — the geometry is in the tiles —
// so this is a few hundred rows and costs nothing to hold. It is read once per
// process, beside the catalogue, and the grading is applied on the served copy
// exactly like geoMapStandardise: changing a rule above must never require a
// re-tile.
func geoLoadContacts(dir, sheet string, classes []byte) (*geoContactDoc, error) {
	path := filepath.Join(dir, sheet+"_contacts.json")
	blob, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	var raw struct {
		Sheet    string          `json:"sheet"`
		TotalKM  float64         `json:"total_km"`
		Quality  json.RawMessage `json:"quality"`
		Contacts []struct {
			A  string  `json:"a"`
			B  string  `json:"b"`
			KM float64 `json:"km"`
		} `json:"contacts"`
	}
	if err := json.Unmarshal(blob, &raw); err != nil {
		return nil, fmt.Errorf("%s: %w", path, err)
	}

	// The class list, already standardised (age + lith attached). Only the
	// lithology is needed here: the age and the name are the client's to look
	// up from the same catalogue, and copying them onto every pair is how the
	// payload doubled for no new information.
	var cat struct {
		Classes []struct {
			Code string `json:"code"`
			Lith string `json:"lith"`
		} `json:"classes"`
	}
	_ = json.Unmarshal(classes, &cat)
	type meta struct{ lith string }
	byCode := map[string]meta{}
	for _, c := range cat.Classes {
		byCode[c.Code] = meta{c.Lith}
	}

	doc := &geoContactDoc{Sheet: sheet, Quality: raw.Quality, graded: map[string]int{}}
	for _, c := range raw.Contacts {
		ma, mb := byCode[c.A], byCode[c.B]
		if best := geoContactBest(ma.lith, mb.lith); best > 0 {
			doc.graded[c.A+"|"+c.B] = best
			doc.NGraded++
			doc.GradedKM += c.KM
		}
		doc.TotalKM += c.KM
		doc.Pairs = append(doc.Pairs, geoContactPair{CodeA: c.A, CodeB: c.B, KM: c.KM})
	}
	doc.N = len(doc.Pairs)
	doc.GradedKM = math.Round(doc.GradedKM*10) / 10
	doc.TotalKM = math.Round(doc.TotalKM*10) / 10

	// INVARIANT 1, at the serving end. A file that parsed but produced no
	// pairs is a broken build, not a sheet whose units never touch, and a
	// zero-length layer offered as if it were complete is exactly the "no-op
	// that reads as an answer" this codebase keeps paying for.
	if doc.N == 0 {
		return nil, fmt.Errorf("%s: contact file has no pairs; rebuild with scripts/geomaps/contacts.py %s", path, sheet)
	}
	return doc, nil
}

// geoContactCommodities is the union of commodities any graded contact on
// these sheets offers — what the UI may present as a contact filter. Derived,
// never a fixed list: adding a rule above must not need a second edit.
func geoContactCommodities() []string {
	seen := map[string]bool{}
	for _, r := range geoContactRules {
		seen[r.Commodity] = true
	}
	out := make([]string, 0, len(seen))
	for k := range seen {
		out = append(out, k)
	}
	sort.Strings(out)
	return out
}
