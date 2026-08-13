"""Measure the commodity-affinity model against an occurrence dataset.

The geology panel offers two choosers: pick a COMMODITY and it isolates the
rock units that can host it, or open the Junctions tab and it draws the
contact lines whose two lithologies make a deposit setting.  Both are
inferences over lithology (`srv/geomap_std.go`, `srv/geomap_contacts.go`) and
neither has ever been scored.  An inference nobody scores is a story, and a
story that is drawn on a map reads as a measurement.

This script scores it on every sheet we serve, against whatever independent
occurrence list reaches that sheet - and a sheet with no list is reported
UNMEASURED rather than skipped, because "nobody has checked here" is a thing
the panel has to say. Four lists, deliberately different in kind and in bias:

  car/ipis      IPIS artisanal-mine field visits (914 sites, gold/diamond
                flags). A surveyor's reach.
  car/ipis_armed, car/ipis_calm
                THE SAME LIST, SPLIT BY WHETHER THE SURVEYORS RECORDED AN ARMED
                ACTOR AT THE PIT. Not extra evidence - one survey cannot
                corroborate itself - but the CAR gold lift is 2.04 on the armed
                stratum and 1.31 on the other (capture p=0.0033, and p=0.0021
                shuffling only within prefecture), so the pooled number is
                partly a statement about who could reach the ground. Scored by
                default for that reason: a lift quoted without its stratum is a
                number whose value depends on a variable it does not mention.
  car/tearline  NGA Tearline's 2021 imagery census of 40 mine systems inside
                the Lobaye Invest permits. A licence boundary's reach - the
                opposite blind spot, and only 3 of its 40 mines lie within
                2 km of an IPIS site. Small and local; it can agree or
                disagree with the national number, not replace it.
  car/crisistracker
                mine sites recovered from Crisis Tracker incident reports in
                EASTERN CAR - the half of the country IPIS never reached (391 km
                to the nearest IPIS mine, none within 25). Only 5 of 41 sites
                name a mineral, so every commodity here is under the floor and
                the rows read "too few sites": that is the point. "Eastern CAR
                is unmeasured per commodity" is a fact the table can only state
                if the list is present.
  tanzania/gst  the Geological Survey of Tanzania's own occurrence register
                (480 points), which is NOT arm's length from the units it
                scores - same minerogenic-mapping programme.
  sudan/osm     OpenStreetMap mine and quarry tags: gold only, 91% of it one
                mapper's campaign, so the baseline is that campaign's own hull.

Two questions per list, kept separate because they have different answers:

  UNITS      of the sites that produce commodity X, what fraction fall inside
             a unit the model grades for X, against the fraction of the map's
             AREA those units cover?  Ratio > 1 = the chooser concentrates.
  JUNCTIONS  how much closer are those sites to the graded contact lines than
             a uniform random point on the same sheet, and than the sites of
             the OTHER commodity (the control that rules out "mines are near
             every line", which they are: 77% sit within 5 km of *some*
             contact)?

Two guards against a flattering number:

  * The random baseline is drawn inside the ground THE LIST COULD HAVE SEEN -
    the mapped units for a national register, the sites' own hull for one
    mapper's campaign, the searched permits for a permit census. Never the
    bounding box, and never wider than the list's own frame: a layer that
    merely happens to be where somebody was looking scores well against a
    sheet-wide baseline and 1.0 against its own frame.
  * The cross-commodity control is gold-only vs diamond-only sites (a site
    flagged for both tells us nothing about which rock it came for).

A third section, --continental, scores the layers we do NOT have: the JRC
Africa Knowledge Platform's WFS (LithoMap Africa, cratons, Macgregor active
faults) against the 7,163 IPIS visits in DRC, where no sheet of ours reaches.
It exists to answer "would another dataset do better than our sheet does",
with the same baseline discipline, before anyone spends a week ingesting one.

Usage:  python3 scripts/geomaps/eval_affinity.py [--json out.json]
                                                 [--continental]
Needs the server running (the catalogue's lithologies and contact rules are
server-owned; re-deriving them here would score a second implementation).
"""

import argparse, collections, csv, json, os, subprocess, sys, time
import numpy as np
import pyproj
from shapely.geometry import shape, Point
from shapely.ops import unary_union, nearest_points
from shapely.strtree import STRtree

SHEET = "car"
IPIS = "data/ipis/caf_mines_ipis.csv"
# The permutation test for the CAR strata, written by scripts/eval_reach_strata.py
# and READ here. Recomputing it would be a second implementation of one test,
# and two answers to "is this difference real" is one too many.
REACH_STRATA = "data/eval/reach_strata.json"
# The second occurrence dataset, and the only one that reaches the other two
# sheets: 969 major nonfuel deposits, USGS OFR 2005-1294-E, fetched off the JRC
# Africa Knowledge Platform's WFS and reprojected to WGS84 once (the WFS serves
# EPSG:3857 whatever you ask for). Committed, because an eval whose truth set is
# a cached download is not reproducible.
USGS = "data/geology_truth/usgs_africa_deposits.geojson"
# IPIS CAR columns are per-commodity flags, not one mineral string.
FLAGS = {"gold": "minerals_or", "diamond": "minerals_diamant"}
GEOD = pyproj.Geod(ellps="WGS84")
NEAR_KM = 5.0
N_RANDOM = 3000
SEED = 7


# ---------------------------------------------------------------------------
# ONE TRUTH SET PER SHEET, AND EVERY SHEET IS ASKED
#
# This eval was written for the CAR because the CAR is where we held an
# occurrence list. Three sheets ship, and a score that exists for only one of
# them is a UI problem as much as a measurement one: the panel now greys out a
# chooser it has measured to fail, so a sheet nobody measured must read as
# UNMEASURED rather than quietly inherit another country's verdict. That is
# what `scope_sheet` in srv/geomap_scores.go is for, and it can only be honest
# if every sheet has been asked the question.
#
# The three answers are different in KIND, which is the whole reason the truth
# set is pluggable rather than "IPIS" spelled into the scoring code:
#
#   car       IPIS artisanal-mine field VISITS - what a surveyor saw on the
#             ground, per-commodity flags, 914 sites. The strongest truth we
#             hold and the narrowest: IPIS visits where IPIS can travel.
#   tanzania  the Geological Survey of Tanzania's own occurrence REGISTER
#             (gmis:mines_minerogenicview, 480 points, 126 gold-bearing), off
#             the same GeoServer as the units. National, named mines, one
#             free-text commodity string per point.
#   sudan     nothing artisanal we hold. 16 USGS major deposits reach the
#             sheet, 9 gold-bearing; MIN_N declines a lift below 8 per
#             commodity rather than computing one from a handful.
#
# TANZANIA'S TRUTH IS NOT INDEPENDENT OF TANZANIA'S MAP THE WAY THE CAR'S IS,
# and the script prints that beside every number it produces. Both come from
# the GST's Minerogenic Map programme, and a minerogenic map exists precisely
# to show mineralisation in relation to lithology - so a unit boundary there
# may have been drawn partly to ENCLOSE a known deposit. The mine points are
# still observed workings rather than model output, so the measurement is worth
# making; but the circularity is of unknown size and pushes in the flattering
# direction, and a Tanzanian lift may not be compared with a CAR lift as though
# both were arm's length. The IPIS visits have the opposite defect (a survey
# footprint, but no relation whatever to a 1964 sheet). Neither caveat is a
# reason to skip the measurement. Both are a reason it never travels alone.


class Truth:
    """An occurrence list for one sheet, reduced to what the scoring needs.

    `sites[commodity]` is a list of shapely Points keyed by OUR commodity
    vocabulary (the keys of the catalogue's `commodities`), so no scoring code
    ever learns a survey's spelling. `label` is the phrase the UI will quote as
    the score's scope - a place and a survey, never a sheet id, because the
    panel demotes sheets to provenance everywhere else. `caveat` is the
    sentence that stops the lift being over-read and there is no way to
    construct a Truth without one.
    """

    def __init__(self, sheet, tid, label, caveat, sites, kind,
                 hull_baseline=False, extra=None, region=None,
                 region_note=None, stratum_of=None, stratum=None):
        self.sheet, self.label, self.caveat = sheet, label, caveat
        # A STRATUM IS NOT A SECOND OPINION. When a list is split by a property
        # of its own sites, the halves share a survey, a footprint and a
        # spelling: they can show that a pooled number DEPENDS on that property,
        # and they can never corroborate each other the way two independent
        # lists do. Carried here so the agreement report cannot mistake a split
        # for a replication, and so a consumer can tell a stratum's lift from a
        # headline one.
        self.stratum_of, self.stratum = stratum_of, stratum
        # AN ID FOR THE LIST, BECAUSE A SHEET CAN HAVE MORE THAN ONE. Once two
        # independent lists score the same ground, (sheet, commodity, kind,
        # floor) stops identifying a measurement, and a consumer keyed on it
        # picks between two disagreeing numbers by file order.
        self.id = tid
        self.sites, self.kind = sites, kind   # kind: visits|register|osm_tags
        # THE FRAME THE LIST WAS COMPILED IN, when that frame is known in
        # advance and is not the sheet. A census of eight mining permits was
        # searched inside those permits and nowhere else, so its baseline is
        # the permits: a hull drawn round the mines it FOUND would be tighter
        # than the ground actually searched, which flatters the model by hiding
        # the searched-and-empty part of the frame.
        self.region, self.region_note = region, region_note
        # WHERE THE RANDOM POINTS COME FROM IS PART OF THE TRUTH SET, not a
        # scoring option. A set whose coverage is a survey's reach or one
        # mapper's campaign must be compared against random ground inside its
        # OWN hull: measured against the whole sheet, the model would be
        # credited for the fact that somebody happened to be working there.
        # Carried by the Truth so it cannot be forgotten at the call site.
        self.hull_baseline = hull_baseline
        self.extra = extra or {}

    def commodities(self):
        return sorted(self.sites)

    def exclusive(self, com, others):
        """Sites of `com` not also recorded for any of `others`.

        The cross-commodity control needs sites that can only have been worked
        for one thing: a pit flagged gold AND diamond says nothing about which
        rock brought the digger there. Matched on rounded coordinates, because
        two lists of Points share no identity.
        """
        seen = {(round(p.x, 6), round(p.y, 6))
                for o in others if o != com for p in self.sites.get(o, [])}
        return [p for p in self.sites.get(com, [])
                if (round(p.x, 6), round(p.y, 6)) not in seen]


def _ipis_car_rows():
    return [r for r in csv.DictReader(open(IPIS)) if r.get("longitude")]


def _ipis_sites(rows):
    return {com: [Point(float(r["longitude"]), float(r["latitude"]))
                  for r in rows if (r.get(col) or "0").strip() in ("1", "1.0")]
            for com, col in FLAGS.items()}


# ARMED PRESENCE IS A PROPERTY OF THE SITE, RECORDED AT THE SITE. IPIS's
# surveyors noted, standing in each pit, whether an armed actor was there - 251
# of 914. It is not ACLED, not a province average, and not our inference.
def _ipis_armed(r):
    return (r.get("actor_type") or "").strip() not in ("", "0")


def truth_car_ipis():
    """CAR: IPIS visits. Per-commodity boolean columns, not one mineral name."""
    rows = _ipis_car_rows()
    return Truth("car", "ipis", "the Central African Republic, 2019 survey",
                 "One sheet, one country, one survey's reachable sites - a lift "
                 "here is evidence the rule is not noise, not a probability of "
                 "finding anything. THE CAR GOLD LIFT IS CONFOUNDED: it is "
                 "measurably higher at mines where the surveyors recorded an "
                 "armed actor, so this pooled number must be read beside the "
                 "two strata below, never instead of them.",
                 _ipis_sites(rows), "visits",
                 extra={"stratified_by": "ipis_armed",
                        "strata": ["ipis_armed", "ipis_calm"]})


# ---------------------------------------------------------------------------
# THE POOLED CAR NUMBER HAS A KNOWN CONFOUND, SO IT NEVER SHIPS ALONE.
#
# scripts/eval_reach_strata.py established it and this script now re-derives it
# on every run: the gold UNIT lift is 2.04 at mines with an armed actor and 1.31
# at mines without, capture 31.3% vs 20.5%, p=0.0033 over 20k label
# permutations - and p=0.0021 when the labels are shuffled only within
# prefecture, which holds the geology and the province-level access fixed. The
# difference is not a story about which rocks host gold; it is a property of
# WHO COULD STAND WHERE, riding inside a number we print as geology.
#
# The consequence for this script is small and strict: a CAR gold lift quoted
# without naming its stratum is a number whose value depends on a variable it
# does not mention, so both strata are scored BY DEFAULT and appear beside the
# pooled row in every output this script writes. Not behind a flag: a
# correctness fact that costs one flag to see is a fact that will be quoted
# without it.
#
# Each stratum is scored in ITS OWN HULL (hull_baseline). A subset's capture
# against the whole sheet's area would reward the smaller stratum for being
# smaller, which is precisely the artefact the comparison is trying to rule out.
def _truth_car_stratum(armed):
    rows = [r for r in _ipis_car_rows() if _ipis_armed(r) == armed]
    if len(rows) < MIN_N:
        return None
    tid = "ipis_armed" if armed else "ipis_calm"
    word = ("where the surveyors recorded an armed actor" if armed
            else "where they recorded none")
    return Truth("car", tid,
                 f"the Central African Republic, 2019 survey — mines {word}",
                 "One stratum of one survey, scored against random ground in "
                 "its own hull. It is not an independent list and cannot "
                 "corroborate the pooled number; it exists to show that the "
                 "pooled number moves with who could reach the ground.",
                 _ipis_sites(rows), "visits", hull_baseline=True,
                 stratum_of="ipis", stratum=("armed" if armed else "calm"),
                 extra={"sites_total": len(rows),
                        "stratifier": "IPIS actor_type recorded at the site"})


def truth_car_ipis_armed():
    return _truth_car_stratum(True)


def truth_car_ipis_calm():
    return _truth_car_stratum(False)


# The GST register lists several minerals per point, comma-separated ("Gold,
# Copper"), in the survey's own spelling - which is not our vocabulary and must
# be mapped by NAME, never by substring. A substring scan is wrong in both
# directions: it would have to catch "Gold" inside "Gold, Copper" (right) while
# not reading "Rare Earth Elements" as nothing and not reading a "Garnet (gem)"
# as a gemstone by luck. Anything unmapped is PRINTED rather than dropped: the
# register also holds sand, gypsum and natural gas, which the affinity model
# makes no claim about, and the only way to be sure a commodity the model DOES
# claim has not gone missing behind a spelling is to see the leftovers.
GMIS_MINES = "data/eval/gmis/mines.json"
GMIS_WFS = ("https://gmis-tanzania.com/geoserver/gmis/ows?service=WFS"
            "&version=2.0.0&request=GetFeature&outputFormat=application/json"
            "&srsName=EPSG:4326&typeNames=gmis:mines_minerogenicview")
GMIS_COMMODITY = {
    "gold": ["Gold"],
    "copper": ["Copper"],
    "diamond": ["Diamond"],
    "uranium": ["Uranium"],
    "coal": ["Coal"],
    "graphite": ["Graphite"],
    "iron": ["Iron"],
    "cobalt": ["Cobalt"],
    "lithium": ["Lithium", "Spodumene"],
    "rare_earth": ["Rare Earth Elements", "Niobium", "Thorium"],
    "gemstone": ["Sapphire", "Ruby", "Corundum", "Emerald", "Tsavorite",
                 "Tourmaline", "Garnet (gem)", "Opal", "Almandine", "Diopside",
                 "Beryl", "Alexandrite", "Amethyst", "Aquamarine", "Rhodolite",
                 "Spinel", "Chrysoprase", "Peridot"],
}
# Tanzania's envelope, for the axis-order trap that put this very country in
# the Indian Ocean once already (scripts/geomaps/gmis_tanzania.py). A WFS that
# answers 4326 with lat/lon yields a map that still LOOKS like a map, so the
# numbers are checked rather than the version note trusted.
TZA_ENVELOPE = (28.0, -13.0, 42.0, 0.5)


def truth_tanzania():
    """Tanzania: the survey's own occurrence register."""
    if not os.path.exists(GMIS_MINES):
        os.makedirs(os.path.dirname(GMIS_MINES), exist_ok=True)
        print(f"  fetching {GMIS_MINES} from the GST GeoServer...")
        subprocess.check_call(["curl", "-fsS", "--compressed", "--max-time",
                               "300", GMIS_WFS, "-o", GMIS_MINES])
    feats = json.load(open(GMIS_MINES))["features"]
    want = {}
    for com, names in GMIS_COMMODITY.items():
        for n in names:
            want.setdefault(n.lower(), set()).add(com)
    sites = collections.defaultdict(list)
    unmapped = collections.Counter()
    for f in feats:
        g = f.get("geometry") or {}
        if g.get("type") != "Point":
            continue
        p = Point(*g["coordinates"][:2])
        if not (TZA_ENVELOPE[0] <= p.x <= TZA_ENVELOPE[2]
                and TZA_ENVELOPE[1] <= p.y <= TZA_ENVELOPE[3]):
            sys.exit(f"GMIS mine at {p.x:.3f},{p.y:.3f} is not in Tanzania - "
                     "the WFS returned lat/lon axis order; delete "
                     f"{GMIS_MINES} and refetch")
        for tok in (f["properties"].get("commodity") or "").split(","):
            tok = tok.strip()
            if not tok:
                continue
            hit = want.get(tok.lower())
            if hit:
                for com in hit:
                    sites[com].append(p)
            else:
                unmapped[tok] += 1
    if unmapped:
        head = ", ".join(f"{t} x{n}" for t, n in unmapped.most_common(8))
        more = f", +{len(unmapped) - 8} more terms" if len(unmapped) > 8 else ""
        print(f"  register terms outside our vocabulary, not scored: {head}{more}")
    return Truth("tanzania", "gst",
                 "Tanzania, the survey's 2015 occurrence register",
                 "The Geological Survey of Tanzania's own register, from the "
                 "same Minerogenic Map programme as the units it scores - a "
                 "unit boundary may have been drawn to enclose a known "
                 "deposit, so this is not arm's length the way the CAR is.",
                 dict(sites), "register")


# ---------------------------------------------------------------------------
# SUDAN / SOUTH SUDAN: OpenStreetMap mine and quarry features.
#
# Surveyed 2026-08-13 (docs/agents/overlays.md). IPIS covers CAR and DRC and has
# nothing for either Sudan; GRAS publishes no WFS the way Tanzania's GST does;
# and every global candidate fails on a different criterion - USGS MRDS' Sudan
# records cite Vail 1978 and the Sudan Survey Department bulletins, i.e. the same
# institutional lineage that drew the sheet we are scoring (a truth set compiled
# by reading the map cannot score the map), while the global mining-footprint
# polygon sets (Maus v2, Tang & Werner) carry no commodity field at all and so
# cannot score a per-commodity claim.
#
# What is left is OSM, and it is independent in the strong sense: a mapper
# tracing pit outlines off satellite imagery is not reading a 2004 paper sheet,
# and there is no path by which one could have been compiled from the other.
#
# TWO CAVEATS, BOTH SHIPPED WITH THE NUMBER.
#
#  1. IT RESOLVES GOLD AND NOTHING ELSE. 1,080 mine/quarry features reach the
#     sheet; 289 carry any commodity tag and 283 of those say gold. MIN_N then
#     declines every other commodity here, which is the correct outcome and not
#     a gap to fill.
#  2. IT IS ONE MAPPER'S CAMPAIGN. 264 of the gold-tagged features were entered
#     by a single OSM user, most of them in one month. That is mapper bias, not
#     survey bias, and it has the same consequence: the random baseline must be
#     drawn inside the SITES' OWN CONVEX HULL, never the sheet, or the model is
#     credited for merely being where somebody was mapping. `hull_baseline`
#     carries that requirement into the scoring rather than leaving it to be
#     remembered, and the concentration is measured and printed beside the lift.
#
# The fetch asks Overpass for `meta` so the concentration is derivable from the
# same download as the sites - a caveat that cannot be recomputed from the file
# is a caveat that will quietly stop being true.
OSM_DIR = "data/eval/osm_mines"
# Several public instances: any one of them 504s under load, and a single
# attempt against a busy mirror is not evidence that Sudan has no mines.
OVERPASS_HOSTS = ["https://overpass-api.de/api/interpreter",
                  "https://overpass.kumi.systems/api/interpreter",
                  "https://overpass.private.coffee/api/interpreter"]
# The tags a dug hole in the ground wears in OSM. Several, because no one of
# them is used consistently: a Sudanese artisanal pit may be `industrial=mine`,
# `landuse=quarry` or a bare `man_made=mineshaft`.
OSM_MINE_TAGS = ["industrial=mine", "landuse=quarry", "landuse=mine",
                 "man_made=mineshaft", "man_made=adit"]
# The commodity tags, in OSM's several spellings, mapped to our vocabulary. Only
# `gold` will clear MIN_N on this sheet; the rest are here so that a commodity
# arriving later is scored rather than silently dropped.
OSM_COMMODITY = {
    "gold": ["gold", "au"],
    "diamond": ["diamond", "diamonds"],
    "copper": ["copper", "cu"],
    "iron": ["iron", "iron_ore", "fe"],
    "uranium": ["uranium"],
    "cobalt": ["cobalt"],
    "lithium": ["lithium"],
    "rare_earth": ["rare_earth", "rare_earth_elements"],
}
SUDAN_ENVELOPE = (21.0, 3.0, 39.5, 24.0)


def _overpass(tag, bbox):
    """One tag, one bbox, cached. Nodes, ways and relations, centred."""
    os.makedirs(OSM_DIR, exist_ok=True)
    safe = tag.replace("=", "_").replace(":", "_")
    path = f"{OSM_DIR}/raw_{safe}.json"
    if not os.path.exists(path):
        k, v = tag.split("=")
        s, w, n, e = bbox[1], bbox[0], bbox[3], bbox[2]
        q = (f'[out:json][timeout:180];('
             f'node["{k}"="{v}"]({s},{w},{n},{e});'
             f'way["{k}"="{v}"]({s},{w},{n},{e});'
             f'relation["{k}"="{v}"]({s},{w},{n},{e});'
             f');out center meta;')
        # A PARTIAL DOWNLOAD MUST NEVER BECOME A CACHE ENTRY. This is the
        # histmaps failure (scripts/histmaps/README.md): a curl that stopped at
        # 264 of 770 lines produced a mosaic that built cleanly and then
        # documented the missing half of the country as absent from the archive.
        # Here it would be a quiet "Sudan has 40 gold sites" and a lift computed
        # from them. So: write to a temp path, require it to PARSE and to hold
        # an `elements` list, and only then move it into place. Overpass 504s
        # under load often enough that a single attempt is not a measurement,
        # and the public instances are interchangeable.
        tmp = path + ".part"
        last = None
        for attempt, host in enumerate(OVERPASS_HOSTS * 2, 1):
            print(f"    Overpass: {tag} ({host.split('/')[2]}, try {attempt})")
            try:
                subprocess.check_call(["curl", "-fsS", "--max-time", "600",
                                       "-d", "data=" + q, host, "-o", tmp])
                els = json.load(open(tmp))["elements"]
            except Exception as exc:              # noqa: BLE001 - any failure retries
                last = exc
                if os.path.exists(tmp):
                    os.remove(tmp)
                time.sleep(5 * attempt)
                continue
            os.replace(tmp, path)
            print(f"      {len(els)} element(s)")
            break
        else:
            sys.exit(f"Overpass would not answer for {tag} after "
                     f"{2 * len(OVERPASS_HOSTS)} tries ({last}). This is a "
                     "missing INPUT, not an empty result - re-run later.")
    return json.load(open(path))["elements"]


def truth_sudan():
    """Sudan + South Sudan: OSM mine/quarry features with a commodity tag."""
    want = {}
    for com, names in OSM_COMMODITY.items():
        for n in names:
            want[n] = com
    sites = collections.defaultdict(list)
    users = collections.Counter()
    seen, total = set(), 0
    for tag in OSM_MINE_TAGS:
        for el in _overpass(tag, SUDAN_ENVELOPE):
            c = el.get("center") or el
            lon, lat = c.get("lon"), c.get("lat")
            if lon is None or lat is None:
                continue
            key = (el["type"], el["id"])
            if key in seen:
                continue
            seen.add(key)
            total += 1
            if not (SUDAN_ENVELOPE[0] <= lon <= SUDAN_ENVELOPE[2]
                    and SUDAN_ENVELOPE[1] <= lat <= SUDAN_ENVELOPE[3]):
                sys.exit(f"OSM mine at {lon:.3f},{lat:.3f} is outside Sudan - "
                         "an axis-order or bbox error; delete " + OSM_DIR)
            tags = el.get("tags") or {}
            raw = (tags.get("resource") or tags.get("mineral")
                   or tags.get("raw_material") or "")
            hit = {want[t.strip().lower()] for t in raw.split(";")
                   if t.strip().lower() in want}
            for com in hit:
                sites[com].append(Point(lon, lat))
                if com == "gold" and el.get("user"):
                    users[el["user"]] += 1
    if not sites:
        return None
    # THE CAVEAT, MEASURED FROM THE SAME DOWNLOAD AS THE SITES. A sentence
    # asserting "one mapper" that nothing recomputes is a sentence that outlives
    # its truth.
    top, topn = (users.most_common(1) or [(None, 0)])[0]
    ng = len(sites.get("gold", []))
    share = topn / ng if ng else 0.0
    print(f"  {total} OSM mine/quarry features, "
          f"{sum(len(v) for v in sites.values())} with a commodity tag")
    if top:
        print(f"  mapper concentration: {share:.0%} of the gold-tagged sites "
              f"({topn}/{ng}) were entered by one OSM user - the baseline is "
              "drawn in the sites' own hull for exactly this reason")
    return Truth("sudan", "osm", "Sudan and South Sudan, OSM mine tags (2025)",
                 f"OpenStreetMap mine and quarry tags, not a field survey: "
                 f"{share:.0%} of the gold sites come from one mapper's "
                 "campaign, so the baseline is the sites' own hull rather than "
                 "the sheet, and this measures gold only - no other commodity "
                 "reaches a countable number here.",
                 dict(sites), "osm_tags", hull_baseline=True,
                 extra={"osm_features": total, "mapper_share": share,
                        "mapper_sites": topn})


# ---------------------------------------------------------------------------
# A SECOND LIST FOR THE CAR, WITH THE OPPOSITE BIAS.
#
# The CAR score rested on IPIS alone, and IPIS has one shape of blind spot: a
# surveyor visits what a surveyor can drive to. NGA Tearline's 2021 project
# (William & Mary geoLab; https://www.tearline.mil/public_page/car-mines)
# digitised artisanal mine systems off commercial satellite imagery inside the
# eight permits awarded to Lobaye Invest - so its blind spot is a permit
# boundary, not a road. Of its 40 mines only 3 sit within 2 km of an IPIS site;
# at site level the two lists barely overlap, which is the whole reason to hold
# both.
#
# It is a SMALL, LOCAL check and the script says so everywhere it can:
#
#  - n=40, inside 0.6% of the sheet. It cannot confirm or refute a national
#    number; it can only agree or disagree with one, which is worth knowing.
#  - THE FRAME IS LEGAL, NOT GEOLOGICAL, AND THAT CUTS THE WRONG WAY. Permits
#    are drawn around ground somebody already believed was mineralised, so the
#    prior "there is gold here" is baked into the search area - a worse confound
#    for an affinity model than IPIS's travel bias. The baseline is therefore
#    the PERMITS, not the sheet and not the mines' own hull: only ground that
#    was actually searched may be random ground, or the model is credited for
#    the analysts' choice of where to look. The permits with no mapped mines are
#    ~1 km placeholder boxes (the article approximates them), so the frame is
#    the three permits that were really mapped.
#  - COMMODITY IS INHERITED, NOT OBSERVED. No analyst reads gold off imagery;
#    the mineral is the one named in the permit. Every permit names gold, so
#    gold is the only claim these points can carry, and diamond - named in E and
#    G alongside gold - is deliberately NOT scored: it would be the same points
#    twice, with no way to say which metal brought the digger.
TEARLINE_PTS = "data/eval/tearline/tearline_car_mines_points.geojson"
TEARLINE_PERMITS = "data/eval/tearline/tearline_car_permits.geojson"


# ---------------------------------------------------------------------------
# THE OTHER HALF OF THE COUNTRY.
#
# Every CAR lift this script has ever printed came from IPIS, and IPIS's 914
# mines span lon 14.6-17.9. Crisis Tracker's 41 span 21.2-24.9: the nearest IPIS
# mine to one of them is 391 km away, and not one is within 25 km. So the
# "national" CAR number is a number about the west, and the east had never been
# looked at until this list arrived (f9a9017, scripts/eval_crisistracker.py).
#
# WHAT IT CAN AND CANNOT SCORE. The commodity is only ever in the incident
# prose, and only 5 sites say what is dug there - below MIN_N in every
# commodity, so this Truth produces "too few sites" rows and NOT lifts. That is
# the correct output and the reason it is included: a per-commodity claim about
# eastern CAR is currently UNMEASURED, and a table that omits the list entirely
# cannot say so. The weaker claim the list CAN answer - does a reported mine sit
# on ground graded for gold or diamond at all - is scored in
# scripts/eval_crisistracker.py (lift 0.89, p=0.38, and the ceiling printed
# beside it), because it is a different question from this script's.
#
# The baseline is the sites' own hull: a community early-warning network reports
# where its communities are.
#
# IT MUST NOT BE STRATIFIED BY ARMED PRESENCE. Every site here is in the list
# BECAUSE it was attacked, so "armed" is the selection rule and there is no
# stratum where nobody was recordable - pooling it with the IPIS surveyor flag
# would compare an observation with an entry criterion.
CRISISTRACKER_SITES = "data/eval/crisistracker/mine_sites.json"


def truth_car_crisistracker():
    """CAR east: mine sites recovered from Crisis Tracker incident reports."""
    if not os.path.exists(CRISISTRACKER_SITES):
        return None
    doc = json.load(open(CRISISTRACKER_SITES))
    sites = collections.defaultdict(list)
    allpts = []
    for s in doc["sites"]:
        if (s.get("iso3") or "").upper() != "CAF":
            continue
        lon, lat = s.get("lon"), s.get("lat")
        if lon is None or lat is None:
            continue
        if not (14.0 <= lon <= 28.0 and 2.0 <= lat <= 11.0):
            sys.exit(f"Crisis Tracker site at {lon},{lat} is outside the CAR - "
                     f"check {CRISISTRACKER_SITES} (an un-located incident "
                     "reads as 0.0/0.0, which is the Gulf of Guinea)")
        p = Point(lon, lat)
        allpts.append(p)
        # ONLY WHAT THE SITE YIELDS. `commodities_looted` is deliberately not
        # read: a sack of diamonds carried to a place says nothing about the
        # rock under it, and scoring a supply chain as a lithology is the exact
        # confusion the labelling kept apart.
        for com in s.get("commodities") or []:
            sites[com].append(p)
    if len(allpts) < 3:
        return None
    # THE FRAME IS THE WHOLE NETWORK'S FOOTPRINT, not the hull of the five sites
    # whose note happened to name a mineral. Which mines get a commodity written
    # down is a property of the reporting, not of the ground, so a hull drawn
    # round those five would shrink the baseline to wherever the prose was
    # richest - the same flattery the hull rule exists to prevent, one level in.
    region = unary_union(allpts).convex_hull
    return Truth("car", "crisistracker",
                 "eastern Central African Republic, community incident reports",
                 "Mine sites recovered from Crisis Tracker incident reports, "
                 "not a survey: every site is here because somebody was "
                 "attacked and somebody could report it, and the commodity is "
                 "whatever the note happened to name - 5 of 41 sites. It "
                 "reaches the eastern half of the country that IPIS never "
                 "visited (391 km to the nearest IPIS mine), which is why it "
                 "is scored even though every commodity here falls under the "
                 "floor.",
                 dict(sites), "incident_reports",
                 region=region,
                 region_note="the hull of the reporting network's mine sites",
                 extra={"sites_total": len(allpts),
                        "sites_with_a_commodity":
                            sum(len(v) for v in sites.values()),
                        "labelled_by": doc.get("labelled_by"),
                        "notice": doc.get("notice")})


def truth_car_tearline():
    """CAR: Tearline's imagery census inside the Lobaye Invest permits."""
    if not os.path.exists(TEARLINE_PTS):
        return None
    feats = json.load(open(TEARLINE_PTS))["features"]
    pts, permits_with_mines = [], set()
    for f in feats:
        lon, lat = f["geometry"]["coordinates"][:2]
        if not (14.0 <= lon <= 28.0 and 2.0 <= lat <= 11.0):
            sys.exit(f"Tearline mine at {lon:.3f},{lat:.3f} is outside the CAR "
                     f"envelope - check {TEARLINE_PTS} for an axis swap")
        pts.append(Point(lon, lat))
        permits_with_mines.add(f["properties"]["permit"])
    # THE SEARCHED FRAME, derived from the points rather than named here: the
    # permits that hold mapped mines are the permits that were really mapped,
    # and typing that list would freeze it against a refetch (invariant 2).
    perm = [shape(f["geometry"])
            for f in json.load(open(TEARLINE_PERMITS))["features"]
            if f["properties"]["permit"] in permits_with_mines]
    region = unary_union(perm) if perm else None
    if region is None or region.is_empty:
        # A frame we cannot reconstruct is a missing input, not a wide one:
        # falling back to the sheet would silently turn a 4,000 km2 census into
        # a national score (invariant 1).
        sys.exit(f"{TEARLINE_PERMITS} holds no polygon for the permits the "
                 "mines sit in; the baseline frame is unknown")
    return Truth("car", "tearline",
                 "the Central African Republic, 2021 imagery census",
                 "40 mine systems traced off satellite imagery inside eight "
                 "mining permits, not a national survey: the search area was "
                 "chosen for who holds the licence, so random ground here means "
                 "random ground INSIDE those permits, and gold is the mineral "
                 "named in the permit rather than one seen at the pit.",
                 {"gold": pts}, "imagery",
                 region=region,
                 region_note=(f"the {len(perm)} mapped Lobaye Invest permits"),
                 extra={"mines": len(pts),
                        "permits": sorted(permits_with_mines),
                        "source": "NGA Tearline / William & Mary geoLab, 2021"})


# ONE SHEET, POSSIBLY SEVERAL LISTS. Ordered: the first is the one a summary
# quotes when it must quote one, and it is the most independent of the map.
# The lists per sheet, in order. The first is the headline; a stratum follows
# the list it splits, because a stratum only means anything beside its parent.
TRUTH = {"car": [truth_car_ipis, truth_car_ipis_armed, truth_car_ipis_calm,
                 truth_car_tearline, truth_car_crisistracker],
         "tanzania": [truth_tanzania],
         "sudan": [truth_sudan]}


def truths_for(sheet):
    """Every occurrence list we hold for a sheet, absent ones dropped.

    A list whose files are not present returns None and is skipped - but the
    caller records the sheet as unmeasured only when NONE of them resolve, so a
    missing optional list can never look like a missing sheet.
    """
    return [t for t in (fn() for fn in TRUTH.get(sheet, [])) if t is not None]


def catalogue(sheet, pwd="test2026"):
    raw = subprocess.check_output(
        ["curl", "-fsS", f"http://localhost:8000/api/geomap?pwd={pwd}"])
    sheets = {s["id"]: s for s in json.loads(raw)["sheets"]}
    if sheet not in sheets:
        sys.exit(f"sheet {sheet} not served; is the server running?")
    return sheets[sheet]["catalogue"]


def pair_key(a, b):
    return f"{a}|{b}" if a < b else f"{b}|{a}"


def dist_km(pt, geom):
    q = nearest_points(pt, geom)[1]
    return GEOD.inv(pt.x, pt.y, q.x, q.y)[2] / 1000.0


# ---------------------------------------------------------------------------
# The continental cross-check.
#
# Layers off the JRC Africa Knowledge Platform's GeoServer (open WFS, GeoJSON,
# whole continent in one request) scored against the 7,163 IPIS visits in DRC,
# which no sheet of ours covers. Cached under data/eval/akp/ because the point
# of the section is to be re-run.
AKP = ("https://africa-knowledge-platform.ec.europa.eu/geoserver/akp/wfs"
       "?service=WFS&version=2.0.0&outputFormat=application/json"
       "&request=GetFeature&typeNames=akp:")
AKP_CACHE = "data/eval/akp"
IPIS_COD = "data/ipis/cod_mines_ipis.csv"


def akp_layer(name):
    import os
    os.makedirs(AKP_CACHE, exist_ok=True)
    path = f"{AKP_CACHE}/{name}.json"
    if not os.path.exists(path):
        subprocess.check_call(["curl", "-fsS", "--compressed", "--max-time",
                               "300", AKP + name, "-o", path])
    return json.load(open(path))["features"]


def continental():
    """Score continental layers against the DRC visit list.

    The random baseline is drawn inside the CONVEX HULL of the visits, not the
    country: IPIS surveys the east, so a country-wide baseline would credit any
    layer that merely happens to be eastern - including a layer of the survey's
    own footprint.

    A craton is scored on its EDGE, not its interior: 60% of the hull is inside
    one, so "the site is on a craton" is true of the random points too.
    """
    rows = [r for r in csv.DictReader(open(IPIS_COD)) if r.get("longitude")]
    sites = collections.defaultdict(list)
    for r in rows:
        sites[(r.get("mineral1") or "?")].append(
            Point(float(r["longitude"]), float(r["latitude"])))
    hull = unary_union([p for v in sites.values() for p in v]).convex_hull
    rng = np.random.default_rng(SEED)
    minx, miny, maxx, maxy = hull.bounds
    rand = []
    while len(rand) < 2000:
        p = Point(rng.uniform(minx, maxx), rng.uniform(miny, maxy))
        if hull.contains(p):
            rand.append(p)
    named = [("gold", sites["Or"]), ("cassiterite", sites["Cassit\u00e9rite"]),
             ("coltan", sites["Coltan"]), ("random", rand)]
    out = {"hull_deg2": hull.area, "sites": {n: len(v) for n, v in named}}
    print("\nCONTINENTAL CROSS-CHECK - JRC AKP layers vs IPIS DRC visits")
    for n, v in named:
        print(f"  {n:12} {len(v):5}")

    litho = akp_layer("LithoMap_Africa")
    geoms = [shape(f["geometry"]) for f in litho]
    glg = [f["properties"]["GLG"] for f in litho]
    tree = STRtree(geoms)
    area = collections.Counter()
    for g, c in zip(geoms, glg):
        if g.intersects(hull):
            area[c] += g.intersection(hull).area
    tot = sum(area.values())

    def at(p):
        for i in tree.query(p):
            if geoms[i].contains(p):
                return int(i)
        return None

    print("\n  LithoMap_Africa - class density for gold visits")
    print(f"  {'GLG':>6} {'sites':>6} {'share':>7} {'area':>7} {'lift':>6}")
    rec = {}
    idx = [i for i in (at(p) for p in sites["Or"]) if i is not None]
    cnt = collections.Counter(glg[i] for i in idx)
    for c, k in cnt.most_common(6):
        sh = area[c] / tot if tot else 0.0
        lift = (k / len(idx)) / sh if sh else float("nan")
        rec[c] = {"sites": k, "share": k / len(idx),
                  "area_share": sh, "lift": lift}
        print(f"  {c:>6} {k:>6} {k/len(idx):>6.1%} {sh:>6.1%} {lift:>6.2f}")
    out["lithomap_gold"] = rec

    layers = {
        "craton_edge": (unary_union([shape(f["geometry"]).boundary
                                     for f in akp_layer("cratons")]), 25.0),
        "active_faults": (unary_union([shape(f["geometry"]) for f
                                       in akp_layer("active_faults")]), 25.0),
    }
    print("\n  proximity layers")
    dist = {}
    for name, (geom, thr) in layers.items():
        print(f"  {name} (within {thr:g} km):")
        d = {}
        for n, pts in named:
            near = float(np.mean([dist_km(p, geom) < thr for p in pts]))
            med = float(np.median([dist_km(p, geom) for p in pts]))
            d[n] = {"near": near, "median_km": med}
            print(f"    {n:12} P {near:.3f}  median {med:7.1f} km")
        for n in ("gold", "cassiterite", "coltan"):
            d[n]["lift"] = (d[n]["near"] / d["random"]["near"]
                            if d["random"]["near"] else float("nan"))
        dist[name] = d
    out["proximity"] = dist
    return out


def score_sheet(sheet, truth, min_sites=8):
    """Score one sheet's affinity model against its own occurrence list.

    Returns the same record shape for every sheet, so the Go table and the
    pinning test do not learn which country they are reading. A commodity with
    fewer than `min_sites` occurrences is reported as `too_few`, never as a
    lift: eight points cannot tell 1.0 from 3.0, and a number printed here
    would be quoted as though they could (MIN_N, and the same discipline the
    USGS section applies).
    """
    cat = catalogue(sheet)
    lith = {c["code"]: c.get("lith") or "mixed" for c in cat["classes"]}
    rules = {r["pair"]: r for r in cat["std"]["contact_rules"]}

    units = json.load(open(f"data/geomaps/{sheet}_units.geojson"))["features"]
    geoms = [shape(f["geometry"]) for f in units]
    props = [f["properties"] for f in units]
    tree = STRtree(geoms)
    areas = np.array([p.get("area_km2") or 0.0 for p in props])
    land = unary_union(geoms)

    contacts = json.load(
        open(f"data/geomaps/{sheet}_contacts.geojson"))["features"]

    def unit_weight(p, com):
        for a in p.get("affinity") or []:
            if a["commodity"] == com:
                return a["weight"]
        return 0

    def junction_weight(f, com):
        r = rules.get(pair_key(lith.get(f["properties"]["code_a"], "mixed"),
                               lith.get(f["properties"]["code_b"], "mixed")))
        for a in (r or {}).get("affinity", []):
            if a["commodity"] == com:
                return a["weight"]
        return 0

    def unit_at(x, y):
        pt = Point(x, y)
        for i in tree.query(pt):
            if geoms[i].contains(pt):
                return int(i)
        return None

    def area_share_in(region):
        """Fraction of `region` covered by the selected units.

        Clipped geometry, not the units' own area_km2 column: a unit half
        outside the hull contributes half. Degrees-squared is fine because it
        is a RATIO taken at one latitude band.
        """
        tot = region.area

        def share(sel):
            if not tot:
                return 0.0
            acc = 0.0
            for i in np.nonzero(sel)[0]:
                g = geoms[int(i)]
                if g.intersects(region):
                    acc += g.intersection(region).area
            return acc / tot
        return share

    # ON THE SHEET means inside the union of mapped units, not inside a
    # bounding box: the corners of a sheet's envelope are other countries, and
    # a site there would be scored against ground this map never described.
    coms = truth.commodities()
    sites = {c: [p for p in truth.sites[c] if land.contains(p)] for c in coms}
    only = {c: [p for p in truth.exclusive(c, coms) if land.contains(p)]
            for c in coms}

    # THE BASELINE IS THE GROUND THE TRUTH SET COULD HAVE SEEN. For a national
    # register that is the whole mapped sheet; for a survey footprint or one
    # mapper's campaign it is the convex hull of the sites, intersected with the
    # mapped units so a hull corner in an unmapped country cannot dilute it.
    # Getting this wrong is the single easiest way to publish a flattering lift:
    # a layer that merely happens to be where the sites are scores well against
    # a sheet-wide baseline and 1.0 against its own hull.
    region, region_note = land, "the mapped sheet"
    if truth.region is not None:
        # A FRAME THE LIST DECLARED, clipped to mapped ground so a permit
        # corner off the sheet cannot dilute the area share.
        region = truth.region.intersection(land)
        region_note = (truth.region_note or "the frame the list was compiled in")
        region_note += f", {100 * region.area / land.area:.1f}% of the sheet"
        if region.is_empty:
            sys.exit(f"{sheet}/{truth.id}: the declared frame does not meet "
                     "the mapped sheet at all")
    elif truth.hull_baseline:
        allpts = [p for c in coms for p in sites[c]]
        if len(allpts) >= 3:
            hull = unary_union(allpts).convex_hull.intersection(land)
            if not hull.is_empty:
                region = hull
                region_note = ("the convex hull of the sites, "
                               f"{100 * hull.area / land.area:.0f}% of the sheet")
    if truth.region is not None:
        # A SITE OUTSIDE THE DECLARED FRAME IS NOT SCORED AGAINST IT. The
        # capture is measured on the same ground as the baseline or the ratio
        # is between two different questions. Dropped points are printed,
        # because "the frame lost half the list" is a bug in the frame and must
        # not pass as a quiet score.
        before = {c: len(v) for c, v in sites.items()}
        sites = {c: [p for p in v if region.intersects(p)]
                 for c, v in sites.items()}
        only = {c: [p for p in v if region.intersects(p)]
                for c, v in only.items()}
        lost = {c: before[c] - len(sites[c]) for c in sites if before[c] != len(sites[c])}
        if lost:
            print(f"    {lost} site(s) fall outside the declared frame, not scored")
    rng = np.random.default_rng(SEED)
    minx, miny, maxx, maxy = region.bounds
    rand = []
    guard = 0
    while len(rand) < N_RANDOM:
        guard += 1
        if guard > N_RANDOM * 400:
            # A no-op must not read as an answer (invariant 1): a region too
            # thin to sample would otherwise yield a short random set and a
            # baseline computed from it, which is a number nobody could tell
            # from a real one.
            sys.exit(f"{sheet}: could not draw {N_RANDOM} random points in "
                     f"{region_note}; the region is degenerate")
        p = Point(rng.uniform(minx, maxx), rng.uniform(miny, maxy))
        if region.contains(p):
            rand.append(p)
    print(f"    baseline region: {region_note}")
    area_share = area_share_in(region)
    # CLIPPED AREA whenever the baseline is not the whole sheet - the lift is
    # capture/area, so an area measured over the sheet against a capture
    # measured inside a frame compares two different denominators.
    clipped = region is not land

    out = {"sheet": sheet, "truth_id": truth.id,
           "stratum_of": truth.stratum_of, "stratum": truth.stratum,
           "near_km": NEAR_KM, "n_random": N_RANDOM,
           "baseline_region": region_note,
           "truth": truth.label, "truth_kind": truth.kind,
           "caveat": truth.caveat, "truth_extra": truth.extra,
           "sites": {c: len(v) for c, v in sites.items()},
           "sites_exclusive": {c: len(v) for c, v in only.items()},
           "min_sites": min_sites,
           "units": {}, "junctions": {}}

    print(f"\n=== {sheet}: {len(units)} units, {len(contacts)} contact pairs")
    print(f"    truth: {truth.label} ({truth.kind})")
    for c in coms:
        mark = "" if len(sites[c]) >= min_sites else f"  <-- under n={min_sites}, no lift"
        print(f"    {c:>11}: {len(sites[c]):>4} on the sheet "
              f"({len(only[c])} recorded for nothing else){mark}")

    scored = [c for c in coms if len(sites[c]) >= min_sites]

    # --- UNITS ----------------------------------------------------------
    print("\n  UNITS - capture of sites vs share of mapped area")
    print(f"  {'commodity':>11} {'w>=':>4} {'capture':>8} {'area':>7} {'lift':>6}")
    for com in coms:
        if com not in scored:
            out["units"][com] = {"verdict": "too few sites",
                                 "n": len(sites[com])}
            continue
        idx = [unit_at(p.x, p.y) for p in sites[com]]
        idx = [i for i in idx if i is not None]
        W = np.array([unit_weight(p, com) for p in props])
        rec = {}
        for mw in (1, 2, 3):
            sel = W >= mw
            # AREA SHARE OVER THE BASELINE REGION, not over the sheet. The unit
            # lift is capture/area, so measuring area on the whole sheet while
            # capture is measured on sites confined to a hull compares two
            # different denominators - and does it in the flattering direction
            # whenever the graded units are concentrated outside the hull.
            area = (area_share(sel) if clipped
                    else (areas[sel].sum() / areas.sum() if areas.sum() else 0.0))
            if not area or not idx:
                # NOTHING TO MEASURE is not MEASURED, NOTHING FOUND. No class
                # graded that highly means the filter selects no ground and the
                # ratio has no denominator; the key is absent so the Go table
                # cannot ship a zero for it.
                continue
            cap = sum(1 for i in idx if W[i] >= mw) / len(idx)
            lift = cap / area
            rec[mw] = {"capture": cap, "area_share": area, "lift": lift,
                       "n_units": int(sel.sum()), "n_sites": len(idx)}
            print(f"  {com:>11} {mw:>4} {cap:>7.1%} {area:>6.1%} {lift:>6.2f}")
        out["units"][com] = rec

    # --- JUNCTIONS ------------------------------------------------------
    print(f"\n  JUNCTIONS - P(within {NEAR_KM:g} km of a graded contact)")
    print(f"  {'commodity':>11} {'w>=':>4} {'lines':>6} {'sites':>7} {'random':>7}"
          f" {'lift':>6} {'other':>7} {'ctrl':>6}")
    anyline = unary_union([shape(f["geometry"]) for f in contacts])
    d_any = np.array([dist_km(p, anyline) for p in rand])
    out["any_contact_random_near"] = float(np.mean(d_any < NEAR_KM))
    for com in coms:
        if com not in scored:
            out["junctions"][com] = {"verdict": "too few sites",
                                     "n": len(sites[com])}
            continue
        # The control is every OTHER scored commodity's exclusive sites. With
        # two commodities that is the other one; with eleven it is "sites worked
        # for something else", which is the same question asked of a bigger
        # sample: does this rule find GOLD, or does it find MINES?
        others = [p for c in scored if c != com for p in only[c]]
        rec = {}
        for mw in (1, 2, 3):
            gs = [shape(f["geometry"]) for f in contacts
                  if junction_weight(f, com) >= mw]
            if not gs:
                continue
            L = unary_union(gs)
            near = lambda pts: float(np.mean(
                [dist_km(p, L) < NEAR_KM for p in pts])) if pts else 0.0
            # The measured set is the EXCLUSIVE sites where there are enough of
            # them, so the control means something; below that it is all of
            # them, and the control is reported as undefined rather than
            # computed from four points.
            use = only[com] if len(only[com]) >= min_sites else sites[com]
            s, r, o = near(use), near(rand), near(others)
            med = float(np.median([dist_km(p, L) for p in use])) if use else None
            rec[mw] = {"n_lines": len(gs), "site_near": s, "random_near": r,
                       "other_near": o, "n_sites": len(use),
                       "exclusive": len(only[com]) >= min_sites,
                       "lift": s / r if r else float("nan"),
                       "control_ratio": s / o if o else float("inf"),
                       "median_km": med}
            print(f"  {com:>11} {mw:>4} {len(gs):>6} {s:>6.1%} {r:>6.1%}"
                  f" {s/r if r else float('nan'):>6.2f} {o:>6.1%}"
                  f" {s/o if o else float('inf'):>6.2f}")
        out["junctions"][com] = rec

    print(f"\n  baseline: a random point on this sheet is within {NEAR_KM:g} km"
          f" of SOME contact {out['any_contact_random_near']:.1%} of the time -"
          " proximity to a line only means something graded.")
    print(f"  caveat: {truth.caveat}")
    return out


# ---------------------------------------------------------------------------
# The other two sheets.
#
# IPIS covers CAR and DRC; Sudan and Tanzania have no artisanal survey we hold.
# The USGS major-deposit list does reach them - 969 points continent-wide - and
# it is a DIFFERENT kind of truth: industrial-scale named deposits, not visited
# artisanal pits. That difference is the point of running both. A rule that
# finds artisanal workings and a rule that finds Bulyanhulu are not the same
# claim, and a model scored on only one of them is being credited with the
# other.
#
# The sample is tiny per sheet (Tanzania 18 deposits, 3 gold-bearing) and the
# script says so rather than printing a lift: three points cannot distinguish
# 1.0 from 3.0, and a number computed from them would be quoted anyway.
MIN_N = 8


def usgs_sheets(sheets=("sudan", "car", "tanzania")):
    dep = json.load(open(USGS))["features"]
    pts = [(Point(*f["geometry"]["coordinates"]), f["properties"]) for f in dep]
    out = {}
    print("\nUSGS MAJOR DEPOSITS - the industrial-scale control")
    for sh in sheets:
        try:
            units = json.load(
                open(f"data/geomaps/{sh}_units.geojson"))["features"]
        except FileNotFoundError:
            print(f"  {sh}: units file absent, skipped")
            continue
        geoms = [shape(f["geometry"]) for f in units]
        props = [f["properties"] for f in units]
        land = unary_union(geoms)
        tree = STRtree(geoms)
        areas = np.array([p.get("area_km2") or 0.0 for p in props])

        def at(p):
            for i in tree.query(p):
                if geoms[i].contains(p):
                    return int(i)
            return None

        inside = [(p, q) for p, q in pts if land.contains(p)]
        gold = [(p, q) for p, q in inside if "Gold" in q["commodity"]]
        rec = {"deposits": len(inside), "gold": len(gold)}
        print(f"  {sh}: {len(inside)} deposits on the sheet, "
              f"{len(gold)} gold-bearing")
        if len(gold) < MIN_N:
            # Not a zero and not a lift: an n this small has no resolving
            # power, and a number printed here would be quoted as one.
            rec["verdict"] = "sample too small"
            names = ", ".join(q["dep_name"] if "dep_name" in q else q["name"]
                              for _, q in gold)
            print(f"    n<{MIN_N}: no lift computed{' (' + names + ')' if names else ''}")
            out[sh] = rec
            continue
        W = {}
        for p in props:
            w = 0
            for a in p.get("affinity") or []:
                if a["commodity"] == "gold":
                    w = a["weight"]
            W[p["code"]] = w
        idx = [i for i in (at(p) for p, _ in gold) if i is not None]
        for mw in (1, 2, 3):
            sel = [c for c, w in W.items() if w >= mw]
            ar = sum(a for p, a in zip(props, areas)
                     if p["code"] in sel) / areas.sum()
            cap = sum(1 for i in idx if W[props[i]["code"]] >= mw) / len(idx)
            rec[mw] = {"capture": cap, "area_share": ar,
                       "lift": cap / ar if ar else float("nan")}
            print(f"    units w_gold>={mw}: capture {cap:>6.1%} "
                  f"area {ar:>6.1%} lift {cap/ar if ar else float('nan'):>5.2f}")
        out[sh] = rec
    return out


def at_floor(rec, floor):
    """One floor's record, whether the keys are ints or JSON's strings.

    This function exists because its absence was a silent no-op: the reports
    below indexed `rec["1"]` while score_sheet writes `rec[1]`, so every
    comparison found nothing, printed nothing, and exited 0 - a blank section
    that reads exactly like "the lists agree everywhere" (invariant 1). The
    same records arrive as strings after a JSON round-trip, so both are valid
    and neither may be assumed.
    """
    if not isinstance(rec, dict) or "verdict" in rec:
        return None
    return rec.get(int(floor)) or rec.get(str(floor))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json")
    ap.add_argument("--sheets", default="car,tanzania,sudan",
                    help="sheets to score, comma-separated. A sheet with no "
                         "occurrence list is reported as unmeasured, which is "
                         "a result the UI needs, not a skip.")
    ap.add_argument("--continental", action="store_true",
                    help="also score JRC AKP continental layers vs IPIS DRC")
    args = ap.parse_args()

    # EVERY SERVED SHEET IS ACCOUNTED FOR, not only the ones we can score.
    # The panel decides whether to quote a number by asking which measured
    # ground the viewport reaches; a sheet missing from this output is a sheet
    # the UI has no way to describe, and the failure would look like a blank
    # rather than like "nobody has checked here". Derived from the server's own
    # sheet list, never typed (invariant 2).
    served = [s["id"] for s in json.loads(subprocess.check_output(
        ["curl", "-fsS", "http://localhost:8000/api/geomap?pwd=test2026"]))["sheets"]]
    asked = [s for s in args.sheets.split(",") if s.strip()]
    out = {"near_km": NEAR_KM, "n_random": N_RANDOM, "min_sites": MIN_N,
           "served_sheets": served, "sheets": {}, "unmeasured": {}}

    for sheet in served:
        if sheet not in asked:
            out["unmeasured"][sheet] = "not requested in this run"
            continue
        truths = truths_for(sheet)
        if not truths:
            # THE HONEST RESULT FOR A SHEET WITH NO TRUTH SET. It is recorded
            # in the same file as the scores, because "we looked and there is
            # no occurrence list for Sudan" is exactly what the panel has to
            # say there, and a fact that lives only in a commit message cannot
            # be shipped.
            out["unmeasured"][sheet] = (
                "no independent occurrence list; the USGS major-deposit "
                "section below is all that reaches this sheet")
            print(f"\n=== {sheet}: no occurrence list held - UNMEASURED")
            continue
        # ONE RECORD PER LIST, NEVER MERGED. Two independent lists over one
        # sheet may disagree - that IS the finding when it happens, and an
        # average of them is a number neither survey supports.
        for truth in truths:
            try:
                out["sheets"].setdefault(sheet, {})[truth.id] = \
                    score_sheet(sheet, truth, MIN_N)
            except FileNotFoundError as e:
                # A missing units/contacts file is a missing INPUT, not a zero
                # score. Recording it as unmeasured keeps the difference
                # (invariant 1: a unit that produces nothing for a valid input
                # reports unfinished, so the caller retries).
                out["unmeasured"][sheet] = f"input absent: {e.filename}"
                print(f"\n=== {sheet}: {e.filename} absent - UNMEASURED "
                      "(build the sheet, then re-run)")
                break

    out["usgs"] = usgs_sheets()
    if args.continental:
        out["continental"] = continental()

    print("\nSUMMARY - what each served sheet is worth")
    for sheet in served:
        if sheet in out["sheets"]:
            for tid, r in out["sheets"][sheet].items():
                scored = [c for c, v in r["units"].items() if "verdict" not in v]
                # A STRATUM IS NAMED AS ONE in the summary too. Four lines under
                # "car" read as four independent checks unless the two that are
                # halves of one survey say so where they are counted.
                tag = (f" [stratum of {r['stratum_of']}, not independent]"
                       if r.get("stratum_of") else "")
                print(f"  {sheet:>9}/{tid:<11} measured against {r['truth']}, "
                      f"{len(scored)} commodity(ies) with n>={MIN_N}{tag}")
        else:
            print(f"  {sheet:>9}: UNMEASURED - {out['unmeasured'][sheet]}")

    # WHERE TWO INDEPENDENT LISTS SCORE THE SAME CLAIM, DO THEY AGREE?
    #
    # This is the only cheap check we have on the truth sets themselves. The two
    # CAR lists are independent of the 1964 sheet and of each other (3 of
    # Tearline's 40 mines are within 2 km of an IPIS site), so a claim they
    # BOTH place on the same side of 1.0 is a claim two different sampling
    # biases could not manufacture between them - and one they straddle is a
    # claim whose sign depends on who was looking, which is exactly the thing a
    # reader must not be shown as a single verdict.
    #
    # STRATA ARE EXCLUDED HERE. A split of one survey shares that survey's
    # footprint, its definition of a mine and its spelling; two halves landing
    # on the same side of 1.0 is arithmetic, not corroboration, and counting it
    # as agreement would let one list vote three times. They get their own
    # section below, which asks the opposite question.
    out["agreement"] = {}
    for sheet, by_id in out["sheets"].items():
        indep = {t: r for t, r in by_id.items() if not r.get("stratum_of")}
        if len(indep) < 2:
            continue
        lines = []
        for kind in ("units", "junctions"):
            for com in sorted({c for r in indep.values() for c in r[kind]}):
                # PER FLOOR, because w>=1 and w>=3 are different claims about
                # different ground. Quoting each list at its own highest floor
                # would compare "any host" with "classic" whenever one of them
                # grades nothing that highly, and print the mismatch as a
                # disagreement between surveys.
                for floor in ("1", "2", "3"):
                    per = {}
                    for tid, r in indep.items():
                        got = at_floor(r[kind].get(com) or {}, floor)
                        if got:
                            per[tid] = got["lift"]
                    if len(per) < 2:
                        continue
                    signs = {l > 1.0 for l in per.values()}
                    verdict = "agree" if len(signs) == 1 else "DISAGREE"
                    out["agreement"].setdefault(sheet, {})[
                        f"{com}|{kind}|w{floor}"] = {
                            "verdict": verdict, "lifts": dict(per)}
                    shown = ", ".join(f"{t} {l:.2f}x"
                                      for t, l in sorted(per.items()))
                    lines.append(f"  {com:>11} {kind:<10} w>={floor}  "
                                 f"{verdict:<9} {shown}")
        if not lines:
            # NOTHING COMPARABLE IS A RESULT, AND IT PRINTS. Silence here is
            # indistinguishable from "everything agreed", which is how the
            # int/str floor bug survived a full run.
            print(f"\nAGREEMENT - {sheet}: {len(indep)} independent list(s), "
                  "no (commodity, kind, floor) scored by two of them")
            continue
        # The header names the lists that actually produced a comparable row -
        # a list under the floor everywhere (Crisis Tracker) contributes
        # nothing and must not be credited in a headline as though it did.
        took = sorted({t for v in out["agreement"][sheet].values()
                       for t in v["lifts"]})
        print(f"\nAGREEMENT - {sheet}: {' vs '.join(took)}")
        for ln in lines:
            print(ln)

    # DOES THE POOLED NUMBER DEPEND ON WHO COULD REACH THE GROUND?
    #
    # The strata answer a different question from the agreement block, and the
    # answer travels with the pooled row rather than in a commit message: a
    # spread far from 1.0 means the headline lift is partly about access, and a
    # reader who quotes it as geology is quoting an unnamed variable.
    #
    # TWO RULES THIS BLOCK GOT WRONG ONCE, BOTH OF WHICH MANUFACTURED A NUMBER:
    #
    #  1. THE POOLED LIFT IS NOT COMPARABLE TO A STRATUM'S. Each stratum is
    #     scored in its own hull and the pooled list against the mapped sheet,
    #     so "pooled 0.38x -> armed 1.07x" prints a change of DENOMINATOR as if
    #     it were an effect. The pooled value is not shown here at all; the
    #     strata are only ever compared with each other.
    #  2. THE FLOORS MUST LINE UP, AND ALL OF THEM ARE REPORTED. Quoting each
    #     stratum at its own highest floor compares w>=3 with w>=2 whenever one
    #     of them grades no ground that highly - and picking the highest shared
    #     floor buries the finding: on CAR gold units the strata differ 1.55x at
    #     w>=1 (2.04 vs 1.31, the tested result) and 1.04x at w>=2.
    out["strata"] = {}
    for sheet, by_id in out["sheets"].items():
        groups = collections.defaultdict(dict)
        for tid, r in by_id.items():
            if r.get("stratum_of"):
                groups[r["stratum_of"]][r["stratum"]] = r
        for parent, per_stratum in sorted(groups.items()):
            if len(per_stratum) < 2:
                continue
            print(f"\nSTRATA - {sheet}/{parent}: "
                  f"{' vs '.join(sorted(per_stratum))} "
                  "(one survey split by armed presence recorded on site; each "
                  "scored in its OWN hull, so these compare only with each "
                  "other - never with the pooled row)")
            for kind in ("units", "junctions"):
                for com in sorted({c for r in per_stratum.values() for c in r[kind]}):
                    for floor in ("1", "2", "3"):
                        per = {}
                        for name, r in per_stratum.items():
                            got = at_floor(r[kind].get(com) or {}, floor)
                            if got:
                                per[name] = got["lift"]
                        if len(per) < 2:
                            continue
                        lo, hi = min(per.values()), max(per.values())
                        spread = (hi / lo) if lo else None
                        out["strata"].setdefault(sheet, {})[
                            f"{com}|{kind}|w{floor}"] = {
                                "parent": parent, "lifts": dict(per),
                                "spread": spread}
                        shown = ", ".join(f"{n} {l:.2f}x"
                                          for n, l in sorted(per.items()))
                        tail = ("  spread undefined (a stratum scores 0)"
                                if spread is None else f"  spread {spread:.2f}x")
                        print(f"  {com:>11} {kind:<10} w>={floor}  {shown}{tail}")
            # THE TEST THAT MAKES THE SPREAD MORE THAN AN EYEBALL. Invariant 12:
            # a difference quoted without its p is a difference judged by eye.
            try:
                rs = json.load(open(REACH_STRATA))
                cp = rs["capture_permutation"]
                out["strata"].setdefault(sheet, {})["_permutation"] = cp
                print(f"  gold capture {cp['capture_armed']:.1%} armed vs "
                      f"{cp['capture_unarmed']:.1%} unarmed, p={cp['p']} "
                      f"({cp['permutations']} permutations; "
                      f"p={cp['within_prefecture']['p']} within prefecture) "
                      f"- {REACH_STRATA}")
            except (OSError, KeyError):
                # Absent is UNTESTED, and it says so: a spread printed with no
                # p beside it must not read as a tested finding.
                print(f"  no permutation test on file ({REACH_STRATA} absent) "
                      "- the spread above is UNTESTED; run "
                      "scripts/eval_reach_strata.py")
                out["strata"].setdefault(sheet, {})["_permutation"] = None

    if args.json:
        # NaN/Infinity are not JSON, and json.dump writes them anyway - which
        # makes the file unreadable to every consumer except Python, including
        # the Go test that pins the shipped scores to it. A ratio with a zero
        # denominator is UNDEFINED, so it ships as null: absent, not zero.
        def jsonable(v):
            if isinstance(v, float) and (v != v or v in (float("inf"), float("-inf"))):
                return None
            if isinstance(v, dict):
                return {k: jsonable(x) for k, x in v.items()}
            if isinstance(v, list):
                return [jsonable(x) for x in v]
            return v
        json.dump(jsonable(out), open(args.json, "w"), indent=1, allow_nan=False)
        print("wrote", args.json)


if __name__ == "__main__":
    main()
