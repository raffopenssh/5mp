package srv

// The licence register: every third-party dataset, tile service, library and
// font this application serves, redistributes or was built from, with the
// terms its publisher actually granted.
//
// WHY ONE FILE. Attribution used to live in six places (map styles, the About
// modal, the Impressum, MBTiles metadata, GeoPackage headers, docs/agents/*)
// and each drifted on its own: the Impressum named nine sources when the app
// used twenty-five, the About modal linked FIRMS but not its citation, and the
// offline-tile builder shipped Google and Bing imagery whose terms forbid
// exactly that. A source is now declared ONCE here and every surface reads
// from this list: /api/licenses (JSON), the About modal's "Data sources &
// licences" section, and the Impressum.
//
// TERMS IS A WORD, NOT A GUESS. `Terms` states what the publisher granted:
//   open        - a named open licence (CC BY, ODbL, public domain, ...)
//   restricted  - usable here under stated conditions (non-commercial, keyed
//                 API, attribution-in-exchange-for-quota); the conditions are
//                 in Notes and must remain true
//   unstated    - the publisher granted no licence; we attribute and cite,
//                 and nobody may redistribute it further on our say-so
// Nothing is listed as "open" without a URL where the licence can be read
// (license_test.go enforces this).
//
// A SOURCE THAT IS NOT LISTED IS A BUG. The test pins the tile hosts the
// frontend and the MBTiles builder reach to entries here, so a new basemap
// cannot be added without declaring who owns it and on what terms.

import (
	"encoding/json"
	"net/http"
	"sort"
)

// LicenseTerms is the publisher's grant, reduced to one word.
type LicenseTerms string

const (
	TermsOpen       LicenseTerms = "open"
	TermsRestricted LicenseTerms = "restricted"
	TermsUnstated   LicenseTerms = "unstated"
)

// LicenseEntry is one third-party work this application depends on.
type LicenseEntry struct {
	ID string `json:"id"`
	// Category groups the About-modal list: "imagery", "data", "software".
	Category string `json:"category"`
	// Name is the work as its publisher names it.
	Name string `json:"name"`
	// Publisher is who holds the rights.
	Publisher string `json:"publisher"`
	// Use is what we do with it, in one clause.
	Use string `json:"use"`
	// Licence is the licence as the publisher names it.
	Licence string `json:"licence"`
	// LicenceURL is where that licence can be read. Required when Terms=open.
	LicenceURL string `json:"licence_url,omitempty"`
	// Attribution is the credit line the publisher asks for, verbatim where
	// the publisher prescribes one (EOX, CARTO, ACLED, OSM all do).
	Attribution string `json:"attribution"`
	// Citation is the academic reference the publisher asks to be cited.
	Citation string `json:"citation,omitempty"`
	// URL is the landing page of the dataset/service.
	URL   string       `json:"url"`
	Terms LicenseTerms `json:"terms"`
	// Notes states the condition our use rests on, so that a reader who
	// changes the use (e.g. makes the project commercial) can see what breaks.
	Notes string `json:"notes,omitempty"`
	// Hosts are the tile/API hostnames this entry licenses. The test uses
	// them to ensure no frontend tile URL reaches an undeclared host.
	Hosts []string `json:"hosts,omitempty"`
}

// ProjectLicence is what WE grant. Code and derived analyses are ours; the
// underlying data remains under each publisher's terms.
var ProjectLicence = struct {
	Code          string `json:"code"`
	CodeURL       string `json:"code_url"`
	Derived       string `json:"derived"`
	Repository    string `json:"repository"`
	NonCommercial string `json:"non_commercial"`
}{
	Code:       "MIT",
	CodeURL:    "https://opensource.org/license/mit",
	Derived:    "Our own derived layers (fire trajectories, settlement clusters, deforestation events, geology affinity, traced map linework) are released under CC BY 4.0, except where an input imposes stricter terms — those inherit them (Sentinel-2 cloudless derivatives are CC BY-NC-SA 4.0; OSM-derived features are ODbL).",
	Repository: "https://github.com/raffopenssh/5mp",
	NonCommercial: "5MP is a non-commercial project (see Impressum). Several imagery and " +
		"data terms rest on that fact; a commercial deployment must re-license " +
		"every entry marked 'restricted'.",
}

// Licenses is the register. Alphabetical within a category.
var Licenses = []LicenseEntry{
	// ---------------------------------------------------------------- imagery
	{
		ID: "brgm-car-geology", Category: "imagery",
		Name:        "Carte géologique de la République Centrafricaine 1:1,500,000 (1964)",
		Publisher:   "J.-L. Mestraud (coord.); Bureau de recherches géologiques et minières (BRGM) and Royal Museum for Central Africa (RMCA), publishers. Digitised copies: National Library of Australia (nla.obj-2981820452), RMCA geocatalogue (BE-RMCA-EARTHS-004777)",
		Use:         "CAR geology overlay: our classified vector units and contacts traced from the sheet; the scan itself is not served",
		Licence:     "In copyright. NLA: research or study use of the online copy, other uses by permission. RMCA: © Royal Museum for Central Africa, other restrictions. Not public domain (collective work, 70 years from 1964 publication under French law)",
		LicenceURL:  "https://geocatalogue.africamuseum.be/geonetwork/srv/api/records/BE-RMCA-EARTHS-004777",
		Attribution: "Mestraud, J.-L. (coord.), 1964. Carte géologique de la République Centrafricaine 1:1,500,000. BRGM / Royal Museum for Central Africa. Digitised copy: National Library of Australia nla.obj-2981820452",
		URL:         "https://nla.gov.au/nla.obj-2981820452",
		Terms:       TermsRestricted,
		Notes: "Used as a research/study derivative in a non-commercial project; the traced units are our derivative work of the sheet. " +
			"A permission request to BRGM and RMCA is the outstanding action; until answered this layer stays non-commercial and the scan is not redistributed.",
	},
	{
		ID: "carto", Category: "imagery",
		Name:        "CARTO Basemaps (Dark Matter)",
		Publisher:   "CARTO / OpenStreetMap contributors",
		Use:         "default dark basemap (proxied via /api/basemap with a server-side key)",
		Licence:     "CARTO Basemaps terms (free tier, attribution required); map data ODbL 1.0",
		LicenceURL:  "https://carto.com/legal/",
		Attribution: "© OpenStreetMap contributors, © CARTO",
		URL:         "https://carto.com/basemaps/",
		Terms:       TermsRestricted,
		Notes:       "5M tile requests/month free tier in exchange for attribution; the API key stays server-side (srv/basemap.go).",
		Hosts:       []string{"basemaps.cartocdn.com"},
	},
	{
		ID: "eox-s2cloudless", Category: "imagery",
		Name:        "Sentinel-2 cloudless 2024",
		Publisher:   "EOX IT Services GmbH (contains modified Copernicus Sentinel data 2024)",
		Use:         "satellite basemap, and the only imagery source of the offline-tiles (MBTiles) builder",
		Licence:     "CC BY-NC-SA 4.0",
		LicenceURL:  "https://creativecommons.org/licenses/by-nc-sa/4.0/",
		Attribution: "Sentinel-2 cloudless - https://s2maps.eu by EOX IT Services GmbH (Contains modified Copernicus Sentinel data 2024)",
		URL:         "https://s2maps.eu",
		Terms:       TermsRestricted,
		Notes: "Free WMTS for non-commercial applications with the attribution above shown legibly near the map. " +
			"Offline MBTiles built from it carry this attribution and licence in their metadata and inherit NC-SA. " +
			"Native resolution is 10 m; the WMTS serves to zoom 18, where the builder caps.",
		Hosts: []string{"tiles.maps.eox.at"},
	},
	{
		ID: "gras-sudan-geology", Category: "imagery",
		Name:        "Geological Map of the Sudan 1:2,000,000 (2004)",
		Publisher:   "Geological Research Authority of the Sudan (GRAS); Government of Sudan, Zenodo record 19150268",
		Use:         "Sudan geology overlay (classified raster, traced units and contacts)",
		Licence:     "CC BY 4.0",
		LicenceURL:  "https://creativecommons.org/licenses/by/4.0/",
		Attribution: "Geological Research Authority of the Sudan (GRAS), 2004; via Zenodo record 19150268",
		URL:         "https://zenodo.org/records/19150268",
		Terms:       TermsOpen,
	},
	{
		ID: "gst-tanzania-geology", Category: "imagery",
		Name:        "Minerogenic Map of Tanzania 1:1,500,000 (2015) and GMIS occurrence register",
		Publisher:   "Geological Survey of Tanzania (GST), Dodoma; GMIS implemented by Beak Consultants GmbH",
		Use:         "Tanzania geology overlay (units, contacts) and mineral-occurrence reference points",
		Licence:     "Not stated (public WFS; GetCapabilities declares Fees NONE / AccessConstraints NONE)",
		Attribution: "Geological Survey of Tanzania, Geological and Mineral Information System (GMIS)",
		URL:         "https://gmis-tanzania.com/",
		Terms:       TermsUnstated,
	},
	{
		ID: "loc-sudan-survey", Category: "imagery",
		Name:        "Sudan Survey Department 1:250,000 series (1915-1968)",
		Publisher:   "Sudan Survey Dept., Khartoum; scans by the Library of Congress Geography & Map Division",
		Use:         "historical map overlay, OCR'd labels and traced linework (Sudan / South Sudan)",
		Licence:     "No known copyright restrictions (Library of Congress rights statement)",
		LicenceURL:  "https://www.loc.gov/item/2008627461/",
		Attribution: "Sudan Survey Dept., Khartoum / Library of Congress g8310m.gct00289",
		URL:         "https://www.loc.gov/item/2008627461/",
		Terms:       TermsOpen,
	},
	{
		ID: "maplibre-glyphs", Category: "imagery",
		Name:        "MapLibre demo font glyphs",
		Publisher:   "MapLibre contributors",
		Use:         "map label glyphs",
		Licence:     "BSD-3-Clause; fonts under the SIL Open Font License 1.1",
		LicenceURL:  "https://github.com/maplibre/demotiles/blob/gh-pages/LICENSE",
		Attribution: "MapLibre",
		URL:         "https://demotiles.maplibre.org/",
		Terms:       TermsOpen,
		Hosts:       []string{"demotiles.maplibre.org"},
	},

	// ------------------------------------------------------------------- data
	{
		ID: "acled", Category: "data",
		Name:        "ACLED (Armed Conflict Location & Event Data)",
		Publisher:   "ACLED",
		Use:         "coverage-bias check of the geology model only — aggregated ADM1 counts; no ACLED rows are shown or exported",
		Licence:     "ACLED Terms of Use and Attribution Policy",
		LicenceURL:  "https://acleddata.com/contentusage",
		Attribution: "ACLED (Armed Conflict Location & Event Data), acleddata.com, accessed 2026-08",
		Citation:    "Raleigh, C., Kishi, R. & Linke, A. (2023). Political instability patterns are obscured by conflict dataset scope conditions, sources, and coding choices. Humanities and Social Sciences Communications 10, 74. doi:10.1057/s41599-023-01559-4",
		URL:         "https://acleddata.com/",
		Terms:       TermsRestricted,
		Notes:       "Redistribution of event rows is prohibited; our analysis is ours and must not be attributed to ACLED. See docs/agents/acled.md.",
	},
	{
		ID: "copernicus-dem", Category: "data",
		Name:        "Copernicus DEM GLO-90",
		Publisher:   "European Space Agency / Airbus, Copernicus Programme",
		Use:         "elevation for river-basin tracing",
		Licence:     "Copernicus DEM licence (free access, attribution required)",
		LicenceURL:  "https://spacedata.copernicus.eu/collections/copernicus-digital-elevation-model",
		Attribution: "© DLR e.V. 2010-2014 and © Airbus Defence and Space GmbH 2014-2018 provided under COPERNICUS by the European Union and ESA; all rights reserved",
		URL:         "https://spacedata.copernicus.eu/collections/copernicus-digital-elevation-model",
		Terms:       TermsOpen,
	},
	{
		ID: "crisistracker", Category: "data",
		Name:        "Crisis Tracker",
		Publisher:   "Invisible Children",
		Use:         "mine-site context recovered from public incident reports (geology model evaluation)",
		Licence:     "Not stated; public incident data, cited with access date",
		Attribution: "Crisis Tracker, a project of Invisible Children, https://crisistracker.org",
		URL:         "https://crisistracker.org",
		Terms:       TermsUnstated,
		Notes:       "Labels and clustering are ours and are not attributed to Crisis Tracker.",
	},
	{
		ID: "faolex", Category: "data",
		Name:        "FAOLEX",
		Publisher:   "Food and Agriculture Organization of the United Nations (FAO)",
		Use:         "legal documents (laws, decrees) per protected area and region — titles and links only",
		Licence:     "FAO terms of use; database content CC BY-NC-SA 3.0 IGO",
		LicenceURL:  "https://www.fao.org/contact-us/terms/en/",
		Attribution: "FAOLEX Database, FAO",
		URL:         "https://www.fao.org/faolex/",
		Terms:       TermsRestricted,
		Notes:       "Documents are linked to FAO, not mirrored.",
	},
	{
		ID: "gadm", Category: "data",
		Name:        "GADM 4.1 administrative areas",
		Publisher:   "GADM / University of California, Davis",
		Use:         "country and province names for search and the regional legal-document sync",
		Licence:     "GADM licence: free for academic and other non-commercial use; redistribution not permitted",
		LicenceURL:  "https://gadm.org/license.html",
		Attribution: "GADM, https://gadm.org",
		URL:         "https://gadm.org/",
		Terms:       TermsRestricted,
		Notes:       "Names and bounding boxes only; the polygons are not served or exported.",
	},
	{
		ID: "geoboundaries", Category: "data",
		Name:        "geoBoundaries (gbOpen) ADM1",
		Publisher:   "William & Mary geoLab",
		Use:         "ADM1 units for the ACLED coverage aggregation",
		Licence:     "CC BY 4.0 (gbOpen)",
		LicenceURL:  "https://www.geoboundaries.org/",
		Attribution: "geoBoundaries, William & Mary geoLab (Runfola et al. 2020)",
		Citation:    "Runfola, D. et al. (2020). geoBoundaries: A global database of political administrative boundaries. PLoS ONE 15(4): e0231866. doi:10.1371/journal.pone.0231866",
		URL:         "https://www.geoboundaries.org/",
		Terms:       TermsOpen,
	},
	{
		ID: "geofabrik", Category: "data",
		Name:        "Geofabrik OpenStreetMap extracts",
		Publisher:   "Geofabrik GmbH / OpenStreetMap contributors",
		Use:         "country .osm.pbf extracts for places, roads and mine features",
		Licence:     "ODbL 1.0",
		LicenceURL:  "https://www.openstreetmap.org/copyright",
		Attribution: "© OpenStreetMap contributors",
		URL:         "https://download.geofabrik.de/",
		Terms:       TermsOpen,
	},
	{
		ID: "gfw-alerts", Category: "data",
		Name:        "Global Forest Watch integrated deforestation alerts (GLAD-L, GLAD-S2, RADD)",
		Publisher:   "World Resources Institute / UMD GLAD / Wageningen University",
		Use:         "near-real-time deforestation alerts",
		Licence:     "CC BY 4.0",
		LicenceURL:  "https://www.globalforestwatch.org/terms/",
		Attribution: "Global Forest Watch, World Resources Institute; GLAD alerts (Hansen et al. 2016); RADD alerts (Reiche et al. 2021)",
		Citation:    "Reiche, J. et al. (2021). Forest disturbance alerts for the Congo Basin using Sentinel-1. Environmental Research Letters 16, 024005",
		URL:         "https://www.globalforestwatch.org/",
		Terms:       TermsOpen,
	},
	{
		ID: "ghsl", Category: "data",
		Name:        "Global Human Settlement Layer (GHS-BUILT-S, GHS-POP, GHS-SMOD)",
		Publisher:   "European Commission, Joint Research Centre (JRC)",
		Use:         "settlement footprints, built-up surface, population",
		Licence:     "CC BY 4.0 (EU open data)",
		LicenceURL:  "https://creativecommons.org/licenses/by/4.0/",
		Attribution: "European Commission, Joint Research Centre (JRC) — Global Human Settlement Layer",
		Citation:    "Pesaresi, M., Politis, P. (2023). GHS-BUILT-S R2023A. European Commission, Joint Research Centre (JRC). doi:10.2905/9F06F36F-4B11-47EC-ABB0-4F8B7B1D72EA",
		URL:         "https://human-settlement.emergency.copernicus.eu/",
		Terms:       TermsOpen,
	},
	{
		ID: "glad-cropland", Category: "data",
		Name:        "GLAD Global Cropland Extent 2000-2019",
		Publisher:   "University of Maryland, GLAD",
		Use:         "cropland-expansion attribution around settlements",
		Licence:     "CC BY 4.0",
		LicenceURL:  "https://glad.umd.edu/dataset/croplands",
		Attribution: "Potapov, P. et al. (2021), UMD GLAD",
		Citation:    "Potapov, P. et al. (2021). Global maps of cropland extent and change show accelerated cropland expansion in the twenty-first century. Nature Food 3, 19-28. doi:10.1038/s43016-021-00429-z",
		URL:         "https://glad.umd.edu/dataset/croplands",
		Terms:       TermsOpen,
	},
	{
		ID: "gsw", Category: "data",
		Name:        "Global Surface Water (transitions, 1984-2021)",
		Publisher:   "European Commission JRC / Google",
		Use:         "new-water candidates near settlements",
		Licence:     "Free for any use with attribution (EC JRC/Google)",
		LicenceURL:  "https://global-surface-water.appspot.com/download",
		Attribution: "EC JRC/Google — Global Surface Water",
		Citation:    "Pekel, J.-F., Cottam, A., Gorelick, N., Belward, A.S. (2016). High-resolution mapping of global surface water and its long-term changes. Nature 540, 418-422. doi:10.1038/nature20584",
		URL:         "https://global-surface-water.appspot.com/",
		Terms:       TermsOpen,
	},
	{
		ID: "hansen-gfc", Category: "data",
		Name:        "Hansen Global Forest Change v1.11 (2000-2023)",
		Publisher:   "University of Maryland, Google, USGS, NASA",
		Use:         "annual tree-cover loss",
		Licence:     "CC BY 4.0",
		LicenceURL:  "https://creativecommons.org/licenses/by/4.0/",
		Attribution: "Hansen/UMD/Google/USGS/NASA",
		Citation:    "Hansen, M.C. et al. (2013). High-Resolution Global Maps of 21st-Century Forest Cover Change. Science 342, 850-853. doi:10.1126/science.1244693",
		URL:         "https://glad.earthengine.app/view/global-forest-change",
		Terms:       TermsOpen,
	},
	{
		ID: "heigit-roads", Category: "data",
		Name:        "HeiGIT global road surface data",
		Publisher:   "Heidelberg Institute for Geoinformation Technology (HeiGIT) / OpenStreetMap contributors",
		Use:         "roads with surface and passability",
		Licence:     "ODbL 1.0 (derived from OpenStreetMap)",
		LicenceURL:  "https://opendatacommons.org/licenses/odbl/1-0/",
		Attribution: "HeiGIT gGmbH; © OpenStreetMap contributors",
		URL:         "https://heigit.org/",
		Terms:       TermsOpen,
	},
	{
		ID: "hydrorivers", Category: "data",
		Name:        "HydroRIVERS v1.0",
		Publisher:   "HydroSHEDS / WWF",
		Use:         "river network, distance-to-river for settlements",
		Licence:     "CC BY 4.0",
		LicenceURL:  "https://www.hydrosheds.org/hydrosheds-core-downloads",
		Attribution: "HydroRIVERS, HydroSHEDS (Lehner & Grill 2013)",
		Citation:    "Lehner, B., Grill, G. (2013). Global river hydrography and network routing: baseline data and new approaches to study the world's large river systems. Hydrological Processes 27(15), 2171-2186",
		URL:         "https://www.hydrosheds.org/products/hydrorivers",
		Terms:       TermsOpen,
	},
	{
		ID: "icmm", Category: "data",
		Name:        "ICMM Global Mining Dataset v1.5",
		Publisher:   "International Council on Mining and Metals",
		Use:         "industrial mine reference points (geology model evaluation)",
		Licence:     "Not stated (public dataset, attribution expected)",
		Attribution: "International Council on Mining and Metals, Global Mining Dataset v1.5",
		URL:         "https://www.icmm.com/en-gb/research/social-performance/2026/global-mining-dataset",
		Terms:       TermsUnstated,
	},
	{
		ID: "ipis", Category: "data",
		Name:        "IPIS artisanal mining site surveys (CAR, Tanzania)",
		Publisher:   "International Peace Information Service (IPIS)",
		Use:         "field-visited mine sites used to score the geology model",
		Licence:     "ODC-BY 1.0 (CAR); not stated for the Tanzania survey",
		LicenceURL:  "https://opendatacommons.org/licenses/by/1-0/",
		Attribution: "International Peace Information Service (IPIS), open data",
		URL:         "https://ipisresearch.be/mapping-services/open-data/",
		Terms:       TermsOpen,
	},
	{
		ID: "iucn", Category: "data",
		Name:        "IUCN Red List of Threatened Species",
		Publisher:   "International Union for Conservation of Nature",
		Use:         "species lists per protected area (names, categories, links)",
		Licence:     "IUCN Red List Terms of Use (non-commercial, attribution, no redistribution of the dataset)",
		LicenceURL:  "https://www.iucnredlist.org/terms/terms-of-use",
		Attribution: "IUCN. The IUCN Red List of Threatened Species. https://www.iucnredlist.org",
		Citation:    "IUCN. The IUCN Red List of Threatened Species. https://www.iucnredlist.org (version as accessed)",
		URL:         "https://www.iucnredlist.org/",
		Terms:       TermsRestricted,
	},
	{
		ID: "nasa-firms", Category: "data",
		Name:        "NASA FIRMS active fire data (VIIRS S-NPP / NOAA-20 / NOAA-21, 375 m)",
		Publisher:   "NASA LANCE / FIRMS, Earth Science Data and Information System (ESDIS)",
		Use:         "all fire detections, trajectories and fire narratives",
		Licence:     "NASA Earth science data policy: free, full and open; acknowledgement requested",
		LicenceURL:  "https://www.earthdata.nasa.gov/engage/open-data-services-software-policies/data-information-policy",
		Attribution: "We acknowledge the use of data from NASA's Fire Information for Resource Management System (FIRMS), part of NASA's Earth Science Data and Information System (ESDIS)",
		Citation:    "NASA FIRMS. VIIRS (S-NPP, NOAA-20, NOAA-21) I Band 375 m Active Fire Product NRT. doi:10.5067/FIRMS/VIIRS/VNP14IMGT_NRT.002",
		URL:         "https://firms.modaps.eosdis.nasa.gov/",
		Terms:       TermsOpen,
	},
	{
		ID: "nasa-vnp46a3", Category: "data",
		Name:        "VIIRS Black Marble monthly nighttime lights (VNP46A3)",
		Publisher:   "NASA LAADS DAAC",
		Use:         "night-light time series at mining and settlement sites (evaluation only)",
		Licence:     "NASA Earth science data policy: free, full and open",
		LicenceURL:  "https://www.earthdata.nasa.gov/engage/open-data-services-software-policies/data-information-policy",
		Attribution: "NASA Black Marble VNP46A3 (Román et al. 2018)",
		Citation:    "Román, M.O. et al. (2018). NASA's Black Marble nighttime lights product suite. Remote Sensing of Environment 210, 113-143",
		URL:         "https://ladsweb.modaps.eosdis.nasa.gov/missions-and-measurements/products/VNP46A3",
		Terms:       TermsOpen,
	},
	{
		ID: "nga-tearline", Category: "data",
		Name:        "Tearline CAR artisanal mine census (Lobaye Invest permits)",
		Publisher:   "NGA Tearline / William & Mary geoLab",
		Use:         "imagery-traced mine reference points (geology model evaluation)",
		Licence:     "Not stated (US government publication)",
		Attribution: "NGA Tearline / geoLab, College of William & Mary, 2021",
		URL:         "https://www.tearline.mil/public_page/car-mines",
		Terms:       TermsUnstated,
	},
	{
		ID: "openalex", Category: "data",
		Name:        "OpenAlex",
		Publisher:   "OurResearch",
		Use:         "research publications per protected area",
		Licence:     "CC0 1.0",
		LicenceURL:  "https://creativecommons.org/publicdomain/zero/1.0/",
		Attribution: "OpenAlex, https://openalex.org",
		Citation:    "Priem, J., Piwowar, H., Orr, R. (2022). OpenAlex: A fully-open index of scholarly works, authors, venues, institutions, and concepts. arXiv:2205.01833",
		URL:         "https://openalex.org/",
		Terms:       TermsOpen,
	},
	{
		ID: "osm", Category: "data",
		Name:        "OpenStreetMap (places, roads, mines via Overpass and extracts)",
		Publisher:   "OpenStreetMap contributors",
		Use:         "nearest places, roads, mine features, settlement names",
		Licence:     "ODbL 1.0",
		LicenceURL:  "https://www.openstreetmap.org/copyright",
		Attribution: "© OpenStreetMap contributors",
		URL:         "https://www.openstreetmap.org/",
		Terms:       TermsOpen,
		Notes:       "Exports containing OSM-derived features carry the attribution and remain ODbL (share-alike).",
	},
	{
		ID: "ucdp", Category: "data",
		Name:        "UCDP Georeferenced Event Dataset (GED)",
		Publisher:   "Uppsala Conflict Data Program, Uppsala University",
		Use:         "mine-site context from conflict event descriptions (geology model evaluation)",
		Licence:     "Free to use with citation (no formal licence document)",
		Attribution: "Uppsala Conflict Data Program (UCDP) GED",
		Citation:    "Sundberg, R., Melander, E. (2013). Introducing the UCDP Georeferenced Event Dataset. Journal of Peace Research 50(4), 523-532; plus the current GED release (Davies et al.)",
		URL:         "https://ucdp.uu.se/",
		Terms:       TermsUnstated,
	},
	{
		ID: "usgs-deposits", Category: "data",
		Name:        "USGS major mineral deposits of Africa (OFR 2005-1294-E)",
		Publisher:   "U.S. Geological Survey; served via the JRC Africa Knowledge Platform",
		Use:         "mineral-deposit reference points (geology model evaluation)",
		Licence:     "CC BY 4.0 (AKP); USGS content is US public domain",
		LicenceURL:  "https://creativecommons.org/licenses/by/4.0/",
		Attribution: "Taylor, C.D. et al. (2009), USGS Open-File Report 2005-1294-E; JRC Africa Knowledge Platform",
		URL:         "https://africa-knowledge-platform.ec.europa.eu/",
		Terms:       TermsOpen,
	},
	{
		ID: "wdpa", Category: "data",
		Name:        "World Database on Protected Areas (WDPA) / Protected Planet",
		Publisher:   "UNEP-WCMC and IUCN",
		Use:         "protected-area boundaries, names, designations and search index",
		Licence:     "Protected Planet Terms and Conditions (non-commercial use, attribution, no redistribution of the dataset)",
		LicenceURL:  "https://www.protectedplanet.net/en/legal",
		Attribution: "UNEP-WCMC and IUCN (2026), Protected Planet: The World Database on Protected Areas (WDPA), Cambridge, UK: UNEP-WCMC and IUCN. www.protectedplanet.net",
		Citation:    "UNEP-WCMC and IUCN (2026). Protected Planet: The World Database on Protected Areas (WDPA). Cambridge, UK",
		URL:         "https://www.protectedplanet.net/",
		Terms:       TermsRestricted,
		Notes:       "Boundaries of the monitored areas are displayed and included in per-area exports with this attribution; the WDPA as a whole is not redistributed.",
	},

	// --------------------------------------------------------------- software
	{
		ID: "lucide", Category: "software",
		Name:        "Lucide icons 0.577.0",
		Publisher:   "Lucide contributors",
		Use:         "UI icon font",
		Licence:     "ISC",
		LicenceURL:  "https://lucide.dev/license",
		Attribution: "Lucide",
		URL:         "https://lucide.dev/",
		Terms:       TermsOpen,
	},
	{
		ID: "maplibre-gl", Category: "software",
		Name:        "MapLibre GL JS 4.1.2",
		Publisher:   "MapLibre contributors",
		Use:         "interactive globe and map rendering",
		Licence:     "BSD-3-Clause",
		LicenceURL:  "https://github.com/maplibre/maplibre-gl-js/blob/main/LICENSE.txt",
		Attribution: "MapLibre",
		URL:         "https://maplibre.org/",
		Terms:       TermsOpen,
		Hosts:       []string{"unpkg.com"},
	},
	{
		ID: "modernc-sqlite", Category: "software",
		Name:        "modernc.org/sqlite (SQLite transpiled to Go)",
		Publisher:   "Jan Mercl / SQLite authors",
		Use:         "database engine",
		Licence:     "BSD-3-Clause; SQLite itself is public domain",
		LicenceURL:  "https://gitlab.com/cznic/sqlite/-/blob/master/LICENSE",
		Attribution: "modernc.org/sqlite",
		URL:         "https://pkg.go.dev/modernc.org/sqlite",
		Terms:       TermsOpen,
	},
	{
		ID: "python-geo", Category: "software",
		Name:        "Python pipeline: numpy, pandas, scipy, scikit-learn, shapely, geopandas, fiona, pyproj, GDAL",
		Publisher:   "respective projects",
		Use:         "offline data pipelines (fire trajectories, settlement clustering, rasters)",
		Licence:     "BSD-3-Clause / MIT",
		LicenceURL:  "https://github.com/raffopenssh/5mp/blob/main/requirements.txt",
		Attribution: "see requirements.txt",
		URL:         "https://github.com/raffopenssh/5mp/blob/main/requirements.txt",
		Terms:       TermsOpen,
	},
	{
		ID: "sheetjs", Category: "software",
		Name:        "xlsx-js-style 1.2.0 (SheetJS Community Edition fork)",
		Publisher:   "SheetJS LLC and contributors",
		Use:         "Excel report export (loaded on demand)",
		Licence:     "Apache-2.0",
		LicenceURL:  "https://www.apache.org/licenses/LICENSE-2.0",
		Attribution: "SheetJS",
		URL:         "https://github.com/gitbrent/xlsx-js-style",
		Terms:       TermsOpen,
		Hosts:       []string{"cdn.jsdelivr.net"},
	},
}

// LicenseByID returns the entry for id, or nil.
func LicenseByID(id string) *LicenseEntry {
	for i := range Licenses {
		if Licenses[i].ID == id {
			return &Licenses[i]
		}
	}
	return nil
}

// LicenseForHost returns the entry that licenses a tile/API hostname, or nil.
func LicenseForHost(host string) *LicenseEntry {
	for i := range Licenses {
		for _, h := range Licenses[i].Hosts {
			if h == host {
				return &Licenses[i]
			}
		}
	}
	return nil
}

// sortedLicenses returns a copy ordered imagery → data → software, then name.
func sortedLicenses() []LicenseEntry {
	list := make([]LicenseEntry, len(Licenses))
	copy(list, Licenses)
	sort.SliceStable(list, func(i, j int) bool {
		if list[i].Category != list[j].Category {
			return categoryRank(list[i].Category) < categoryRank(list[j].Category)
		}
		return list[i].Name < list[j].Name
	})
	return list
}

func categoryRank(c string) int {
	switch c {
	case "imagery":
		return 0
	case "data":
		return 1
	}
	return 2
}

// HandleAPILicenses serves the register as JSON. Public and cacheable: an
// attribution page that needs a password is not an attribution.
func (s *Server) HandleAPILicenses(w http.ResponseWriter, r *http.Request) {
	list := sortedLicenses()
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Cache-Control", "public, max-age=3600")
	json.NewEncoder(w).Encode(map[string]interface{}{
		"project":  ProjectLicence,
		"licenses": list,
		"count":    len(list),
	})
}
