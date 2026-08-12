package srv

import (
	"encoding/json"
	"os"
	"strings"
	"testing"
)

// The mapping is a first-match scan over strings the two sheets print, so the
// failures it can have are ordering failures — and they are silent (a wrong
// but plausible colour). These pin the ones that have already bitten.
func TestGeoAgeOf(t *testing.T) {
	cases := []struct {
		group, want string
		mixed       bool
	}{
		// "precambrien" contains "cambrien": the crystalline basement of the
		// CAR sheet must not come back Cambrian.
		{"Precambrien D - facies cristallophyllien", "paleoproterozoic", false},
		{"Precambrien A - groupe superieur", "neoproterozoic", false},
		{"Pan-African metavolcanics", "neoproterozoic", false},
		{"Quaternaire", "quaternary", false},
		{"Secondaire", "mesozoic", false},
		{"Primaire", "paleozoic", false},
		{"Archaean craton", "archean", false},
		{"Permo-Triassic", "triassic", false},
		// A hyphenated SPAN is one unit straddling a boundary, not a merged
		// class, so it is NOT mixed and it does not take the oldest — each of
		// these is a curated line above both of its endpoints. Before
		// 2026-08-12 the Cambro-Ordovician rule sat BELOW {"ordovician"} and
		// could never fire; it was invisible because both answers agreed.
		{"Cambro-Ordovician", "ordovician", false},
		{"Jurassic-Cretaceous", "cretaceous", false},
		{"Tertiary-Quaternary", "quaternary", false},
		// A merged class names several ages; it takes the OLDEST and says so.
		{"Tertiary / Lower Proterozoic basement", "paleoproterozoic", true},
		{"Tertiary-Quaternary / Pan-African metasediments", "neoproterozoic", true},
		{"", "unknown", false},
	}
	for _, c := range cases {
		got, mixed := geoAgeOf(c.group)
		if got != c.want || mixed != c.mixed {
			t.Errorf("geoAgeOf(%q) = %q,%v; want %q,%v", c.group, got, mixed, c.want, c.mixed)
		}
	}
}

func TestGeoLithOf(t *testing.T) {
	cases := []struct{ name, group, want string }{
		{"Recent alluvium and wadi deposits", "Quaternary", "alluvium"},
		{"Abyad limestone", "Tertiary", "carbonate"},
		{"Banded iron formation", "Lower Proterozoic basement", "ironstone"},
		{"Ophiolite: mafic oceanic upper sequence", "Pan-African ophiolite", "ultramafic"},
		{"Younger intrusions (IYg granite, IYs syenite)", "Pan-African igneous", "intrusive"},
		{"Acidic metavolcanics", "Pan-African metavolcanics", "volcanic"},
		{"Gneiss (roots of the arc assemblage)", "Pan-African gneiss/amphibolite", "metamorphic"},
		// "greenschist" CONTAINS "schist", so the greenschist rule has to sit
		// above it or it can never fire. It did not until 2026-08-12: Sudan's
		// MSv, a volcano-sedimentary pile, came back `metamorphic` (wavy
		// dashes) while a {"greenschist", "volcanic"} line lower in the list
		// sat there looking like a decision that had been made.
		{"Volcano-sedimentary greenschist assemblage", "Pan-African metasediments", "volcanic"},
		{"Greenschist facies metabasalt", "Pan-African", "volcanic"},
		// …and the bare word still means what it says.
		{"Graphitic schist", "Lower Proterozoic basement", "metamorphic"},
		// argillite with carbonate nodules is a mudrock, not a carbonate
		{"Formations fluvio-glaciaires (argilites a nodules calcaires)", "Primaire", "mudrock"},
	}
	for _, c := range cases {
		if got := geoLithOf(c.name, c.group, nil); got != c.want {
			t.Errorf("geoLithOf(%q) = %q; want %q", c.name, got, c.want)
		}
	}
	// A merged class spanning two lithologies must not pick one.
	if got := geoLithOf("Marble / Quartzite", "x", []string{"A", "B"}); got != "mixed" {
		t.Errorf("merged lithology = %q; want mixed", got)
	}
}

// Every class of every installed sheet must resolve to a real legend entry —
// an unknown key would render as a missing pattern, i.e. an invisible unit.
func TestGeoStandardiseCoversInstalledSheets(t *testing.T) {
	liths := map[string]bool{}
	for _, l := range geoLithologies {
		liths[l.Key] = true
	}
	for _, s := range []string{"sudan", "car"} {
		blob, err := os.ReadFile("../data/geomaps/" + s + "_classes.json")
		if err != nil {
			t.Skipf("%s catalogue not built", s)
		}
		var d map[string]any
		if err := json.Unmarshal(geoMapStandardise(blob), &d); err != nil {
			t.Fatal(err)
		}
		if d["std"] == nil {
			t.Fatalf("%s: no std legend", s)
		}
		for _, raw := range d["classes"].([]any) {
			c := raw.(map[string]any)
			age, _ := c["age"].(string)
			if _, ok := geoAgeByKey[age]; !ok {
				t.Errorf("%s/%v: unknown age %q", s, c["code"], age)
			}
			if age == "unknown" {
				t.Errorf("%s/%v: undated (group %q)", s, c["code"], c["group"])
			}
			if lith, _ := c["lith"].(string); !liths[lith] {
				t.Errorf("%s/%v: unknown lithology %q", s, c["code"], lith)
			}
			if c["color"] == nil {
				t.Errorf("%s/%v: printed ink dropped", s, c["code"])
			}
		}
	}
}

// ---- the vocabulary's gaps have to be loud ---------------------------------

// THE POINT OF THIS TEST. geoAgeRules and geoLithRules are a hand-written
// vocabulary of the words two particular sheets happen to print. Nothing about
// a third sheet FAILS: its classes come back age "unknown" (grey) and lith
// "mixed" (the generic hatch), which on screen is exactly how the map states
// "this unit is genuinely undated and undifferentiated". A missing rule and a
// deliberate cartographic statement render identically — the recurring shape
// in this codebase, a no-op that reads as an answer.
//
// So the shipped catalogues are audited here, and the failure names the exact
// string to write a rule for. If this fails, ADD THE RULE. Do not relax the
// test: a sheet whose vocabulary we do not speak must not ship looking
// authoritative. And do not add a rule that GUESSES an age from a unit's name
// — "Age not stated" is an answer some units honestly have (Sudan's PZs), and
// the whole value here is that a missing rule stays distinguishable from it.
func TestShippedCataloguesHaveNoUnmappedVocabulary(t *testing.T) {
	for _, s := range []string{"sudan", "car"} {
		blob, err := os.ReadFile("../data/geomaps/" + s + "_classes.json")
		if err != nil {
			// A fresh checkout has the committed catalogues, but CI that
			// prunes data/ should skip rather than fail on an absent file.
			t.Skipf("%s catalogue not present: %v", s, err)
		}
		rep, err := geoVocabAudit(s, blob)
		if err != nil {
			t.Fatalf("%s: %v", s, err)
		}
		if rep.Classes == 0 {
			t.Fatalf("%s: catalogue parsed to zero classes — an audit over nothing "+
				"passes trivially, which is the failure mode this file exists to stop", s)
		}
		if !rep.clean() {
			t.Errorf("unmapped vocabulary — add a rule in geomap_std.go for each string below:\n%s", rep)
		}
		t.Logf("%s", rep)
	}
}

// The audit must actually be able to see a gap; a checker that reports clean
// on everything is worth less than no checker, because it certifies.
func TestVocabAuditSeesAGap(t *testing.T) {
	rep := geoVocabAuditClasses("probe", []geoVocabClass{
		// A plausible third sheet (southern Africa): neither string is in
		// either rule list, and neither renders as an error — grey and
		// generically hatched, which is why this has to be reported.
		{"Ka", "Bushveld layered complex", "Karoo Supergroup", nil},
		// Covered — must NOT be reported.
		{"QF", "Recent alluvium", "Quaternary", nil},
		// The distinction the whole audit exists for. This lands on lith
		// "mixed" like the Bushveld line above, but for the opposite reason:
		// {"sediment", "mixed"} is a RULE that fired, i.e. the sheet itself
		// says undifferentiated. Same pixel, different meaning, and only one
		// of the two is a defect.
		{"PZs", "Undifferentiated Palaeozoic sediments", "Palaeozoic", nil},
	})
	if rep.Age != 1 || rep.Lith != 1 {
		t.Fatalf("age gaps = %d, lith gaps = %d; want 1 and 1\n%s", rep.Age, rep.Lith, rep)
	}
	var sawGroup, sawName bool
	for _, g := range rep.Gaps {
		if g.Code == "QF" {
			t.Errorf("a covered class was reported as a gap: %+v", g)
		}
		// The raw string IS the deliverable — it is what a maintainer pastes
		// into geoAgeRules. A count alone would send them back to the sheet.
		if g.Kind == "age" && g.Text == "Karoo Supergroup" {
			sawGroup = true
		}
		if g.Kind == "lithology" && strings.Contains(g.Text, "Bushveld") {
			sawName = true
		}
	}
	if !sawGroup || !sawName {
		t.Errorf("the report must carry the offending strings verbatim; got %+v", rep.Gaps)
	}
	// PZs is the honest case: the sheet dates it and the sheet calls it
	// undifferentiated, so nothing about it is a gap. Reporting it would both
	// drown the real gaps and tempt someone to invent an age or a rock for a
	// unit that genuinely has neither.
	for _, g := range rep.Gaps {
		if g.Code == "PZs" {
			t.Errorf("PZs is answered, not unmapped; must not be a gap: %+v", g)
		}
	}
	if k, ok := geoLithResolve("Undifferentiated Palaeozoic sediments", "Palaeozoic", nil); k != "mixed" || !ok {
		t.Errorf(`"undifferentiated sediments" = %q,%v; want mixed,true — mixed as an ANSWER`, k, ok)
	}
	if k, ok := geoLithResolve("Bushveld layered complex", "Karoo Supergroup", nil); k != "mixed" || ok {
		t.Errorf("an unrecognised rock = %q,%v; want mixed,false — mixed as a GAP", k, ok)
	}
}

// An `age_ambiguous` is a group naming two unrelated periods where the answer
// came from where the rules happen to sit in the list. That is not a
// vocabulary gap, it is a coin toss wearing a decision's face.
func TestVocabAuditFlagsAnUncuratedSpan(t *testing.T) {
	orig := geoAgeRules
	var stripped []struct{ needle, key string }
	for _, r := range orig {
		if r.needle == "jurassic-cretaceous" {
			continue
		}
		stripped = append(stripped, r)
	}
	geoAgeRules = stripped
	defer func() { geoAgeRules = orig }()

	rep := geoVocabAuditClasses("probe", []geoVocabClass{
		{"JK", "Fluviatile sandstone", "Jurassic-Cretaceous", nil},
	})
	if rep.Ambiguous != 1 {
		t.Fatalf("an uncurated span must be flagged; got %s", rep)
	}
	// A merged class legitimately naming two ages is NOT ambiguous: the sheet
	// itself declines to separate them, geoAgeOf says so with age_mixed, and
	// reporting it would drown the real gaps.
	geoAgeRules = orig
	rep = geoVocabAuditClasses("probe", []geoVocabClass{
		{"TA/PLq", "Middle Abyad / Quartzite", "Tertiary / Lower Proterozoic basement",
			[]string{"TA", "PLq"}},
		// Specific-beats-generic, the pattern this whole file rests on:
		// "precambrien d" contains "precambrien" contains "cambrien". Curated,
		// not accidental — must not be reported.
		{"D", "Complexe de base indifferencie", "Precambrien D - facies cristallophyllien", nil},
	})
	if rep.Ambiguous != 0 {
		t.Errorf("a merged class and a curated containment must not be flagged: %s", rep)
	}
}

// A rule that can never fire is the same silent shape as a missing one: it
// sits in the list looking like a decision that has been made. Both the
// Cambro-Ordovician age rule and the greenschist lithology rule were sitting
// below a needle that contains them, and neither had ever fired.
func TestGeoRuleOrderHasNoDeadRules(t *testing.T) {
	for i, r := range geoLithRules {
		for j := 0; j < i; j++ {
			e := geoLithRules[j]
			if e.key == r.key || !strings.Contains(r.needle, e.needle) {
				continue
			}
			t.Errorf("lith rule %q→%q can never fire: %q→%q above it matches every string it does",
				r.needle, r.key, e.needle, e.key)
		}
	}
	for i, r := range geoAgeRules {
		for j := 0; j < i; j++ {
			e := geoAgeRules[j]
			if e.key == r.key || !strings.Contains(r.needle, e.needle) {
				continue
			}
			t.Errorf("age rule %q→%q can never fire: %q→%q above it matches every string it does",
				r.needle, r.key, e.needle, e.key)
		}
	}
}

// The gap has to reach a maintainer who is not running `go test`, so it rides
// in the catalogue the API already ships — counts plus the offending strings.
// No UI is invented here: the data is simply present instead of absent.
func TestStandardiseShipsTheUnmappedSummary(t *testing.T) {
	in := []byte(`{"sheet":"probe","classes":[
		{"code":"Ks","name":"Bushveld layered complex","group":"Karoo Supergroup","color":"#aabbcc"},
		{"code":"QF","name":"Recent alluvium","group":"Quaternary","color":"#ddeeff"}]}`)
	var d map[string]any
	if err := json.Unmarshal(geoMapStandardise(in), &d); err != nil {
		t.Fatal(err)
	}
	um, ok := d["unmapped"].(map[string]any)
	if !ok {
		t.Fatal("the catalogue must ship an `unmapped` summary, not just `std`")
	}
	if um["age"] != float64(1) || um["lithology"] != float64(1) {
		t.Errorf("unmapped counts = %v; want 1 age and 1 lithology", um)
	}
	gaps, _ := um["gaps"].([]any)
	if len(gaps) != 2 {
		t.Fatalf("want the offending strings, got %v", gaps)
	}
	if !strings.Contains(string(mustJSON(t, gaps)), "Karoo Supergroup") {
		t.Error("the summary must carry the sheet's own string — a count alone " +
			"tells a maintainer there is a problem but not what to write a rule for")
	}
	// Per class too, so a legend row can mark itself rather than only the
	// sheet totalling its defects.
	cs := d["classes"].([]any)
	if cs[0].(map[string]any)["age_unmapped"] != true {
		t.Error("the unmapped class must be flagged in place")
	}
	// Absent, not false, on a healthy class: 63 `"lith_unmapped": false` keys
	// would bury the two that matter.
	if _, present := cs[1].(map[string]any)["age_unmapped"]; present {
		t.Error("a covered class must not carry the flag at all")
	}
	// An unmapped class still renders — grey and generically hatched. It must
	// not vanish, and its age must stay the honest "unknown".
	if cs[0].(map[string]any)["age"] != "unknown" {
		t.Error("a missing rule must never be papered over with a guessed age")
	}
}

func mustJSON(t *testing.T, v any) []byte {
	t.Helper()
	b, err := json.Marshal(v)
	if err != nil {
		t.Fatal(err)
	}
	return b
}
