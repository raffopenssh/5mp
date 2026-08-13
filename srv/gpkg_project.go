package srv

// An embedded QGIS project inside the GeoPackage.
//
// Styles alone are not enough. A GeoPackage has no layer order, no visibility
// and no grouping, so "Add layers" on a fully-loaded export puts 163,000 fire
// detections and 4,800 trajectories on top of everything at continental zoom:
// the file is correct and the map is an orange smear. The KML export solved
// this years ago with folders and <visibility>0, and it is the same problem.
//
// QGIS reads a project stored in the file's own `qgis_projects` table
// (Project > Open From > GeoPackage, or double-clicking the entry in the
// Browser). It is a .qgz — a zip around a .qgs XML document — stored hex
// encoded, which is what QGIS itself writes there.
//
// The layer XML is the same renderer/labeling body already generated for
// layer_styles, so there is exactly one description of what a fire looks like.
// A user who ignores the project and adds a single layer by hand still gets its
// symbology from layer_styles; the project adds order, groups, visibility and
// the temporal setup on top.

import (
	"archive/zip"
	"bytes"
	"encoding/hex"
	"fmt"
	"strings"
)

// gpkgLayerSpec is one entry in the project tree.
type gpkgLayerSpec struct {
	Table    string
	Title    string // display name in the layer panel
	Group    string
	Geometry string // Point | Line | Polygon
	WKBType  string
	QML      string
	Visible  bool
	Opacity  float64
	// Abstract is what the layer SAYS ABOUT ITSELF. GDAL maps a GeoPackage
	// `gpkg_contents.description` onto the layer abstract, so a user who adds
	// the table by hand sees it — but a project's <maplayer> overrides the
	// layer's metadata with the project's own, and an empty element there
	// SILENTLY BLANKS IT. The embedded project is the path we tell people to
	// use, so without this the disclaimer ("an affinity is an inference", "this
	// file is a view") survives only on the route nobody takes.
	Abstract string
	// TemporalField turns on QGIS's temporal controller for the layer, so the
	// time slider animates it with no setup. Only set where a single instant
	// per feature is meaningful (a detection); a trajectory has a range.
	TemporalStart string
	TemporalEnd   string
}

const qgsSRS = `<spatialrefsys nativeFormat="Wkt">
  <wkt>` + wgs84WKTEscaped + `</wkt>
  <proj4>+proj=longlat +datum=WGS84 +no_defs</proj4>
  <srsid>3452</srsid><srid>4326</srid><authid>EPSG:4326</authid>
  <description>WGS 84</description>
  <projectionacronym>longlat</projectionacronym>
  <ellipsoidacronym>EPSG:7030</ellipsoidacronym>
  <geographicflag>true</geographicflag>
</spatialrefsys>`

const wgs84WKTEscaped = `GEOGCS[&quot;WGS 84&quot;,DATUM[&quot;WGS_1984&quot;,SPHEROID[&quot;WGS 84&quot;,6378137,298.257223563,AUTHORITY[&quot;EPSG&quot;,&quot;7030&quot;]],AUTHORITY[&quot;EPSG&quot;,&quot;6326&quot;]],PRIMEM[&quot;Greenwich&quot;,0,AUTHORITY[&quot;EPSG&quot;,&quot;8901&quot;]],UNIT[&quot;degree&quot;,0.0174532925199433,AUTHORITY[&quot;EPSG&quot;,&quot;9122&quot;]],AUTHORITY[&quot;EPSG&quot;,&quot;4326&quot;]]`

// qmlBody strips the <qgis> wrapper off a generated QML, leaving the
// renderer/labeling elements that belong inside a <maplayer>.
func qmlBody(qml string) string {
	if i := strings.Index(qml, "<qgis "); i >= 0 {
		if j := strings.Index(qml[i:], ">"); j >= 0 {
			qml = qml[i+j+1:]
		}
	}
	return strings.TrimSuffix(strings.TrimSpace(strings.TrimSuffix(strings.TrimSpace(qml), "</qgis>")), "</qgis>")
}

func qgsLayerID(table string) string { return table + "_5mp_export" }

// qgsTemporal wires the layer into QGIS's temporal controller, so the time
// slider animates the export with no setup.
//
// The mode values are QgsVectorLayerTemporalProperties::TemporalMode and they
// are NOT zero-based-by-convenience: 0 is ModeFixedTemporalRange, which ignores
// the fields completely. Writing 0/1 for instant/range therefore produced a
// layer that reported itself as temporal, showed the fields in the dialog, and
// silently rendered every feature at every timestep — a wrong answer that looks
// like a working one. 1 = instant from one field, 2 = start+end fields.
func qgsTemporal(l gpkgLayerSpec) string {
	if l.TemporalStart == "" {
		return `<temporal enabled="0" mode="0"><fixedRange><start></start><end></end></fixedRange></temporal>`
	}
	mode := 1 // ModeFeatureDateTimeInstantFromField
	end := ""
	if l.TemporalEnd != "" {
		mode = 2 // ModeFeatureDateTimeStartAndEndFromFields
		end = l.TemporalEnd
	}
	return fmt.Sprintf(`<temporal startField="%s" endField="%s" durationUnit="min" limitMode="0" accumulateFeatures="0" durationField="" fixedDuration="0" enabled="1" mode="%d">
  <fixedRange><start></start><end></end></fixedRange>
</temporal>`, l.TemporalStart, end, mode)
}

// buildQGISProject renders the .qgs document. gpkgName is the file's own base
// name: the datasource is relative to the project, and the project lives inside
// the GeoPackage, so "./<name>.gpkg" is a self-reference. That is why the
// download filename must equal the on-disk basename.
func buildQGISProject(projectName, gpkgName string, layers []gpkgLayerSpec, bbox [4]float64) string {
	var tree, maps strings.Builder
	groups := []string{}
	byGroup := map[string][]gpkgLayerSpec{}
	for _, l := range layers {
		if _, seen := byGroup[l.Group]; !seen {
			groups = append(groups, l.Group)
		}
		byGroup[l.Group] = append(byGroup[l.Group], l)
	}

	// The layer tree is drawn top-first, and `layers` is given in draw order
	// (bottom first, like a map), so it is reversed here rather than at every
	// call site.
	for gi := len(groups) - 1; gi >= 0; gi-- {
		g := groups[gi]
		items := byGroup[g]
		anyVisible := false
		for _, l := range items {
			if l.Visible {
				anyVisible = true
			}
		}
		checked := "Qt::Unchecked"
		if anyVisible {
			checked = "Qt::Checked"
		}
		fmt.Fprintf(&tree, `<layer-tree-group name=%q expanded="1" checked=%q>`+"\n", g, checked)
		for i := len(items) - 1; i >= 0; i-- {
			l := items[i]
			c := "Qt::Unchecked"
			if l.Visible {
				c = "Qt::Checked"
			}
			fmt.Fprintf(&tree,
				`<layer-tree-layer source="./%s|layername=%s" providerKey="ogr" expanded="0" id="%s" name="%s" checked="%s" legend_split_behavior="0" patch_size="-1,-1"><customproperties><Option/></customproperties></layer-tree-layer>`+"\n",
				gpkgName, l.Table, qgsLayerID(l.Table), xmlEscape(l.Title), c)
		}
		tree.WriteString("</layer-tree-group>\n")
	}

	for _, l := range layers {
		op := l.Opacity
		if op == 0 {
			op = 1
		}
		fmt.Fprintf(&maps, `<maplayer type="vector" geometry="%s" wkbType="%s" labelsEnabled="1"
  simplifyDrawingTol="1" simplifyLocal="1" simplifyAlgorithm="0" simplifyDrawingHints="1" simplifyMaxScale="1"
  minScale="0" maxScale="0" hasScaleBasedVisibilityFlag="0" symbologyReferenceScale="-1" readOnly="0"
  autoRefreshMode="Disabled" autoRefreshTime="0" refreshOnNotifyEnabled="0" styleCategories="AllStyleCategories">
  <id>%s</id>
  <datasource>./%s|layername=%s</datasource>
  <layername>%s</layername>
  <srs>%s</srs>
  <provider encoding="UTF-8">ogr</provider>
  <flags><Identifiable>1</Identifiable><Removable>1</Removable><Searchable>1</Searchable><Private>0</Private></flags>
  %s
  %s
  <layerOpacity>%g</layerOpacity>
  <blendMode>0</blendMode>
  <featureBlendMode>0</featureBlendMode>
  %s
</maplayer>
`, l.Geometry, l.WKBType, qgsLayerID(l.Table), gpkgName, l.Table, xmlEscape(l.Title),
			qgsSRS, qgsTemporal(l), qmlBody(l.QML), op, qgsLayerMetadata(l))
	}

	return fmt.Sprintf(`<!DOCTYPE qgis PUBLIC 'http://mrcc.com/qgis.dtd' 'SYSTEM'>
<qgis version="3.34.0" projectname="%[1]s">
<homePath path=""/>
<title>%[2]s</title>
<transaction mode="Disabled"/>
<projectCrs>%[3]s</projectCrs>
<layer-tree-group>
%[4]s<custom-order enabled="0"/>
</layer-tree-group>
<snapping-settings enabled="0" tolerance="12" unit="1" mode="2" type="1"/>
<relations/>
<mapcanvas name="theMapCanvas" annotationsVisible="1">
  <units>degrees</units>
  <extent><xmin>%[5]g</xmin><ymin>%[6]g</ymin><xmax>%[7]g</xmax><ymax>%[8]g</ymax></extent>
  <rotation>0</rotation>
  <destinationsrs>%[3]s</destinationsrs>
  <rendermaptile>0</rendermaptile>
</mapcanvas>
<projectlayers>
%[9]s</projectlayers>
<ProjectViewSettings rotation="0" UseProjectScales="0">
  <Scales/>
  <DefaultViewExtent xmin="%[5]g" ymin="%[6]g" xmax="%[7]g" ymax="%[8]g">%[3]s</DefaultViewExtent>
</ProjectViewSettings>
<ProjectTimeSettings timeStep="1" timeStepUnit="d" frameRate="10" cumulativeTemporalRange="0"/>
<properties>
  <Gui><CanvasColour type="QString">#18181b</CanvasColour></Gui>
  <Measure><Ellipsoid type="QString">EPSG:7030</Ellipsoid></Measure>
  <PositionPrecision><Automatic type="bool">true</Automatic></PositionPrecision>
</properties>
<visibility-presets/>
<layerorder/>
</qgis>
`, xmlEscape(projectName), xmlEscape(projectName), qgsSRS, tree.String(),
		bbox[0], bbox[1], bbox[2], bbox[3], maps.String())
}

// qgsLayerMetadata carries the layer's own abstract INTO the project.
//
// Opening the file through its embedded project is the route the docs and the
// download page point at, and QGIS reads the <maplayer>'s <resourceMetadata>
// in preference to the provider's. Omitting the element does not fall back:
// the abstract comes out empty, and the abstract is where every disclaimer in
// this export lives ("grade is this app's own inference ... NOT a record of
// any deposit", "OTHER ORGANISATIONS' observations"). Verified by rendering,
// not by reading the spec.
func qgsLayerMetadata(l gpkgLayerSpec) string {
	if l.Abstract == "" {
		return ""
	}
	return fmt.Sprintf(`<resourceMetadata>
    <identifier>%s</identifier>
    <title>%s</title>
    <type>dataset</type>
    <language>ENG</language>
    <abstract>%s</abstract>
  </resourceMetadata>`, xmlEscape(l.Table), xmlEscape(l.Title), xmlEscape(l.Abstract))
}

// qgzBytes zips a .qgs into the .qgz container QGIS expects.
func qgzBytes(qgs string) ([]byte, error) {
	var buf bytes.Buffer
	zw := zip.NewWriter(&buf)
	f, err := zw.Create("5mp.qgs")
	if err != nil {
		return nil, err
	}
	if _, err := f.Write([]byte(qgs)); err != nil {
		return nil, err
	}
	if err := zw.Close(); err != nil {
		return nil, err
	}
	return buf.Bytes(), nil
}

// writeQGISProject stores the project in the GeoPackage. Content is hex text,
// not a blob: that is what QGIS writes and what it expects to read back.
func (w *gpkgWriter) writeQGISProject(projectName, gpkgName string, layers []gpkgLayerSpec, bbox [4]float64) error {
	if len(layers) == 0 {
		return nil
	}
	qgz, err := qgzBytes(buildQGISProject(projectName, gpkgName, layers, bbox))
	if err != nil {
		return err
	}
	if _, err := w.tx.Exec(`CREATE TABLE IF NOT EXISTS qgis_projects
		(name TEXT PRIMARY KEY, metadata BLOB, content BLOB)`); err != nil {
		return err
	}
	_, err = w.tx.Exec(`INSERT OR REPLACE INTO qgis_projects (name, metadata, content) VALUES (?,?,?)`,
		projectName, `{"last_modified_user": "5MP Conservation Monitoring"}`, hex.EncodeToString(qgz))
	return err
}

// gpkgProjectSpecs is the project's opinion about the export: draw order,
// grouping, and — the part that matters — what is switched ON.
//
// Order is bottom-first, like a map. What is checked mirrors the app's own
// default view and the KML export's <visibility> flags: the area, its geography
// and the analysed events. The two raw firehoses (163k fire detections for one
// park-year; millions for an AOI) ship switched OFF, because a layer that
// covers the whole canvas the moment the file opens teaches the user that the
// export is broken. They are one checkbox away, in a group that says what they
// are.
//
// Only layers actually present are emitted — `present` comes from the writer's
// non-empty layers, so a park with no watersheds gets no empty group.
func gpkgProjectSpecs(present map[string]bool, isAOI bool) []gpkgLayerSpec {
	all := []gpkgLayerSpec{
		{Table: "watershed_upstream", Title: "Upstream watershed", Group: "Water", Geometry: "Polygon", WKBType: "MultiPolygon", QML: styleBasinUpstream(), Visible: false},
		{Table: "waterbodies", Title: "Waterbodies", Group: "Water", Geometry: "Polygon", WKBType: "Unknown", QML: styleWater(), Visible: true},
		{Table: "lakes", Title: "Lakes", Group: "Water", Geometry: "Polygon", WKBType: "Unknown", QML: styleLakes(), Visible: true},
		{Table: "watershed_rivers", Title: "Watershed rivers", Group: "Water", Geometry: "Line", WKBType: "Unknown", QML: styleBasinRivers(), Visible: false},
		{Table: "watershed_downstream", Title: "Downstream trace", Group: "Water", Geometry: "Line", WKBType: "Unknown", QML: styleBasinDownstream(), Visible: false},
		{Table: "rivers", Title: "Rivers (reaches)", Group: "Water", Geometry: "Line", WKBType: "Unknown", QML: styleRivers(), Visible: false},
		{Table: "rivers_merged", Title: "Rivers", Group: "Water", Geometry: "Line", WKBType: "LineString", QML: styleRivers(), Visible: true},

		{Table: "roads", Title: "Roads", Group: "Infrastructure", Geometry: "Line", WKBType: "Unknown", QML: styleRoads(), Visible: true},
		{Table: "patrol_tracks", Title: "Patrol tracks", Group: "Infrastructure", Geometry: "Line", WKBType: "Unknown", QML: stylePatrolTracks(), Visible: false},
		{Table: "patrol_effort", Title: "Patrol effort (monthly)", Group: "Infrastructure", Geometry: "Polygon", WKBType: "Polygon", QML: stylePatrolEffort(), Visible: false, Opacity: 0.8, TemporalStart: "period_start"},
		{Table: "airstrips", Title: "Airstrips", Group: "Infrastructure", Geometry: "Point", WKBType: "Point", QML: styleAirstrips(), Visible: true},

		{Table: "deforestation", Title: "Deforestation", Group: "Human activity", Geometry: "Polygon", WKBType: "MultiPolygon", QML: styleDeforestation(), Visible: true, TemporalStart: "start_date", TemporalEnd: "end_date"},
		{Table: "settlements", Title: "Settlements", Group: "Human activity", Geometry: "Polygon", WKBType: "MultiPolygon", QML: styleSettlements(), Visible: true},

		// The raw detections are the one layer that must arrive switched OFF:
		// millions of coincident orange points drawn on top of everything are
		// not a map, and a first impression of "this file is broken" is hard to
		// undo. They stay one checkbox away, named so it is obvious what they
		// are. The trajectories are ON — same story, 38k features instead of
		// 6.9M, and a fire layer that shows nothing until you go looking is its
		// own kind of wrong answer.
		{Table: "fire_detections", Title: "Fire detections (raw VIIRS)", Group: "Fire", Geometry: "Point", WKBType: "Point", QML: styleFireDetections(), Visible: false, TemporalStart: "acq_datetime_utc"},
		{Table: "fire_trajectories", Title: "Fire trajectories", Group: "Fire", Geometry: "Line", WKBType: "Unknown", QML: styleFireTrajectory(), Visible: true, TemporalStart: "start_date", TemporalEnd: "end_date"},

		{Table: "places", Title: "Places", Group: "Reference", Geometry: "Point", WKBType: "Point", QML: stylePlaces(), Visible: true},
	}
	// The area outline is drawn last (on top) and is always on: it is the
	// answer to "which of these things is the area".
	if isAOI {
		all = append(all, gpkgLayerSpec{Table: "aoi_boundary", Title: "Area of interest", Group: "Reference", Geometry: "Polygon", WKBType: "MultiPolygon", QML: styleAOI(), Visible: true})
	} else {
		all = append(all, gpkgLayerSpec{Table: "boundary", Title: "Protected area", Group: "Reference", Geometry: "Polygon", WKBType: "MultiPolygon", QML: styleBoundary(), Visible: true})
	}
	out := make([]gpkgLayerSpec, 0, len(all))
	for _, l := range all {
		if present[l.Table] {
			out = append(out, l)
		}
	}
	return out
}
