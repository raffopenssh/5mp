package srv

import (
	"encoding/json"
	"os"
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
