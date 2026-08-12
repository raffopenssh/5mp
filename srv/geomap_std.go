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
var geoAgeRules = []struct{ needle, key string }{
	{"quaternai", "quaternary"}, {"quaternar", "quaternary"}, {"neo-tchadien", "quaternary"},
	{"neogene", "neogene"}, {"miocene", "neogene"}, {"pliocene", "neogene"},
	{"paleogene", "paleogene"}, {"eocene", "paleogene"}, {"oligocene", "paleogene"},
	{"tertiaire", "tertiary"}, {"tertiary", "tertiary"},
	{"cretace", "cretaceous"}, {"cretaceous", "cretaceous"},
	{"jurassic", "jurassic"}, {"jurassique", "jurassic"},
	{"permo-trias", "triassic"}, {"trias", "triassic"},
	{"secondaire", "mesozoic"}, {"mesozoic", "mesozoic"},
	{"permian", "permian"}, {"permien", "permian"},
	{"carbonifer", "carboniferous"},
	{"devonian", "devonian"}, {"devonien", "devonian"},
	{"silurian", "silurian"}, {"silurien", "silurian"},
	{"ordovician", "ordovician"}, {"ordovicien", "ordovician"},
	{"cambro-ordovician", "ordovician"},
	{"palaeozoic", "paleozoic"}, {"paleozoic", "paleozoic"}, {"primaire", "paleozoic"},
	// Pan-African is the Neoproterozoic-to-earliest-Cambrian orogenic cycle;
	// on the Sudan sheet every "Pan-African …" group is Neoproterozoic ground.
	{"pan-african", "neoproterozoic"},
	{"neoproterozoic", "neoproterozoic"},
	{"precambrien a", "neoproterozoic"},
	{"upper proterozoic", "neoproterozoic"},
	{"middle proterozoic", "mesoproterozoic"}, {"mesoproterozoic", "mesoproterozoic"},
	{"lower proterozoic", "paleoproterozoic"}, {"paleoproterozoic", "paleoproterozoic"},
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
				dup := false
				for _, s := range seen {
					if s == r.key {
						dup = true
					}
				}
				if !dup {
					seen = append(seen, r.key)
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
	{"granulite", "metamorphic"}, {"charnockit", "metamorphic"}, {"migmatit", "metamorphic"},
	{"anatexite", "metamorphic"}, {"embrechite", "metamorphic"},
	{"gneiss", "metamorphic"}, {"schist", "metamorphic"}, {"schiste", "metamorphic"},
	{"micaschist", "metamorphic"}, {"amphibolit", "metamorphic"}, {"leptynite", "metamorphic"},
	{"phyllite", "metamorphic"}, {"slate", "metamorphic"}, {"metamorphic", "metamorphic"},
	{"cristallophyllien", "metamorphic"},
	{"metavolcanic", "volcanic"}, {"volcano-sediment", "volcanic"}, {"volcanic", "volcanic"},
	{"volcanique", "volcanic"}, {"basalt", "volcanic"}, {"rhyolit", "volcanic"},
	{"tuff", "volcanic"}, {"greenschist", "volcanic"},
	{"granite", "intrusive"}, {"granodiorit", "intrusive"}, {"syenit", "intrusive"},
	{"gabbro", "intrusive"}, {"intrusion", "intrusive"}, {"intrusive", "intrusive"},
	{"diorit", "intrusive"}, {"basique", "intrusive"}, {"dolerit", "intrusive"},
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
}

// geoLithOf classifies a unit by name, falling back to its group.
//
// A class that merges units of DIFFERENT lithologies is "mixed" and gets the
// mixed hatch — the same rule as a mixed age, for the same reason: the sheet
// does not say, so the map must not either.
func geoLithOf(name, group string, codes []string) string {
	hit := func(s string) string {
		s = strings.ToLower(s)
		for _, r := range geoLithRules {
			if strings.Contains(s, r.needle) {
				return r.key
			}
		}
		return ""
	}
	// A merged class prints its members separated by "/" in the name too.
	if len(codes) > 1 && strings.Contains(name, "/") {
		keys := map[string]bool{}
		for _, part := range strings.Split(name, "/") {
			if k := hit(part); k != "" {
				keys[k] = true
			}
		}
		if len(keys) > 1 {
			return "mixed"
		}
		for k := range keys {
			return k
		}
	}
	if k := hit(name); k != "" {
		return k
	}
	if k := hit(group); k != "" {
		return k
	}
	return "mixed"
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
		c["lith"] = geoLithOf(str(c, "name"), str(c, "group"), codes)
	}
	doc["std"] = geoStdLegend()
	out, err := json.Marshal(doc)
	if err != nil {
		return blob
	}
	return out
}
