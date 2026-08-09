"""The printed legend of each geology sheet, measured off the scan.

Why this file exists at all
--------------------------
Vectorizing a scanned geological map is colour-quantization: every pixel is
assigned to the legend swatch it matches.  So the legend *is* the class list,
and a wrong swatch colour is not a cosmetic error - it silently relabels a
whole formation.  Therefore every colour here is **sampled from the scan**, at
a recorded pixel box, and `verify()` re-samples it so drift is detectable.  No
colour was picked by eye or copied from a publication.

Three things the sampling has to survive, all of which broke a naive version:

* **Halftone.** Neither sheet uses flat fill; both are screened dot patterns.
  The median of raw pixels inside a swatch is pulled toward the paper by the
  white between dots.  `sample_swatch` medians a median-blurred copy instead,
  which is the screen's own average.
* **The code is printed *on* the swatch.** `QF`, `CT`, `bA` ... sit inside the
  colour box in dark ink.  Pixels darker than their local background by more
  than `INK_DELTA` are dropped before the median; without that, every swatch
  reads a few percent too dark and pale ones read grey.
* **Adjacent swatches are not separated by a rule** on the Sudan sheet - the
  colour column is one continuous strip.  So the boxes below are *centres with
  a margin*, not detected rectangles, and the Sudan pitch was fitted to the
  detected band edges (42.7 px, origin 48) before being frozen here.

Coordinates are **pixels in the source TIFF**, not in any crop, so this file
stands alone.

Indistinguishable classes
-------------------------
`Legend.merge_groups()` reports sets of codes whose printed colours are closer
than `SEPARABLE_DIST` in RGB.  On the Sudan sheet six pairs are effectively the
same ink (QE/QD, QC/QB, QF/QA, TC/TB/TA, ...).  A quantizer *cannot* separate
them and must not pretend to: the vectorizer emits the merged class and the UI
labels it with every member code.  This is a property of the printing, not a
bug in the extraction - the sheet distinguishes those units by position in the
legend column, which the map body does not carry.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, asdict

import cv2
import numpy as np

# a pixel this much darker than its local background is ink (a code letter,
# a hachure, a contact), not fill
INK_DELTA = 12
# RGB euclidean distance below which two swatches cannot be told apart
SEPARABLE_DIST = 18.0
# swatches within this distance of the paper tone cannot be found by colour
# alone in the map body - the vectorizer must reach them by "inside the
# cutline and not any other class", never by matching the paper
PAPER_DIST = 26.0
PAPER_RGB = {"sudan": (247, 239, 206), "car": (248, 244, 224)}

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
OUT_DIR = os.path.join(ROOT, "data", "geomaps")


@dataclass
class Unit:
    code: str          # as printed
    name: str          # the printed description, trimmed
    group: str         # the printed era / assemblage heading
    box: tuple         # (x, y, w, h) sample box in source-TIFF pixels
    hex: str = ""      # filled by sample()
    rgb: tuple = ()
    commodities: list = field(default_factory=list)


def sample_swatch(img, x, y, w, h):
    """Median fill colour of a screened swatch, ignoring printed ink."""
    box = img[y:y + h, x:x + w]
    if box.size == 0:
        raise ValueError("empty swatch box")
    blur = cv2.medianBlur(box, 9 if min(box.shape[:2]) > 9 else 3)
    g = cv2.cvtColor(box, cv2.COLOR_BGR2GRAY).astype(np.float32)
    gb = cv2.cvtColor(blur, cv2.COLOR_BGR2GRAY).astype(np.float32)
    keep = blur.reshape(-1, 3)[((gb - g) < INK_DELTA).reshape(-1)]
    if len(keep) < 50:
        keep = blur.reshape(-1, 3)
    b, g_, r = np.median(keep, axis=0)
    return (int(r), int(g_), int(b))


# ---------------------------------------------------------------------------
# Sudan, GRAS 2004.  Legend block occupies x 5669..6851, y 5366..8120 of the
# source TIFF.  The rock-unit colour column is x 5941..6007 (the codes are
# printed in it); the 26 Phanerozoic/Nubian bands are a regular 42.7 px pitch
# from y 5414, fitted to the detected band edges and then frozen.  The
# Pan-African and Proterozoic blocks below are not a regular column - they are
# subdivided diagonally - so each is a hand-placed box inside one facies.
# ---------------------------------------------------------------------------
_SL_X, _SL_Y = 5669, 5366        # legend crop origin in the source TIFF
_SUD_COL_X, _SUD_COL_W = 272, 66  # colour column, legend-crop coords
_SUD_Y0, _SUD_PITCH = 70.0, 43.0

_SUD_COLUMN = [
    ("QF", "Recent alluvium and wadi deposits", "Quaternary"),
    ("QE", "Colluvium, sand sheets and amalgamated dunes", "Quaternary"),
    ("QD", "Older alluviums, raised terraces, younger gravel and sand plains", "Quaternary"),
    ("QC", "Lacustrine deposits, alluvial fans, dunes and dune fields", "Quaternary"),
    ("QB", "Palaeolevees, old gravel and stabilized dunes", "Quaternary"),
    ("QA", "Raised coral reef", "Quaternary"),
    ("TQ", "Umm Ruwaba Formation: gravel, sand, silt and clay", "Tertiary-Quaternary"),
    ("TD", "Undifferentiated Tertiary sandstone, Hudi chert and Miocene Red Sea sediments", "Tertiary"),
    ("TC", "Upper Abyad: cherty limestone, marine sand and laterite", "Tertiary"),
    ("TB", "Abyad limestone", "Tertiary"),
    ("TA", "Middle Abyad: siltstone, sandstone, marl, carbonate, clay and gypsum", "Tertiary"),
    ("KU", "Basal Abyad: fluviatile or near-shore marine sandstone, partly shale near top", "Cretaceous"),
    ("KM", "Fluviatile sandstone, lacustrine siltstone and mudstone", "Cretaceous"),
    ("KL", "Sandstone and siltstone", "Cretaceous"),
    ("JK", "Fluviatile sandstone", "Jurassic-Cretaceous"),
    ("J", "Gilf Kebir sandstone: massive fluviatile sandstone and some mudstone", "Jurassic"),
    ("PT", "Lakia Formation: fluviatile sandstone, lacustrine siltstone and mudstone", "Permo-Triassic"),
    ("CU", "Lacustrine sandstone, siltstone, gypsum and mudstone with thin lignite beds", "Carboniferous"),
    ("CL", "Sandstone with Visean flora", "Carboniferous"),
    ("D", "Marine and fluvial sandstone, major shale and siltstone beds in lower part", "Devonian"),
    ("DL", "Fluvial sandstone, Uweinat area", "Devonian"),
    ("S", "Umm Ras Formation: sandstone with Cruziana accacensis, basal shale and some tillite", "Silurian"),
    ("O", "Karkur Talh Formation, Uweinat area: sandstone with Cruziana rouaulti", "Ordovician"),
    ("CO", "Cambro-Ordovician fluviatile sandstone and glacial deposits", "Cambro-Ordovician"),
    ("C", "Amaki Series: molassic sandstone, conglomerate, greywacke and minor limestone", "Cambrian"),
    ("PZs", "Undifferentiated Palaeozoic sediments", "Palaeozoic"),
]

# code, description, group, (x, y, w, h) in legend-crop coords
_SUD_BLOCKS = [
    ("IY", "Younger intrusions (IYg granite, IYs syenite, IYb gabbro)",
     "Pan-African igneous", (105, 1265, 55, 50)),
    ("IU", "Undifferentiated syn- to late-orogenic intrusions (granite, gabbro, syenite, diorite)",
     "Pan-African igneous", (185, 1265, 60, 50)),
    ("IO", "Older intrusions (granite and granodiorite, gabbro, anorthosite, quartz porphyry)",
     "Pan-African igneous", (275, 1265, 55, 50)),
    ("MSv", "Volcano-sedimentary greenschist assemblage",
     "Pan-African metasediments", (150, 1345, 140, 26)),
    ("MSc", "Conglomerate and sandstone, slate, phyllite and schist",
     "Pan-African metasediments", (200, 1390, 80, 20)),
    ("MSm", "Marble", "Pan-African metasediments", (210, 1435, 70, 18)),
    ("MSq", "Quartzite", "Pan-African metasediments", (225, 1478, 70, 18)),
    ("MVa", "Acidic metavolcanics", "Pan-African metavolcanics", (175, 1520, 60, 20)),
    ("MVb", "Intermediate-basic metavolcanics", "Pan-African metavolcanics", (270, 1565, 60, 20)),
    ("OP", "Ophiolite: mafic oceanic upper sequence (OPm) and ultramafic mantle lower sequence (OPu)",
     "Pan-African ophiolite", (180, 1605, 70, 20)),
    ("GAn", "Gneiss (roots of the arc assemblage)", "Pan-African gneiss/amphibolite", (175, 1700, 60, 40)),
    ("GAm", "Amphibolite (roots of the arc assemblage)", "Pan-African gneiss/amphibolite", (270, 1700, 60, 40)),
    ("PMg", "Gneiss", "Middle Proterozoic basement", (150, 1815, 150, 26)),
    ("PMm", "Marble", "Middle Proterozoic basement", (150, 1858, 150, 26)),
    ("PMq", "Quartzite", "Middle Proterozoic basement", (150, 1901, 150, 26)),
    ("PMa", "Amphibolite", "Middle Proterozoic basement", (150, 1944, 150, 26)),
    ("PMs", "Graphitic schist", "Middle Proterozoic basement", (150, 1987, 150, 26)),
    ("PLu", "Undifferentiated metamorphic rocks, migmatite (PLt) and gneiss (PLg)",
     "Lower Proterozoic basement", (150, 2073, 150, 26)),
    ("PLp", "Para schist", "Lower Proterozoic basement", (150, 2115, 150, 26)),
    ("PLm", "Marble and calc-silicate rocks", "Lower Proterozoic basement", (150, 2157, 150, 26)),
    ("PLq", "Quartzite", "Lower Proterozoic basement", (150, 2199, 150, 26)),
    ("PLr", "Banded iron formation", "Lower Proterozoic basement", (150, 2242, 150, 26)),
    ("PLc", "Ferruginous chert", "Lower Proterozoic basement", (150, 2285, 150, 26)),
    ("PLs", "Graphitic schist", "Lower Proterozoic basement", (150, 2328, 150, 26)),
    ("PLa", "Amphibolite", "Lower Proterozoic basement", (150, 2370, 150, 26)),
    ("ARg", "Granulite with mylonitised and retrogressed equivalents", "Archaean craton", (150, 2455, 150, 26)),
    ("ARn", "Granulite (noritic varieties)", "Archaean craton", (150, 2498, 150, 26)),
]


def _sudan_units():
    units = []
    for i, (code, name, group) in enumerate(_SUD_COLUMN):
        yc = _SUD_Y0 + _SUD_PITCH * i
        units.append(Unit(code, name, group,
                          (_SL_X + _SUD_COL_X, _SL_Y + int(yc) - 13, _SUD_COL_W, 26)))
    for code, name, group, (x, y, w, h) in _SUD_BLOCKS:
        units.append(Unit(code, name, group, (_SL_X + x, _SL_Y + y, w, h)))
    return units


# ---------------------------------------------------------------------------
# CAR, BRGM 1964.  Legend block occupies x 19235.., y 671.. of the source TIFF
# at 600 dpi, so its swatches are ~200x130 px with a printed rule around each -
# they were located by contour, then frozen here.  Codes are Greek/subscripted
# on the sheet; the ASCII spelling used here is given in `code` and the printed
# form in `name` where it differs.
# ---------------------------------------------------------------------------
_CL_X, _CL_Y = 19235, 671

_CAR_BLOCKS = [
    ("a2", "Formations alluviales recentes", "Quaternaire", (1236, 789, 193, 123)),
    ("a1", "Formations neo-tchadiennes: sables, sables argileux, argiles d'origine fluvio-lacustre",
     "Quaternaire", (1235, 988, 195, 129)),
    ("CT", "Formations paleo-tchadiennes, Continental Terminal: gres ferrugineux, sables beiges, cuirasses lateritiques",
     "Tertiaire", (1232, 1378, 198, 125)),
    ("PB", "Formations des plateaux de Bambio: gres silicifies, sables beiges, limons sableux",
     "Tertiaire", (2712, 1384, 198, 126)),
    ("GC2", "Gres de Carnot-Berberati: formations fluvio-lacustres (gres, gres kaoliniques, argiles, conglomerats)",
     "Secondaire", (1230, 1968, 198, 130)),
    ("GO", "Gres de Mouka-Ouadda: formations fluvio-lacustres (gres, gres kaoliniques, argiles, conglomerats)",
     "Secondaire", (2709, 1972, 199, 131)),
    ("GC1", "Formations fluvio-glaciaires (argilites a nodules calcaires, tillites)",
     "Primaire", (1229, 2290, 200, 134)),
    ("Bi", "Complexe tillitique de la Bandja et series de Nola, Kouki, Bangui-Mbaiki, Ouadikei, Fourou-mbala, Banga, Tandja, Kosha, Moyen-Chinko, Markla, Coumbal",
     "Precambrien A - groupe superieur", (1229, 3230, 197, 127)),
    ("bA", "Intrusions basiques", "Precambrien A - groupe superieur", (1228, 3624, 198, 130)),
    ("S", "Schistes epimetamorphiques dominants (a sericite, a chlorite), quartzites",
     "Precambrien D - facies cristallophyllien", (1228, 4226, 198, 129)),
    ("Q", "Quartzites dominants (a sericite, a muscovite, a chlorite), quartzites vitreux, quartzites feldspathiques, conglomerats quartzeux, quartzito-schistes, micaschistes",
     "Precambrien D - facies cristallophyllien", (1227, 4390, 199, 128)),
    ("Xi", "Micaschistes dominants (a muscovite, a deux micas, corbures, a grenat, a disthene), quartzites, amphibolo-schistes",
     "Precambrien D - facies cristallophyllien", (1227, 4550, 199, 128)),
    ("Zeta", "Gneiss a micas, amphibolites, a pyroxenes, leptynites",
     "Precambrien D - facies cristallophyllien", (1225, 4705, 201, 137)),
    ("A", "Amphibolites, amphibolopyroxenites, pyroxenites",
     "Precambrien D - facies cristallophyllien", (1226, 4872, 199, 132)),
    ("C", "Formations charnockitiques ou a facies malgachitique",
     "Precambrien D - facies cristallophyllien", (1226, 5033, 200, 130)),
    ("D", "Complexe de base indifferencie",
     "Precambrien D - facies cristallophyllien", (1226, 5192, 198, 130)),
    ("M", "Facies migmatitiques: embrechites (Me), anatexites (Ma)",
     "Precambrien D - facies cristallophyllien", (2709, 4800, 193, 127)),
    ("bD", "Intrusions basiques recristallisees", "Precambrien D - facies cristallin", (1224, 5573, 201, 128)),
    ("gamma_c", "Granites en massifs circonscrits: sub-alcalins, calco-alcalins, calco-sodiques",
     "Precambrien D - facies cristallin", (1221, 5780, 204, 132)),
    ("gamma_h", "Granites heterogenes concordants: syncinematiques, d'anatexie",
     "Precambrien D - facies cristallin", (1221, 5943, 205, 133)),
]


def _car_units():
    return [Unit(code, name, group, (_CL_X + x + 8, _CL_Y + y + 8, w - 16, h - 16))
            for code, name, group, (x, y, w, h) in _CAR_BLOCKS]


SHEET_UNITS = {"sudan": _sudan_units, "car": _car_units}


# ---------------------------------------------------------------------------
# Commodity affinity.
#
# This is an INFERENCE OVER LITHOLOGY, not an occurrence dataset.  It says
# "rocks of this kind are the kind that host X", which is a textbook statement
# about a rock type, not evidence that anything was ever found at a given
# point.  It is labelled as such everywhere it surfaces - compare the mining
# verdict in AGENTS.md: inference from context ships, fabricated evidence does
# not.  Nothing here counts, ranks or locates a deposit.
#
# `weight` is a coarse 1-3 prospectivity: 3 = classic host, 2 = plausible host,
# 1 = weak/derived association (e.g. placer downstream of a lode host).
# ---------------------------------------------------------------------------
COMMODITY_LABELS = {
    "gold": "Gold",
    "diamond": "Diamond",
    "uranium": "Uranium",
    "iron": "Iron",
    "lithium": "Lithium / rare metals",
    "cobalt": "Cobalt / nickel / chromium",
    "copper": "Copper / base metals",
    "rare_earth": "Rare earths / niobium",
}

# (sheet, code) -> [(commodity, weight, why)].  Keyed by SHEET as well as code
# because the two sheets reuse letters for unrelated things: "S" is Silurian
# sandstone on Sudan and an epimetamorphic schist belt on CAR, "C" is Cambrian
# molasse vs charnockite, "D" is Devonian sandstone vs undifferentiated
# basement.  A code-only table silently gave CAR's gold-bearing schist the
# uranium affinity of a Sudanese sandstone.
_AFF_SUDAN = {
    "MSv": [("gold", 3, "volcano-sedimentary greenschist belt: the classic orogenic-gold host"),
            ("copper", 2, "volcanogenic massive sulphide setting")],
    "MVa": [("gold", 3, "acidic metavolcanics of the arc assemblage host Nubian Shield lode gold"),
            ("copper", 2, "felsic volcanic-hosted massive sulphide")],
    "MVb": [("copper", 2, "mafic-intermediate metavolcanics"),
            ("gold", 2, "arc metavolcanics adjacent to the mineralised belts")],
    "MSc": [("gold", 2, "metasedimentary belt rocks between the gold-bearing volcanics")],
    "MSq": [("gold", 1, "quartzite: silica-rich unit within the mineralised belt")],
    "OP": [("cobalt", 3, "ophiolite: chromite, nickel and cobalt in ultramafics"),
           ("gold", 2, "listwaenite alteration along ophiolite thrusts is gold-bearing")],
    "IY": [("gold", 2, "late granites drive the hydrothermal systems"),
           ("lithium", 2, "evolved granite: pegmatite and greisen affinity"),
           ("rare_earth", 2, "alkaline/younger granite association")],
    "IU": [("gold", 2, "syn- to late-orogenic intrusions"),
           ("lithium", 1, "granitic pegmatite affinity")],
    "IO": [("copper", 1, "older calc-alkaline intrusions"),
           ("gold", 1, "granodiorite of the arc assemblage")],
    "GAn": [("gold", 1, "gneissic basement of the arc roots")],
    "GAm": [("cobalt", 1, "amphibolite: metamorphosed mafic protolith")],
    "PLr": [("iron", 3, "banded iron formation")],
    "PLc": [("iron", 2, "ferruginous chert associated with the iron formations"),
            ("gold", 1, "BIF-hosted gold association")],
    "PLa": [("cobalt", 1, "amphibolite: metamorphosed mafic protolith")],
    "PLq": [("gold", 1, "quartzite: possible palaeoplacer host")],
    "PMa": [("cobalt", 1, "amphibolite: metamorphosed mafic protolith")],
    "ARg": [("uranium", 1, "granulite-facies cratonic basement")],
    "ARn": [("cobalt", 1, "noritic granulite: mafic protolith")],
    "PZs": [("uranium", 2, "Palaeozoic sandstone: roll-front uranium host")],
    "C": [("uranium", 1, "Cambrian molasse sandstone")],
    "CO": [("uranium", 2, "fluviatile sandstone: roll-front uranium host")],
    "S": [("uranium", 1, "Palaeozoic sandstone sequence")],
    "D": [("uranium", 1, "Devonian fluvial sandstone")],
    "DL": [("uranium", 1, "Devonian fluvial sandstone")],
    "J": [("uranium", 2, "Nubian-type massive fluviatile sandstone: roll-front host")],
    "KU": [("uranium", 2, "Nubian sandstone: roll-front uranium host")],
    "KM": [("uranium", 1, "Nubian sandstone")],
    "KL": [("uranium", 1, "Nubian sandstone")],
    "QD": [("gold", 1, "older alluvium: placer affinity downstream of lode hosts")],
    "QF": [("gold", 1, "recent alluvium and wadi fill: artisanal placer ground")],
}

_AFF_CAR = {
    "Bi": [("diamond", 3, "the Bandja/Nola tillitic and sandstone series carry CAR's alluvial diamond field"),
           ("gold", 2, "same series hosts the western gold workings")],
    "GC1": [("diamond", 2, "fluvio-glacial formations underlying the diamondiferous gravels")],
    "GC2": [("diamond", 3, "Carnot-Berberati sandstone: the principal secondary diamond reservoir")],
    "GO": [("diamond", 3, "Mouka-Ouadda sandstone: the eastern secondary diamond reservoir")],
    "S": [("gold", 2, "epimetamorphic schist belt: orogenic gold host")],
    "Q": [("gold", 2, "quartzite and quartz-vein-bearing schist belt")],
    "Xi": [("gold", 2, "micaschist belt adjacent to the gold workings"),
           ("lithium", 1, "pegmatite affinity in micaschist")],
    "A": [("cobalt", 2, "amphibolite and pyroxenite: mafic-ultramafic affinity"),
          ("copper", 1, "mafic-hosted base metals")],
    "bA": [("cobalt", 2, "basic intrusions: nickel-cobalt-chromium affinity"),
           ("copper", 1, "mafic intrusion-hosted base metals")],
    "bD": [("cobalt", 2, "recrystallised basic intrusions")],
    "C": [("cobalt", 1, "charnockitic formations: mafic granulite affinity")],
    "gamma_c": [("lithium", 2, "circumscribed granite massifs: pegmatite and greisen affinity"),
                ("rare_earth", 2, "evolved granite association"),
                ("gold", 1, "granite-driven hydrothermal systems")],
    "gamma_h": [("lithium", 1, "anatectic granite: weaker pegmatite affinity"),
                ("uranium", 1, "anatectic granite")],
    "Zeta": [("uranium", 1, "high-grade gneiss of the basement complex")],
    "M": [("uranium", 1, "migmatite of the basement complex")],
    "a1": [("diamond", 2, "neo-Chadian alluvium: the working alluvial diamond ground"),
           ("gold", 1, "alluvial gold ground")],
    "a2": [("diamond", 2, "recent alluvium: the working alluvial diamond ground"),
           ("gold", 1, "alluvial gold ground")],
    "CT": [("diamond", 1, "Continental Terminal cover over the diamondiferous series")],
}

AFFINITY = {}
for _sheet, _tbl in (("sudan", _AFF_SUDAN), ("car", _AFF_CAR)):
    for _code, _rows in _tbl.items():
        AFFINITY[(_sheet, _code)] = _rows


@dataclass
class Legend:
    sheet: str
    units: list

    # -- construction ------------------------------------------------------
    @classmethod
    def measure(cls, sheet, img=None, src=None):
        """Sample every swatch out of the source scan."""
        if img is None:
            src = src or _sheet_src(sheet)
            img = cv2.imread(src, cv2.IMREAD_COLOR)
            if img is None:
                raise FileNotFoundError(src)
        units = SHEET_UNITS[sheet]()
        h, w = img.shape[:2]
        for u in units:
            x, y, bw, bh = u.box
            if x < 0 or y < 0 or x + bw > w or y + bh > h:
                raise ValueError("swatch box %r for %s is outside the %dx%d scan"
                                 % (u.box, u.code, w, h))
            u.rgb = sample_swatch(img, x, y, bw, bh)
            u.hex = "#%02x%02x%02x" % u.rgb
            u.commodities = [
                dict(commodity=c, weight=wt, why=why)
                for c, wt, why in AFFINITY.get((sheet, u.code), [])
            ]
        return cls(sheet, units)

    @classmethod
    def load(cls, sheet):
        with open(os.path.join(OUT_DIR, "legend_%s.json" % sheet)) as fh:
            d = json.load(fh)
        return cls(d["sheet"], [Unit(**{**u, "box": tuple(u["box"]),
                                        "rgb": tuple(u["rgb"])}) for u in d["units"]])

    def save(self):
        os.makedirs(OUT_DIR, exist_ok=True)
        path = os.path.join(OUT_DIR, "legend_%s.json" % self.sheet)
        with open(path, "w") as fh:
            json.dump(dict(sheet=self.sheet, n_units=len(self.units),
                           merge_groups=self.merge_groups(),
                           paper_like=self.paper_like(),
                           units=[asdict(u) for u in self.units]),
                      fh, indent=1)
        return path

    # -- properties --------------------------------------------------------
    def palette(self):
        """(N,3) float array of RGB, row i matching units[i]."""
        return np.array([u.rgb for u in self.units], dtype=np.float32)

    def merge_groups(self):
        """Sets of codes whose printed colours are not separable.

        Returned as a list of lists of codes, only for groups of 2+.  The
        quantizer must treat each group as ONE class; see the module docstring.
        """
        pal = self.palette()
        n = len(pal)
        parent = list(range(n))

        def find(i):
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i

        for i in range(n):
            for j in range(i + 1, n):
                if float(np.linalg.norm(pal[i] - pal[j])) < SEPARABLE_DIST:
                    a, b = find(i), find(j)
                    if a != b:
                        parent[a] = b
        groups = {}
        for i in range(n):
            groups.setdefault(find(i), []).append(self.units[i].code)
        return [sorted(set(g)) for g in groups.values() if len(set(g)) > 1]

    def paper_like(self):
        """Codes printed in an ink too close to the paper to find by colour.

        CAR's migmatite `M` is an almost-white box with a printed letter, and
        Sudan's palest Quaternary units are only a few units off the paper
        tone.  A quantizer keyed on colour alone will label every unmapped
        margin, every legend box and every inset as that unit.  These must be
        resolved by exclusion inside the cutline instead, so the vectorizer
        asks for this list rather than discovering the problem as a country
        covered in migmatite.
        """
        paper = np.array(PAPER_RGB[self.sheet], dtype=np.float32)
        return [u.code for u in self.units
                if float(np.linalg.norm(np.array(u.rgb, np.float32) - paper)) < PAPER_DIST]

    def commodity_index(self):
        """commodity -> [(code, weight)], for the UI's group toggles."""
        out = {}
        for u in self.units:
            for c in u.commodities:
                out.setdefault(c["commodity"], []).append((u.code, c["weight"]))
        return {k: sorted(v, key=lambda t: (-t[1], t[0])) for k, v in sorted(out.items())}

    # -- QA ----------------------------------------------------------------
    def verify(self, img=None, tol=6.0):
        """Re-sample and report drift; a nonzero return means the boxes moved."""
        fresh = Legend.measure(self.sheet, img=img)
        bad = []
        for a, b in zip(self.units, fresh.units):
            d = float(np.linalg.norm(np.array(a.rgb, float) - np.array(b.rgb, float)))
            if d > tol:
                bad.append((a.code, a.hex, b.hex, round(d, 1)))
        return bad


def _sheet_src(sheet):
    from sheets import SHEETS  # local import: sheets.py has no cv2 dependency
    return os.path.join(ROOT, SHEETS[sheet]["src"])


def contact_sheet(leg, path, img=None):
    """Render every unit as a swatch beside its sampled colour - the eyeball QA."""
    if img is None:
        img = cv2.imread(_sheet_src(leg.sheet), cv2.IMREAD_COLOR)
    rows = []
    for u in leg.units:
        x, y, w, h = u.box
        crop = cv2.resize(img[y:y + h, x:x + w], (120, 46), interpolation=cv2.INTER_AREA)
        flat = np.zeros((46, 60, 3), np.uint8)
        flat[:] = (u.rgb[2], u.rgb[1], u.rgb[0])
        lab = np.full((46, 430, 3), 255, np.uint8)
        cv2.putText(lab, u.code, (4, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 0), 1)
        cv2.putText(lab, u.hex, (110, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (90, 90, 90), 1)
        com = ",".join(c["commodity"] for c in u.commodities)
        cv2.putText(lab, com[:52], (4, 38), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (160, 40, 40), 1)
        rows.append(np.hstack([crop, flat, lab]))
    half = (len(rows) + 1) // 2
    left, right = rows[:half], rows[half:]
    while len(right) < len(left):
        right.append(np.full_like(left[0], 255))
    cv2.imwrite(path, np.hstack([np.vstack(left), np.vstack(right)]))
    return path


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("sheet", choices=sorted(SHEET_UNITS))
    ap.add_argument("--contact", metavar="PNG", help="write a QA contact sheet")
    ap.add_argument("--verify", action="store_true",
                    help="re-sample the saved legend and exit 1 on drift")
    a = ap.parse_args(argv)

    img = cv2.imread(_sheet_src(a.sheet), cv2.IMREAD_COLOR)
    if img is None:
        raise SystemExit("cannot read %s" % _sheet_src(a.sheet))

    if a.verify:
        leg = Legend.load(a.sheet)
        bad = leg.verify(img=img)
        for code, was, now, d in bad:
            print("DRIFT %-8s %s -> %s  (%.1f)" % (code, was, now, d))
        print("%d/%d units drifted" % (len(bad), len(leg.units)))
        return 1 if bad else 0

    leg = Legend.measure(a.sheet, img=img)
    path = leg.save()
    print("%s: %d units -> %s" % (a.sheet, len(leg.units), path))
    for g in leg.merge_groups():
        print("  not separable: %s" % " = ".join(g))
    pl = leg.paper_like()
    if pl:
        print("  paper-like (resolve by exclusion, not by colour): %s" % ", ".join(pl))
    idx = leg.commodity_index()
    for c, rows in idx.items():
        print("  %-10s %d units (%s)" % (c, len(rows), ", ".join(r[0] for r in rows[:8])))
    if a.contact:
        print("  contact sheet -> %s" % contact_sheet(leg, a.contact, img=img))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
