package srv

// QGIS QML styles for the GeoPackage export.
//
// A GeoPackage carries no cartography of its own, so a plain export opens as N
// layers in N random pastel colours — the user then has to rebuild, by hand and
// from memory, the colour language the app already speaks (fire red, canopy
// loss purple, settlement amber, water blue). QGIS looks for a `layer_styles`
// table in the same file and applies the useAsDefault row on open, so the
// styling ships with the data and no .qml sidecars can go missing.
//
// The QML is generated rather than embedded as files because almost every layer
// is the same handful of shapes with a different colour, and a categorized
// renderer differs only in its category list. Keep it in the Option-map form
// (QGIS >= 3.24); the old flat `<prop k= v=>` form is deprecated and drops
// silently on newer builds.

import (
	"fmt"
	"strings"
)

// 5MP palette, matching globe.html / the KML styles.
const (
	colFire       = "239,68,68"
	colFireOld    = "148,110,100"
	colDefo       = "168,85,247"
	colSettle     = "245,158,11"
	colRiver      = "59,130,246"
	colWater      = "3,169,244"
	colRoad       = "210,180,140"
	colBoundary   = "34,197,94"
	colPlace      = "229,231,235"
	colPatrol     = "139,195,74"
	colBasinUp    = "56,189,248"
	colBasinDown  = "14,116,144"
	colAirstrip   = "250,204,21"
	colFireDetect = "255,120,40"
)

func qmlDoc(body string) string {
	return `<!DOCTYPE qgis PUBLIC 'http://mrcc.com/qgis.dtd' 'SYSTEM'>` + "\n" +
		`<qgis version="3.34.0" styleCategories="Symbology|Labeling|Rendering">` + "\n" +
		body + "\n</qgis>\n"
}

func qmlOpt(name, value string) string {
	return fmt.Sprintf(`<Option name=%q type="QString" value=%q/>`, name, value)
}

func qmlSymbol(kind, name string, opts ...string) string {
	class := map[string]string{"marker": "SimpleMarker", "line": "SimpleLine", "fill": "SimpleFill"}[kind]
	return fmt.Sprintf(`<symbol type=%q name=%q alpha="1" clip_to_extent="1" force_rhr="0" frame_rate="10" is_animated="0">
  <layer class=%q enabled="1" locked="0" pass="0">
    <Option type="Map">
      %s
    </Option>
  </layer>
</symbol>`, kind, name, class, strings.Join(opts, "\n      "))
}

func qmlPointSymbol(name, rgb string, size float64, outline string) string {
	return qmlSymbol("marker", name,
		qmlOpt("name", "circle"),
		qmlOpt("color", rgb+",255"),
		qmlOpt("outline_color", outline),
		qmlOpt("outline_width", "0.2"),
		qmlOpt("outline_width_unit", "MM"),
		qmlOpt("size", fmt.Sprintf("%g", size)),
		qmlOpt("size_unit", "MM"))
}

func qmlLineSymbol(name, rgb string, width float64) string {
	return qmlSymbol("line", name,
		qmlOpt("line_color", rgb+",255"),
		qmlOpt("line_width", fmt.Sprintf("%g", width)),
		qmlOpt("line_width_unit", "MM"),
		qmlOpt("capstyle", "round"),
		qmlOpt("joinstyle", "round"))
}

func qmlFillSymbol(name, rgb string, fillAlpha int, outlineWidth float64) string {
	return qmlSymbol("fill", name,
		qmlOpt("color", fmt.Sprintf("%s,%d", rgb, fillAlpha)),
		qmlOpt("style", "solid"),
		qmlOpt("outline_color", rgb+",255"),
		qmlOpt("outline_style", "solid"),
		qmlOpt("outline_width", fmt.Sprintf("%g", outlineWidth)),
		qmlOpt("outline_width_unit", "MM"))
}

func qmlSingle(symbol string) string {
	return `<renderer-v2 type="singleSymbol" forceraster="0" symbollevels="0" enableorderby="0">
  <symbols>
` + symbol + `
  </symbols>
</renderer-v2>`
}

// qmlLabels draws `field` as a white halo-buffered label. Used where the name
// is the whole point of the layer (places, rivers, roads).
func qmlLabels(field string, size float64, rgb string, placement int) string {
	return fmt.Sprintf(`<labeling type="simple">
  <settings calloutType="simple">
    <text-style fontFamily="Arial" fontSize="%g" fontSizeUnit="Point" textColor="%s,255" fieldName=%q isExpression="0" forcedBold="0" forcedItalic="0">
      <text-buffer bufferDraw="1" bufferSize="1" bufferSizeUnits="MM" bufferColor="0,0,0,200" bufferOpacity="1" bufferJoinStyle="128"/>
    </text-style>
    <placement placement="%d" placementFlags="10" dist="1" distUnits="MM" overlapHandling="PreventOverlap" lineAnchorPercent="0.5"/>
    <rendering scaleMin="0" scaleMax="0" fontMinPixelSize="3" fontMaxPixelSize="10000" upsidedownLabels="0" labelPerPart="0"/>
  </settings>
</labeling>`, size, rgb, field, placement)
}

// qmlCategorized renders one symbol per value of `field`. `cats` is an ordered
// list of {value, label, symbol-rgb}; unmatched values fall through to the
// layer's base colour rather than disappearing.
type qmlCat struct {
	Value string
	Label string
	RGB   string
}

func qmlCategorized(field, kind string, cats []qmlCat, fallbackRGB string, size float64, fillAlpha int) string {
	var catXML, symXML strings.Builder
	mk := func(name, rgb string) string {
		switch kind {
		case "marker":
			return qmlPointSymbol(name, rgb, size, "0,0,0,80")
		case "line":
			return qmlLineSymbol(name, rgb, size)
		default:
			return qmlFillSymbol(name, rgb, fillAlpha, size)
		}
	}
	for i, c := range cats {
		fmt.Fprintf(&catXML, `<category render="1" value=%q label=%q symbol="%d"/>`+"\n", c.Value, c.Label, i)
		symXML.WriteString(mk(fmt.Sprint(i), c.RGB) + "\n")
	}
	fmt.Fprintf(&catXML, `<category render="1" value="" label="other" symbol="%d"/>`+"\n", len(cats))
	symXML.WriteString(mk(fmt.Sprint(len(cats)), fallbackRGB) + "\n")
	return fmt.Sprintf(`<renderer-v2 type="categorizedSymbol" attr=%q forceraster="0" symbollevels="0" enableorderby="0">
  <categories>
%s  </categories>
  <symbols>
%s  </symbols>
</renderer-v2>`, field, catXML.String(), symXML.String())
}

// ---- per-layer styles ----------------------------------------------------

func styleBoundary() string {
	return qmlDoc(qmlSingle(qmlSymbol("fill", "0",
		qmlOpt("color", colBoundary+",25"),
		qmlOpt("style", "solid"),
		qmlOpt("outline_color", colBoundary+",255"),
		qmlOpt("outline_style", "solid"),
		qmlOpt("outline_width", "0.86"),
		qmlOpt("outline_width_unit", "MM"))))
}

// Fire trajectories are coloured by the group's behaviour type, which is the
// thing an analyst actually sorts on (a transhumance line and a contained
// wildfire mean different work).
func styleFireTrajectory() string {
	// Values are the v5 pipeline's actual group_type vocabulary; the previous
	// list had five names the pipeline never emits, so 94% of trajectories
	// (everything but transhumance) drew in the fallback colour.
	return qmlDoc(qmlCategorized("group_type", "line", []qmlCat{
		{"transhumance", "Transhumance", "251,146,60"},
		{"spreading_fire", "Spreading fire", colFire},
		{"spot_fire", "Spot fire", "250,204,21"},
		{"local_fire", "Local fire", "217,119,6"},
		{"external_fire", "External fire", "148,163,184"},
		{"management_controlled", "Management burn", "132,204,22"},
	}, colFire, 0.5, 0))
}

// Detections come from the area's BOUNDING BOX, so the layer legitimately
// contains points outside the area (kept as context, flagged in_area). The
// renderer says so: inside is the hot orange the app uses, outside a muted
// ember. A single symbol would quietly present the bbox corners as if they were
// the area — for XSA that is more than half the points.
func styleFireDetections() string {
	return qmlDoc(qmlCategorized("in_area", "marker", []qmlCat{
		{"1", "Inside the area", colFireDetect},
		{"0", "Nearby (within the bounding box)", "120,72,46"},
	}, colFireDetect, 1.2, 0))
}

func styleDeforestation() string {
	// The attr is an expression, not a bare column: an event the pipeline
	// itself flagged as questioned (needs_review) must not render in the same
	// confident colour as a verified clearing — the 2023 Kafia Kingi "logging"
	// block is 282 of 314 km² and would otherwise dominate the map as fact.
	// Category values below are the classifier's actual vocabulary
	// (deforestation_events.classification: natural / slash_burn / logging /
	// encroachment) — the previous list named five values that did not occur,
	// so the largest real categories all fell into "other".
	return qmlDoc(qmlCategorized(
		"CASE WHEN needs_review = 1 THEN 'questioned' ELSE classification END",
		"fill", []qmlCat{
			{"slash_burn", "Slash-and-burn", "217,119,6"},
			{"logging", "Logging", "132,90,223"},
			{"encroachment", "Encroachment", "239,68,68"},
			{"natural", "Natural", "74,222,128"},
			{"questioned", "Questioned (needs review)", "148,163,184"},
		}, colDefo, 0.26, 110))
}

func styleSettlements() string {
	// Values are park_settlements.classification as it exists in the data;
	// temporary_camp is the single biggest class (10,583 of 16,890) and used
	// to render as "other".
	return qmlDoc(qmlCategorized("classification", "fill", []qmlCat{
		{"temporary_camp", "Temporary camp", "234,179,8"},
		{"village", "Village", colSettle},
		{"town", "Town", "249,115,22"},
		{"settlement", "Settlement", "217,180,140"},
		{"residential", "Residential", "196,150,110"},
		{"pastoral", "Pastoral", "163,230,53"},
		{"agricultural", "Agricultural", "134,199,90"},
		{"mining", "Mining", "168,85,247"},
		{"fishing", "Fishing", "56,189,248"},
	}, colSettle, 0.26, 110))
}

func styleRivers() string {
	return qmlDoc(qmlSingle(qmlLineSymbol("0", colRiver, 0.46)) + "\n" + qmlLabels("name", 8, colRiver, 3))
}

func styleRoads() string {
	return qmlDoc(qmlCategorized("highway_type", "line", []qmlCat{
		{"trunk", "Trunk", "251,191,36"},
		{"primary", "Primary", "245,158,11"},
		{"secondary", "Secondary", "217,119,6"},
		{"tertiary", "Tertiary", "180,150,110"},
		{"residential", "Residential", "190,175,150"},
		{"unclassified", "Unclassified", colRoad},
		{"track", "Track", "160,140,110"},
		{"path", "Path", "140,125,100"},
	}, colRoad, 0.4, 0) + "\n" + qmlLabels("name", 7, "222,207,180", 3))
}

func stylePlaces() string {
	return qmlDoc(qmlSingle(qmlPointSymbol("0", colPlace, 1.6, "0,0,0,160")) + "\n" +
		qmlLabels("name", 8.5, colPlace, 6))
}

func styleWater() string {
	return qmlDoc(qmlSingle(qmlFillSymbol("0", colWater, 90, 0.2)) + "\n" + qmlLabels("name", 8, colWater, 6))
}

func styleLakes() string {
	return qmlDoc(qmlSingle(qmlFillSymbol("0", colWater, 120, 0.26)) + "\n" + qmlLabels("name", 8, colWater, 6))
}

func stylePatrolEffort() string {
	return qmlDoc(qmlSingle(qmlFillSymbol("0", colPatrol, 60, 0.2)))
}

func stylePatrolTracks() string {
	return qmlDoc(qmlSingle(qmlLineSymbol("0", colPatrol, 0.36)))
}

func styleAirstrips() string {
	return qmlDoc(qmlSingle(qmlSymbol("marker", "0",
		qmlOpt("name", "triangle"),
		qmlOpt("color", colAirstrip+",255"),
		qmlOpt("outline_color", "0,0,0,180"),
		qmlOpt("outline_width", "0.2"),
		qmlOpt("size", "3"),
		qmlOpt("size_unit", "MM"))) + "\n" + qmlLabels("name", 8, colAirstrip, 6))
}

func styleBasinUpstream() string {
	return qmlDoc(qmlSingle(qmlSymbol("fill", "0",
		qmlOpt("color", colBasinUp+",30"),
		qmlOpt("style", "solid"),
		qmlOpt("outline_color", colBasinUp+",200"),
		qmlOpt("outline_style", "dash"),
		qmlOpt("outline_width", "0.4"),
		qmlOpt("outline_width_unit", "MM"))))
}

func styleBasinDownstream() string {
	return qmlDoc(qmlSingle(qmlLineSymbol("0", colBasinDown, 0.8)) + "\n" + qmlLabels("river", 8, colBasinDown, 3))
}

func styleBasinRivers() string {
	return qmlDoc(qmlSingle(qmlLineSymbol("0", colBasinUp, 0.3)))
}

func styleAOI() string {
	return qmlDoc(qmlSingle(qmlSymbol("fill", "0",
		qmlOpt("color", "251,191,36,20"),
		qmlOpt("style", "solid"),
		qmlOpt("outline_color", "251,191,36,255"),
		qmlOpt("outline_style", "solid"),
		qmlOpt("outline_width", "0.9"),
		qmlOpt("outline_width_unit", "MM"))))
}

// The exported viewport: a dashed outline with no fill. It is a frame, not a
// feature — filling it would tint everything the export is about.
func styleViewFrame() string {
	return qmlDoc(qmlSingle(qmlSymbol("fill", "0",
		qmlOpt("color", "0,0,0,0"),
		qmlOpt("style", "no"),
		qmlOpt("outline_color", "148,163,184,255"),
		qmlOpt("outline_style", "dash"),
		qmlOpt("outline_width", "0.5"),
		qmlOpt("outline_width_unit", "MM"))))
}

// Detections in a VIEW export have no in_area column to categorise on (a view
// is a rectangle, not an area), so they get one ember symbol.
func styleViewDetections() string {
	return qmlDoc(qmlSingle(qmlSymbol("marker", "0",
		qmlOpt("color", colFireDetect+",210"),
		qmlOpt("name", "circle"),
		qmlOpt("outline_style", "no"),
		qmlOpt("size", "1.2"),
		qmlOpt("size_unit", "MM"))))
}
