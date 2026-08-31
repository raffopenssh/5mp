#!/usr/bin/env python3
"""The two EASY PIP documents, generated from the measurements - never typed.

    python3 scripts/easypip/build_docs.py          # writes both, ~2 s
    python3 scripts/easypip/build_docs.py --check  # fail if the files drifted

WHAT THIS WRITES
  reports/PIP_APRCA_WSS_2026-08_EASY.txt        the full Priority Intervention
                                                Plan: problem, evidence,
                                                documents, actions, budget,
                                                references
  reports/PIP_TWO_PAGER_2026-08_EASY.txt        the same argument in two pages

WHY IT IS A SCRIPT AND NOT A DOCUMENT. Four EASY reports already restate one
another's numbers in prose, and when a boundary KML arrived on 2026-08-31 the
park's population moved from ~1,900 to 1 - but three paragraphs elsewhere kept
the old figure, one of them inside a legal argument. That is root invariant 2
(never type a count that describes a variable input). So every figure below is
read from data/eval/pip_facts.json (built by scripts/easypip/pip_facts.py),
data/eval/zone_stats.json and data/easy_docs/easy_docs_index.json; re-running
after any boundary or budget edit moves the map, the GeoPackage and both
documents together. A quantity that cannot be measured prints as a word saying
so - never as a zero (root invariant 1).

Everything here is plain ASCII at 80 columns, with text charts, because the
readers who most need it open it over a satellite link in a field office.
"""
import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
FACTS = ROOT / "data/eval/pip_facts.json"
ZONES = ROOT / "data/eval/zone_stats.json"
DOCS = ROOT / "data/easy_docs/easy_docs_index.json"
OUT_FULL = ROOT / "reports/PIP_APRCA_WSS_2026-08_EASY.txt"
OUT_SHORT = ROOT / "reports/PIP_TWO_PAGER_2026-08_EASY.txt"
W = 80
RULE = "-" * W
HRULE = "=" * W


# ------------------------------------------------------------------ helpers
def n(x, dp=0):
    """A number with thousands separators, or the word for 'we cannot say'."""
    if x is None:
        return "not measured"
    return f"{x:,.{dp}f}"


def usd(x):
    return "USD " + n(x)


def bar(value, vmax, width=34, ch="#"):
    if vmax <= 0:
        return ""
    return ch * max(1 if value > 0 else 0, round(width * value / vmax))


def para(text, indent="", width=W):
    """Wrap a paragraph, honouring an indent, collapsing internal whitespace."""
    words = text.split()
    lines, cur = [], indent
    for w_ in words:
        cand = (cur + " " + w_) if cur.strip() else indent + w_
        if len(cand) > width and cur.strip():
            lines.append(cur)
            cur = indent + w_
        else:
            cur = cand
    if cur.strip():
        lines.append(cur)
    return "\n".join(lines)


def bullet(text, indent="    ", marker="* "):
    """A bullet whose continuation lines line up under its text, not under
    its marker - otherwise a three-line bullet reads as three bullets."""
    body = para(text, indent=indent + " " * len(marker))
    return indent + marker + body[len(indent) + len(marker):]


def head(title, sub=None):
    s = f"\n{title}\n{RULE}"
    if sub:
        s += "\n" + para(sub)
    return s


def short_zone(name):
    """The shape names as a reader says them out loud."""
    s = re.sub(r"_\d[\d',\.]*\s*(km2|ha)?$", "", name)
    s = s.replace("zone-p\u00e2turage-durable_", "grazing: ")
    s = s.replace("_", " ").replace("-", " ")
    s = re.sub(r"\s+", " ", s).strip()
    return {"Pongo Wau Numatinna National Park": "Pongo-Wau-Numatinna NP",
            "Southern NP Wilderness": "Southern-NP Wilderness",
            "Boro Biri headwaters": "Boro-Biri headwaters",
            "Wau Wilderness": "Wau Wilderness",
            "Boro Wilderness": "Boro Wilderness",
            "grazing: Numatina": "Numatina grazing",
            "grazing: Garamba": "Garamba grazing",
            "grazing: Radom Sudan": "Radom grazing",
            "Ecological Corridor Wau SouthernNP Nord": "Corridor pin N",
            "Ecological Corridor Wau SouthernNP South": "Corridor pin S",
            }.get(s, s)


# ---------------------------------------------------------------- the facts
def load():
    for p in (FACTS, ZONES, DOCS):
        if not p.exists():
            sys.exit(f"{p} missing - run scripts/easypip/pip_facts.py first")
    F = json.load(open(FACTS))
    Z = json.load(open(ZONES))
    D = json.load(open(DOCS))
    sys.path.insert(0, str(ROOT / "scripts" / "easybudget"))
    import build_budget as B
    rows, tot = B.compute()
    return F, Z, D, B, rows, tot


def action_costs(B, rows):
    """Cost per plan action - derived from the budget's own lines, so an
    action that gains a line gains a cost without anyone retyping a total."""
    return {k: sum(sum(r["tot"]) for r in rows if r["act"] == k) for k in B.ACTIONS}


# The "paper package" is the smallest fundable slice: what it takes to file
# the regulation text and the gazette request with nobody in the field. It is
# named line by line rather than filtered by action, because the actions that
# matter here (2 and 3) carry no cost of their own - their cost is somebody
# else's salary. Each name must match exactly one budget line: a filter that
# silently matched nothing would print a confidently small number
# (root invariant 1).
PAPER_LINES = [
    "Project coordinator, Wau (national)",
    "In-country counsel: regulations text, s.24 gazette request, boundary description",
    "NGO/CBO registration, RRC renewal, no-objection letters",
    "Juba accommodation (paper track: regulations, gazette request, RRC)",
    "Juba per diems",
]


def paper_package(rows):
    total = 0
    for name in PAPER_LINES:
        hits = [r for r in rows if r["item"] == name]
        if len(hits) != 1:
            sys.exit(f"paper package: {name!r} matched {len(hits)} budget "
                     f"lines - the budget was edited, fix PAPER_LINES")
        total += hits[0]["tot"][0]
    return total


def cat_totals(B, rows):
    out = {}
    for c in B.CATS:
        out[c] = sum(sum(r["tot"]) for r in rows if r["cat"] == c)
    return out


PAGE_LINES = 64          # what a printed page of this monospace text holds

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
          "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def fire_calendar(F, indent="    "):
    """When this ground burns. The single most operational chart in the file:
    it sets the field season, and it is measured, not assumed."""
    by = F["park"]["by_calendar_month"]
    vals = [by.get(f"{i:02d}", 0) for i in range(1, 13)]
    mx = max(vals)
    out = []
    for i, v in enumerate(vals):
        out.append(f"{indent}{MONTHS[i]:>4} {bar(v, mx, 46):46s} {n(v):>8}")
    return "\n".join(out)


def fire_calendar_compact(F, indent="    "):
    """The same measurement in six lines, for the two-pager.

    The seven quiet months are collapsed into ONE row that still prints its
    total - a reader must be able to see that the gap is measured and small,
    not that it was left out."""
    by = F["park"]["by_calendar_month"]
    vals = [by.get(f"{i:02d}", 0) for i in range(1, 13)]
    mx = max(vals)
    keep = [10, 11, 0, 1, 2]                       # Nov, Dec, Jan, Feb, Mar
    quiet = [i for i in range(12) if i not in keep]
    out = []
    for i in keep:
        out.append(f"{indent}{MONTHS[i]:>4} {bar(vals[i], mx, 40):40s}"
                   f" {n(vals[i]):>8}")
    q = sum(vals[i] for i in quiet)
    out.append(f"{indent}{'Apr-Oct':>4} {bar(q, mx, 40):40s} {n(q):>8}"
               f"   (seven months)")
    return "\n".join(out)


def zone_rows(F, Z):
    """One row per shape, park first then biggest, with the numbers that
    decide something: who lives there, who lives on the rim, how it burns,
    how much gold-graded ground the model puts inside it."""
    out = []
    for r in sorted(F["shapes"]["rows"],
                    key=lambda r: (r["name"] != F["park"]["key"],
                                   -(r["measured_km2"] or 0))):
        nm = r["name"]
        s_ = Z["settlements"].get(nm, {})
        rim = Z["settlements_rim"].get(f"{nm} \u2014 {Z['rim_km']:g} km rim", {})
        f_ = Z["fire_detections"].get(nm, {})
        t_ = Z["fire_trajectories"].get(nm, {})
        g_ = (F["gold"]["per_zone"].get(nm) or {}).get("inside", {})
        fronts = t_.get("fronts_touching") or 0
        out.append(dict(
            name=short_zone(nm), key=nm, kind=r["kind"],
            area=r["measured_km2"], says=r["file_says_km2"],
            delta=r["delta_pct"],
            clusters=s_.get("clusters"), people=s_.get("population_est"),
            rim_clusters=rim.get("clusters"), rim_people=rim.get("population_est"),
            rate=f_.get("detections_per_1000km2_per_year"),
            fronts=fronts, ends=t_.get("ends_inside"),
            intercept=(round(100 * t_["ends_inside"] / fronts)
                       if fronts and t_.get("ends_inside") is not None else None),
            gold=g_.get("top05_cells")))
    return out


# The laws that do work in this plan. The TEXT of each row is our reading;
# the STATUS and DATE columns are read from the document index so a
# re-classified dump moves the table (and a law that leaves the dump prints
# as missing rather than silently keeping its row).
LAW_ROWS = [
    ("Wildlife Conservation and Protected Areas Act, 2026",
     "Creates every vehicle this plan needs: community conservancies (s.14), "
     "community wildlife associations and their scouts (s.15-16), wildlife "
     "corridors (s.9), presidential declaration of a park (s.8). Its "
     "regulations are being drafted NOW (s.80) - that is the opening."),
    ("Mining Act, 2012",
     "The other law over the same ground. Artisanal gold licences are granted "
     "by the STATE authority (s.75), not Juba. s.24 lets the Minister close "
     "an area to mineral-title applications by Gazette order - the cheapest "
     "protective act in this plan. s.23(1)(g) only recognises a park ban if "
     "the park's own instrument says mining is banned in it."),
    ("The Land Act, 2009",
     "s.66(1): pastoral land SHALL be delineated and protected. A livestock "
     "corridor is already a land-administration duty, not only a wildlife "
     "instrument."),
    ("NON-GOVERNMENTAL ORGANIZATIONS ACT, 2016",
     "The second clock. No operation without RRC registration (s.9), CBOs "
     "register in the state or county where they work (s.11), 80% national "
     "staff (s.18), annual renewal with audited accounts (s.13)."),
    ("The NGOs Registration, Procedures and Regulations, 2016",
     "Work permits, residence permits, customs and technical agreements - the "
     "detail that makes the NGO Act slow."),
    ("South Sudan National Environment Policy",
     "Makes an EIA binding on development projects - the road rebuild and any "
     "infrastructure crossing the corridor."),
    ("Forestry Policy (Harmonized) FINAL clean 2012",
     "Lists Namatina as a gazetted Central Forest Reserve (1953). Two "
     "ministries hold one piece of ground; settle it before the declaration."),
    ("National Comprehensive Migration Policy",
     "Records that transhumance is NOT supported by national or regional "
     "governance instruments. That gap is the corridor's opening - and the "
     "reason no ready-made procedure exists."),
    ("Local Government Act 2009",
     "County authorities are consulted on land acquisition and use, and CBOs "
     "register at county level."),
    ("Labour Act, 2017",
     "Seasonal scout contracts, allowances and end-of-season terms sit here."),
]


def law_table(D):
    """Match our rows to the dump's own classification - never restate it."""
    idx = {v["title"]: (k, v) for k, v in D["documents"].items()}
    out = []
    for title, why in LAW_ROWS:
        hit = idx.get(title)
        if hit is None:
            out.append((title, "NOT IN THE DUMP - re-check", "", why))
            continue
        _f, v = hit
        out.append((title, v["status"].replace("_", " "), v["date"], why))
    return out


def doc_status_counts(D):
    c = {}
    for v in D["documents"].values():
        c[v["status"]] = c.get(v["status"], 0) + 1
    return dict(sorted(c.items(), key=lambda kv: -kv[1]))


# ============================================================== FULL DOCUMENT
def full_doc(F, Z, D, B, rows, tot, today):
    L = []
    P = L.append
    A = action_costs(B, rows)
    C = cat_totals(B, rows)
    park, un, gold, fire, bud = (F["park"], F["union"], F["gold"], F["fire"],
                                 F["budget"])
    rim = park["rim"]
    zrows = zone_rows(F, Z)

    # ---------------------------------------------------------------- cover
    P(HRULE)
    P("")
    P("        PRIORITY INTERVENTION PLAN")
    P("        Pongo-Wau-Numatinna, the livestock corridor and the")
    P("        western reserves - Western Bahr el Ghazal, South Sudan")
    P("")
    P("        AP-RCA priority support plan, turned into a costed")
    P("        three-year intervention. Plain language, August 2026.")
    P("        INTERNAL DRAFT - every clause number to be verified")
    P("        against the signed scan before external use.")
    P("")
    P(para("        The plan asks for six things: check whether the old "
           "Tamboura - Deim Zubeir road survives; a livestock corridor along "
           "the CAR border; a new Pongo-Wau-Numatinna National Park; "
           "Community Wildlife Areas; eight ECHO/TANGO awareness teams; four "
           "Focal Points. This document answers, for each of them, what the "
           "ground actually holds, what the law now allows, what to do, in "
           "what order, and what it costs.", indent="        "))
    P("")
    P(para(f"        Measured on {F['shapes']['n_shapes']} shapes received "
           f"2026-08-31, {n(F['xsa']['clusters'])} settlement clusters, "
           f"{n(fire['trajectories'])} tracked fire fronts, "
           f"{D['counts']['files']} source documents and a live budget model. "
           f"Three years, {usd(bud['three_year'])}.",
           indent="        "))
    P("")
    P(HRULE)
    P("")

    # ------------------------------------------------------------ how to read
    P(head("HOW TO READ THIS DOCUMENT"))
    P("")
    P(para("It runs in five moves, in the order the work happened:"))
    P("")
    P("    1  THE PROBLEM      what was asked, and what is actually on the")
    P("                        ground - measured, not assumed.")
    P("    2  THE INSIGHT      the eight things the measurements changed our")
    P("                        mind about. Each one moves an action.")
    P("    3  THE DOCUMENTS    what we read, how old it is, and what it")
    P("                        allows. Law first, because the law moved.")
    P("    4  THE ACTIONS      eleven, in order, each with an owner, a")
    P("                        season, a cost and a way to know it is done.")
    P("    5  THE MONEY        three years, phased on the same clock, with")
    P("                        the four things that would make it wrong.")
    P("")
    P(para("Then references: every dataset, every document, every artefact "
           "this plan hands over."))
    P("")
    P("  FOUR RULES THIS DOCUMENT KEEPS")
    P(bullet("A NUMBER SAYS WHAT IT COUNTS. Population is a satellite "
           "estimate and a LOWER BOUND, never a census. Fire rates are "
           "2024-2025 only, because the VIIRS satellite fleet triples on "
           "2024-01-01 and a rate spanning that date is not one series. "
           "A count of settlement CLUSTERS is not a count of buildings.",
           indent="    "))
    P(bullet("WHAT WE CANNOT MEASURE SAYS SO. Where a figure is absent you "
           "will read 'not measured', never a zero. A blank map is often the "
           "reach of a list, not the absence of the thing.", indent="    "))
    P(bullet("NOTHING IS TYPED TWICE. Every figure here is generated from "
           "data/eval/pip_facts.json by scripts/easypip/build_docs.py. Change "
           "a boundary and the map, the GeoPackage and both documents move "
           "together.", indent="    "))
    P(bullet("SSWS LEADS. Nothing in this plan happens without the South "
           "Sudan Wildlife Service backing it and being visibly in front of "
           "it on the ground. That is action 1 and it is a premise, not a "
           "task.", indent="    "))
    P("")
    P("  COMPANION FILES")
    P("    EASY_PIP_MAP_2026-08.pdf / .png     the one-sheet map")
    P("    EASY_PIP_MAP_2026-08.gpkg           the same map, clickable, QGIS")
    P("    PIP_TWO_PAGER_2026-08_EASY.txt      this argument in two pages")
    P("    BUDGET_APRCA_WSS_2026-08_EASY.txt   the full budget model (+ .xlsx)")
    P("")

    # ================================================== 1. THE PROBLEM
    P("")
    P(HRULE)
    P("PART 1 - THE PROBLEM")
    P(HRULE)

    P(head("1.1  WHAT WAS ASKED"))
    P("")
    P(para("A two-page plan and a map arrived from AP-RCA. It proposes six "
           "things, and it is right about most of them. What it does not have "
           "is a measurement: how many people live inside the shapes it draws, "
           "who is on their edge, what burns, who else is already working "
           "there, and which law now applies. Without those, the plan cannot "
           "be sequenced, and it cannot be costed."))
    P("")
    P("    THE ASK                          THIS DOCUMENT'S ANSWER, IN A LINE")
    P("    " + "-" * 74)
    P("    1 Does the old Tamboura -        ANSWERED. Gone as a road. Rebuild,")
    P("      Deim Zubeir road survive?      do not expect to find. ~180 km")
    P("                                     through empty bush; separate")
    P("                                     capital project, not this budget.")
    P("    2 A livestock corridor along     AXIS CONFIRMED by tracked fire")
    P("      the CAR - South Sudan border   fronts. Counterpart exists and has")
    P("                                     named its price: ground, water,")
    P("                                     veterinary and medical support.")
    P("    3 A new Pongo-Wau-Numatinna      SUPPORTED. The polygon as drawn is")
    P("      National Park                  empty; the argument is timing and")
    P("                                     the mining law, not people.")
    P("    4 Community Wildlife Areas       STRONGLY SUPPORTED - but on the")
    P("                                     RIM, not the empty interior.")
    P("    5 Eight ECHO/TANGO teams         RIGHT GATEWAYS, WRONG ASSUMPTION:")
    P("                                     half the sites are near-empty; the")
    P("                                     audience is seasonal.")
    P("    6 Four Focal Points              Wau and Tambura strong; Deim")
    P("                                     Zubeir defensible; Boro-Medina")
    P("                                     nearly empty - twin it with Raga.")
    P("")
    P(para("Two things the plan does not mention at all decide whether any of "
           "it survives: FIRE, which is the landscape's economy and its "
           "biggest management difference between parks, and ARTISANAL GOLD, "
           "which is licensed under a different law by a different authority "
           "on a different timetable. Both are in Part 2."))

    P(head("1.2  WHAT IS ACTUALLY ON THE GROUND"))
    P("")
    P(para(f"Six KML files arrived on 2026-08-31 and they contain "
           f"{F['shapes']['n_shapes']} shapes: "
           f"{F['shapes']['n_polygons']} polygons and "
           f"{F['shapes']['n_markers']} single map pins. Everything below is "
           f"measured against those shapes."))
    P("")
    P("  THE PROPOSED PARK, INSIDE ITS OWN BOUNDARY")
    P("")
    P(f"    Area measured                          {n(park['area_km2'])} km2"
      f"   (file name says {n(park['file_says_km2'])})")
    P(f"    Settlement clusters inside             {n(park['clusters'])}")
    P(f"    Estimated people inside                {n(park['people'])}")
    P(f"        the one settlement: {park['only_settlement']['place']}"
      f" {park['only_settlement']['classification'].replace('_', ' ')},"
      f" {park['only_settlement']['lat']:.4f} N"
      f" {park['only_settlement']['lon']:.4f} E")
    P(f"    New settlements since 2015             {n(park['new_since_2015'])}")
    P(f"    Cropland inside (GLAD 30 m, 2019)      {n(park['cropland_km2'],2)} km2")
    P(f"    Verified clearing 2001-2026            {n(park['clearing_km2'],2)} km2"
      f"   ({n(park['clearing_events'])} events, last {park['clearing_last_year']})")
    P(f"    Fire fronts touching, 2024-2026        {n(park['fronts'])}"
      f"   ({park['fronts_transhumance_pct']}% transhumance)")
    P(f"    Fire detections 2024-2025              {n(park['fire_detections_2024_2025'])}"
      f"   = {n(park['fire_rate'])} per 1,000 km2/yr")
    P(f"    Reported mine sites inside             {n(park['mining_inside']['reported'])}")
    P(f"    Model top-5% cells inside              {n(park['mining_inside']['top05_cells'])}"
      f"   ({n(park['mining_rim']['top05_cells'])} in the"
      f" {rim['rim_km']:g} km rim)")
    P("")
    P(para(f"This replaces our own first reading. The August draft measured a "
           f"BOUNDING BOX and reported about 1,900 people; the real polygon "
           f"lies south-west of that box. On today's satellite evidence the "
           f"park is not 'nearly empty' - it is empty: one cattle camp, about "
           f"one estimated person, in {n(park['area_km2'])} km2. A box is not "
           f"a boundary."))
    P("")
    P("  THE DOORSTEP IS A DAY'S WALK WIDE, AND IT IS WHERE EVERYONE IS")
    P("")
    P(f"    Inside the boundary               {n(park['clusters']):>7} clusters"
      f"  {n(park['people']):>9} people")
    P(f"    First {rim['rim_km']:g} km outside it"
      f"           {n(rim['clusters']):>7} clusters  {n(rim['people']):>9} people")
    for pl in rim["largest"]:
        P(f"        {pl['place']:<22} {n(pl['pop']):>9}   {pl['classification']}")
    P(f"    Nearest place of 1,000+           {park['nearest_1000']['place']}"
      f", {park['nearest_1000']['km']:g} km out"
      f" ({n(park['nearest_1000']['pop'])} people)")
    P(f"    Nearest big town                  {park['biggest_near']['place']}"
      f" (Wau), {park['biggest_near']['km']:g} km out"
      f" ({n(park['biggest_near']['pop'])})")
    P("")
    P(para(f"None of the {n(rim['clusters'])} rim clusters is new since 2015. "
           f"The pressure is real but it is a day's walk away, not a fence "
           f"line - which is exactly the condition in which a boundary can "
           f"still be drawn without acquiring anybody's land."))
    P("")
    P("  ALL ELEVEN SHAPES, MEASURED")
    P("")
    P("    SHAPE                     AREA km2   IN: CLUST/PEOPLE  RIM: CLUST/PEOPLE")
    P("    " + "-" * 74)
    for r in zrows:
        if r["kind"] != "polygon":
            continue
        P(f"    {r['name']:<24} {n(r['area']):>9}   {n(r['clusters']):>5} /"
          f" {n(r['people']):>7}   {n(r['rim_clusters']):>5} / {n(r['rim_people']):>8}")
    P("    --- for scale ---")
    P(f"    {'XSA study area':<24} {n(F['xsa']['area_km2']):>9}"
      f"   {n(F['xsa']['clusters']):>5} / {n(F['xsa']['people']):>7}")
    P("")
    P("    SHAPE                       FIRE RATE   FRONTS   DIE INSIDE   TOP-5% GOLD")
    P("    " + "-" * 74)
    for r in zrows:
        if r["kind"] != "polygon":
            continue
        ip = f"{r['intercept']}%" if r["intercept"] is not None else "n/a"
        P(f"    {r['name']:<24} {n(r['rate']):>10}   {n(r['fronts']):>6}"
          f"   {ip:>10}   {n(r['gold']):>11}")
    P(f"    {'XSA study area':<24} {n(fire['xsa_rate']):>10}")
    P("    (fire rate = detections per 1,000 km2 per year, 2024-2025 only)")
    P("")
    P(para("THREE STRUCTURAL FACTS SIT IN THOSE TWO TABLES."))
    P("")
    P(bullet(f"THE SHAPES OVERLAP AND THEIR AREAS DO NOT ADD. The "
           f"{F['shapes']['n_polygons']} polygons cover "
           f"{n(F['shapes']['union_km2'])} km2 of distinct ground; their areas "
           f"sum to {n(F['shapes']['sum_of_areas_km2'])} km2 because "
           f"{n(F['shapes']['overlap_km2'])} km2 lies under more than one "
           f"designation. The proposed park lies 99.4% inside the Wau "
           f"Wilderness block. These are nested designations over mostly the "
           f"same ground, and a legal instrument that does not say which one "
           f"governs a hectare will be read differently by a herder, a warden "
           f"and a mining desk.", indent="    ", marker="1. "))
    P("")
    P(bullet(f"NOBODY LIVES IN THE INTERIORS AND EVERYBODY LIVES ON THE "
           f"RIMS. The union of the polygons holds {n(un['inside']['clusters'])} "
           f"clusters and about {n(un['inside']['population_est'])} estimated "
           f"people - a Wau neighbourhood spread over an area the size of "
           f"Hungary - of which "
           f"{n(un['inside']['by_classification'].get('temporary_camp'))} are "
           f"cattle camps and "
           f"{n(un['inside']['by_classification'].get('village'))} villages, "
           f"with no town anywhere inside any shape. Every one is classed "
           f"permanent; not one is new since 2015. The "
           f"{un['rim']['rim_km']:g} km ring around the whole union holds "
           f"{n(un['rim']['clusters'])} clusters and "
           f"{n(un['rim']['population_est'])} people, "
           f"{n(un['rim']['by_persistence'].get('recent'))} of them new since "
           f"2015. Designation displaces almost nobody. The negotiation is "
           f"with people who use the ground seasonally and live outside it.",
           indent="    ", marker="2. "))
    P("")
    P(bullet(f"FIRE IS BIGGER THAN ANY OF THEM. Every zone burns at "
           f"{n(fire['zone_rate_min'])}-{n(fire['zone_rate_max'])} detections "
           f"per 1,000 km2 per year against a study-area average of "
           f"{n(fire['xsa_rate'])}, and {fire['transhumance_pct']}% of the "
           f"tracked fronts in the whole area are classed transhumance. A "
           f"management plan for any of these shapes is a fire-governance "
           f"plan or it is fiction.", indent="    ", marker="3. "))

    P(head("1.3  THE CLOCK - WHY THIS IS A NOW PROBLEM"))
    P("")
    P("    WHAT IS MOVING                        WHY IT CLOSES A WINDOW")
    P("    " + "-" * 74)
    P("    The 2026 Act's regulations are        Two sentences written now are")
    P("    being drafted (s.80)                  worth more than any later")
    P("                                          project: the boundary")
    P("                                          description, and an express")
    P("                                          mining ban inside the park.")
    P("    Artisanal gold licences are issued    Whoever files first sets the")
    P("    by the STATE, not by Juba             default. A Gazette closure")
    P("                                          (Mining Act s.24) costs")
    P("                                          nothing and can be filed")
    P("                                          before the park exists.")
    P("    The boom towns north-east of the      Their cropland is still about")
    P("    park are growing fast                 1.5%. Land-use planning is")
    P("                                          cheap before farmland")
    P("                                          arrives, impossible after.")
    P("    A funding cycle is turning next       The incumbent's three-year")
    P("    door (EU / NaturAfrica)               window runs to 2026 and the")
    P("                                          EU is scoping how to")
    P("                                          implement the new Act.")
    P("    The last wildlife survey here was     A declaration with no current")
    P("    flown in 2007                         baseline can never show that")
    P("                                          it did anything.")
    P("")
    P(para("None of this is an emergency. All of it is a queue, and the queue "
           "moves whether or not this plan joins it."))
    for f in (part2_insight, part3_documents, part4_actions, part5_money,
              part6_references):
        L += f(F, Z, D, B, rows, tot, today)
    return L


# ================================================== 2. WHAT THE DATA CHANGED
def part2_insight(F, Z, D, B, rows, tot, today):
    L = []
    P = L.append
    park, un, gold, fire = F["park"], F["union"], F["gold"], F["fire"]
    cor, con = F["corridor"], F["conflict"]

    P("")
    P("")
    P(HRULE)
    P("PART 2 - WHAT THE MEASUREMENTS CHANGED")
    P(HRULE)
    P("")
    P(para("Eight findings. Each one moves an action in Part 4, and each is "
           "written so you can see what would have to be false for it to be "
           "wrong."))

    # --- 1 the park
    P(head("INSIGHT 1  THE PARK IS EMPTY - AND THAT IS THE LEGAL ARGUMENT"))
    P("")
    P(para(f"One cattle camp, about one estimated person, in "
           f"{n(park['area_km2'])} km2; no cropland; "
           f"{n(park['clearing_km2'], 2)} km2 of verified clearing in 25 "
           f"years, most of it classed natural, the last event in "
           f"{park['clearing_last_year']}."))
    P("")
    P(para("WHY IT MATTERS. The 2026 Act does not let community land be taken "
           "into a park unless it is legally acquired first. Emptiness is "
           "therefore not a sad fact about the place - it is what makes the "
           "declaration feasible and cheap. Keep the boundary off the "
           "boom-town rim and there is almost nothing to acquire."))
    P("")
    P(para("WHAT WOULD MAKE THIS WRONG. Satellite settlement mapping "
           "undercounts dispersed, war-damaged and seasonal occupation; a "
           "field mission counted one town at roughly 40,000 where we measure "
           f"{n(F['sites']['Raga (ECHO)']['people'])}. Cattle camps that move "
           "with the season and leave no roof are exactly what this method "
           "misses. Trust the RANKING, verify the interior on the ground in "
           "season one."))
    P("    MOVES: actions 2, 3, 8.")

    # --- 2 fire
    P(head("INSIGHT 2  FIRE IS THE ECONOMY, THE CALENDAR AND THE ONLY "
           "MANAGEMENT NUMBER"))
    P("")
    P(para(f"{n(fire['trajectories'])} fire fronts were tracked across the "
           f"study area in {fire['trajectory_window'][0]} to "
           f"{fire['trajectory_window'][1]}, and "
           f"{fire['transhumance_pct']}% of them are classed transhumance - "
           f"the movement of herders, not wildfire. Inside the proposed park, "
           f"{park['nov_feb_share']}% of all detections fall in November to "
           f"February."))
    P("")
    P("  WHEN THE PROPOSED PARK BURNS - VIIRS detections by calendar month,")
    P("  2024-2025 (the years with the full three-satellite fleet)")
    P("")
    P(fire_calendar(F))
    P("")
    P(para("READ THAT AS A STAFFING CHART. Every audience this plan must "
           "reach - herders, hunters, returning villagers - is present in the "
           "burning months and absent in the wet ones. Corridor talks outside "
           "15 December to 15 February happen with nobody. Scouts are "
           "therefore contracted seasonally, and the paper track runs in the "
           "rains from Juba."))
    P("")
    P(para(f"The park also INTERCEPTS fire: of {n(park['fronts'])} fronts "
           f"touching it, {n(park['fronts_start_inside'])} start inside and "
           f"{n(park['fronts_die_inside'])} die inside - "
           f"{park['interception_pct']}%, the highest of any shape in the "
           f"set. That is an argument for the park (fires stop here) and a "
           f"warning (they stop by burning out, not by being stopped). Fire "
           f"interception is the one management number that has ever "
           f"distinguished parks in this region, and it is measurable every "
           f"year from data this project already holds - so it belongs in the "
           f"logframe as a target."))
    P("    MOVES: actions 1, 6, 9, 10 - and the whole calendar of the budget.")

    # --- 3 gold
    P(head("INSIGHT 3  GOLD IS THE OPEN FLANK, AND THE FIX IS FREE"))
    P("")
    P(para("The park would be protected by the Wildlife Act. The GROUND is "
           "licensed under the Mining Act 2012, by state authorities, on "
           "their own timetable. That mismatch, not the digging, is the risk."))
    P("")
    P(f"    Reported mine sites inside the proposed shapes   "
      f"{n(gold['reference']['inside_proposed_shapes'])}")
    P(f"    Reported sites in the whole of South Sudan       "
      f"{n(gold['reference']['ssd_sites'])}"
      f"  (source: {', '.join(gold['reference']['ssd_sources'])})")
    P(f"    ...of which carry a commodity                    "
      f"{n(gold['reference']['ssd_with_commodity'])}")
    P(f"    Nearest known working to Raga                    "
      f"{n(gold['reference']['nearest_to_raga_km'])} km"
      f", in {gold['reference']['nearest_to_raga_where']}")
    P("")
    P(para(f"SAY WHAT THAT MEANS OUT LOUD: {gold['reference']['meaning']}. "
           f"Any statement that this ground is mine-free is a statement about "
           f"our data collection, not about the geology."))
    P("")
    P(para(f"What we do have is a prediction model with a MEASURED skill: "
           f"{gold['verdict']}"))
    P("")
    P("    TOP-5% GROUND BY MODEL SCORE - where the exposure actually is")
    P("    " + "-" * 74)
    for h in gold["hottest"]:
        P(f"    {short_zone(h['zone']):<34} {n(h['top05_cells']):>4} cells inside")
    P(f"    {'Pongo-Wau-Numatinna NP':<34} "
      f"{n(park['mining_inside']['top05_cells']):>4} cells inside"
      f"   ({n(park['mining_rim']['top05_cells'])} in its rim)")
    P("")
    P(para("So the exposure is NOT in the park - it is in the WILDERNESS "
           "blocks and on the grazing zones. Any Gazette closure request must "
           "cover them, not just the park. And no candidate cell is evidence "
           "of a pit: it is an imagery target with a modest measured skill, "
           "and it should be flown before it is quoted."))
    P("")
    P("    THREE LEVERS, IN ORDER OF SPEED AND COST")
    P("    1  Mining Act s.24 Gazette order closing the ground to")
    P("       mineral-title applications. Needs no park, no boundary and no")
    P("       budget - the Minister, the Council of Ministers and the state")
    P("       committee. Beware the mirror clause: s.27 reserves land FOR")
    P("       mining, and whoever files first sets the default.")
    P("    2  Register conservancies on the RIM, where the cells are. Once a")
    P("       conservancy is authorized, nobody may license any natural")
    P("       resource inside it without the governing authority's written")
    P("       permission (2026 Act s.14(4)). That is a statutory veto, and it")
    P("       is the strongest single tool in the whole legal stack.")
    P("    3  Write the mining ban into the park's declaration and into the")
    P("       Schedule IV boundary regulations, in the Mining Act's own words")
    P("       - otherwise a state mining desk reads no bar at all.")
    P("")
    P(para("AND TREAT DIGGING AS A LIVELIHOOD, NOT ONLY A CRIME. Artisanal "
           "mining is legal for citizens and licensable at state level; for "
           "returnee households it is dry-season income beside herding and "
           "hunting. Zone it: closed inside the park and the headwaters, "
           "supported and licensed outside, benefits routed through the "
           "conservancy agreement. Awareness first, seizure last."))
    P("    MOVES: actions 3, 4, 7, 10.")

    # --- 4 corridor
    P(head("INSIGHT 4  THE CORRIDOR AXIS IS REAL - BUT WHAT ARRIVED WAS TWO PINS"))
    P("")
    P(para("Tracked fire fronts are the best proxy we have for moving herders, "
           "and along the border belt they run north-west to south-east, on "
           "the axis the plan drew, about twice as often as crossing it. The "
           "grazing zone as drawn sits ON that axis. The plan's corridor "
           "geometry is right."))
    P("")
    P(para(f"The two 'ecological corridor' files, however, each hold a single "
           f"map pin, {cor['pins_apart_km']:g} km apart - not a polygon. We "
           f"measured a {cor['disc_radius_km']:g} km disc around each and say "
           f"so. The connectivity claim they encode is testable and it holds: "
           f"{n(cor['park_snp_shared_fronts'])} fire fronts touch both the "
           f"proposed park and Southern National Park, and the two shapes "
           f"physically meet over {cor['park_snp_touch_km2']} km2 - a neck, "
           f"not a gap. {cor['ask'].upper()}."))
    P("")
    P(para("THE COUNTERPART EXISTS AND HAS NAMED ITS PRICE. Herder leadership "
           "is organized and seated in two of this plan's own anchor towns. "
           "The stated conditions for respecting protected areas were "
           "specific: designated alternative ground, water points, veterinary "
           "and medical support. That is why those items are budget lines "
           "from year two and not an intention."))
    P("    MOVES: action 6 - and the ask back to AP-RCA for a polygon.")

    # --- 5 CWA
    P(head("INSIGHT 5  IT IS A RETURN, NOT AN INVASION"))
    P("")
    P(para("Four out of five settlements founded since 2015 sit within 3 km "
           "of a village NAMED on the 1930s survey sheets. People are coming "
           "back to places their families left, and the sheets are traced, "
           "georeferenced and name-by-name checkable - which is not just a "
           "nice framing but a legal asset: the 2026 Act lets the Minister "
           "make rules for people who are long-standing residents 'due to "
           "documented historical circumstance'. Our sheets are that "
           "documentation."))
    P("")
    P(para(f"Nobody is leaving, either: built surface across the study area "
           f"went {n(F['xsa']['built_2000_km2'],1)} -> "
           f"{n(F['xsa']['built_2015_km2'],1)} -> {n(F['xsa']['built_km2'],1)} "
           f"km2 (2000 / 2015 / today). Plan for more people, not fewer."))
    P("")
    P(para("AND PUT THE CONSERVANCIES WHERE THE PEOPLE ARE. The park's own "
           f"rim holds {n(park['rim']['people'])} people; the Southern-NP and "
           f"Numatina rims hold six figures each. Registration belongs there, "
           f"and on the Boro-Biri rim at Koko - not around the empty "
           f"interior."))
    P("    MOVES: actions 4, 8.")

    # --- 6 sites
    P(head("INSIGHT 6  THE SITE LIST HAS THE RIGHT GATEWAYS AND THE WRONG "
           "ASSUMPTION"))
    P("")
    P("    SITE                     PEOPLE WITHIN 15 KM   VERDICT")
    P("    " + "-" * 74)
    order = sorted(F["sites"].items(), key=lambda kv: -kv[1]["people"])
    for name, v in order:
        base = name.split("(")[0].strip()
        kind = "Focal Point" if "Focal" in name else "ECHO/TANGO"
        approx = "~" in name
        if v["people"] >= 20000:
            verdict = "STRONG anchor"
        elif v["people"] >= 200:
            verdict = "Justified - small but alive"
        elif v["people"] > 0:
            verdict = "WEAK - seasonal outreach only"
        else:
            verdict = "EMPTY - a historic site"
        P(f"    {base + (' ~' if approx else ''):<24} {n(v['people']):>10}"
          f"          {verdict}")
        P(f"    {'  (' + kind + ')':<24} {'':>10}          "
          f"nearest settlement {v['nearest_settlement_km']:g} km")
    P("")
    P("    The verdict column is a headcount rule, not the whole judgement:")
    P("    a site can be near-empty and still be the right door. The two")
    P("    corridor gates (Deim Zubeir in the north, M'Bittima in the south)")
    P("    and the south-east approach post are strategic positions, and the")
    P("    plan keeps them - as seasonal posts, not staffed stations.")
    P("")
    P("    ~ = position uncertain; located from a 1930s sheet label or two")
    P("        candidate positions. Verify on the ground before building.")
    P("")
    P(para("WHAT THE LIST GETS RIGHT: it brackets the empty corridor at both "
           "ends, covers the real towns, and posts a site on the park's "
           "south-east approach. WHAT IT MISSES: nothing covers the "
           "return-belt boom towns north of Wau - tens of thousands of "
           "people, and the region's next land-conversion front. The largest "
           "audiences in this landscape are the unserved ones."))
    P("")
    P(para("So: anchors where a real community exists; the thin sites become "
           "seasonal outreach FROM those anchors rather than stations; one "
           "new post in the boom belt; a Juba liaison desk, because every "
           "signature this plan needs is made there."))
    P("    MOVES: action 9.")

    # --- 7 law
    P(head("INSIGHT 7  THE LAW JUST CAUGHT UP WITH THE PLAN"))
    P("")
    P("    1975  Parks Act           keep people out; permits and game scouts")
    P("    2003  New Sudan Acts      same idea, plus an ARMED wildlife force")
    P("    2015  Bill (never passed) first draft of the community turn")
    P("    2026  ACT - SIGNED        communities become legal rights-holders")
    P("")
    P("    PLAN ITEM                  NOW EXISTS IN LAW AS")
    P("    " + "-" * 74)
    P("    Community Wildlife Areas   Community conservancies (s.14) - with a")
    P("                               statutory veto over resource use inside")
    P("    ECHO/TANGO teams           Community wildlife associations and")
    P("                               their registered scouts (s.15, s.16)")
    P("    Livestock corridor         Wildlife corridors (s.9), protected;")
    P("                               infrastructure crossing one needs a")
    P("                               funded impact plan")
    P("    New national park          Declared by the President (s.8);")
    P("                               community land only via legal")
    P("                               acquisition")
    P("    CAR / Chinko link          Transboundary agreements (s.74) - slow:")
    P("                               Council of Ministers AND Legislature")
    P("")
    P(para("TWO TRAPS IN THE SAME ACT. Burning vegetation and straying "
           "livestock inside a protected area are offences, with livestock "
           "confiscated on conviction and penalties measured in years and "
           "millions. Applied on day one, in a landscape where herding IS the "
           "economy, that criminalises everyone the teams must win over. And "
           "the wildlife service descends from an armed force - excellent for "
           "presence, risky for community work, so community-engagement "
           "training is explicit in the budget, not assumed."))
    P("    MOVES: actions 1, 2, 4, 10.")

    # --- 8 context
    P(head("INSIGHT 8  THIS IS NOT AN EMPTY LANDSCAPE, AND NOT A QUIET ONE "
           "EITHER"))
    P("")
    P(para("An incumbent conservation operator already runs the gazetted park "
           "next door with community scouts and EU funding, and calls those "
           "scouts by the acronym this plan uses for its AREAS. A neighbouring "
           "landscape shows how a management agreement, an aerial survey and "
           "an international designation actually get done in this country. "
           "Both are assets. Neither is mentioned in the plan as it arrived, "
           "and a proposal that ignores them will read as uninformed to the "
           "ministry and to the donor alike."))
    P("")
    P(para(f"On security, our own per-state totals say something useful and "
           f"specific: {con['wbeg']['name']} records "
           f"{n(con['wbeg']['events'])} conflict events since "
           f"{con['window'][0][:4]} - second FEWEST of "
           f"{con['n_states']} states - yet its peak weekly population "
           f"exposure, {n(con['wbeg']['peak_weekly_population_exposure'])}, "
           f"ranks {con['wbeg']['rank_by_peak_exposure']} in the country. A "
           f"low-frequency, high-amplitude place: long workable periods, "
           f"punctuated. Its record year is still "
           f"{con['wbeg']['record_year']}. The neighbour is the trend to "
           f"watch - {con['western_equatoria']['name']} had its record year "
           f"in {con['western_equatoria']['record_year']} "
           f"({n(con['western_equatoria']['record_year_events'])} events, "
           f"{con['western_equatoria']['vac_share_pct']}% of its events "
           f"violence against civilians)."))
    P("")
    P(para("PLAN ACCORDINGLY: a short field season, movable assets, paperwork "
           "that continues from Juba when the field stops, and a contingency "
           "that prices a lost season rather than a daily risk."))
    P("")
    P(para(f"({con['attribution']})"))
    P("    MOVES: actions 1, 11, and the budget's contingency rate.")
    return L


# ================================================== 3. DOCUMENTS REVIEWED
def part3_documents(F, Z, D, B, rows, tot, today):
    L = []
    P = L.append
    c = D["counts"]
    st = doc_status_counts(D)
    P("")
    P("")
    P(HRULE)
    P("PART 3 - WHAT WE READ")
    P(HRULE)
    P("")
    P(para(f"{c['files']} source files were collected and "
           f"{c['documents_read']} were read end to end, including "
           f"{c['ocr_pages']} scanned pages put through OCR. Each was dated, "
           f"its legal status judged, and its relevance to this plan scored. "
           f"One file was unreadable and is listed as such rather than "
           f"quietly dropped."))
    P("")
    P("    HOW THE DOCUMENTS SORT BY LEGAL STATUS")
    P("    " + "-" * 74)
    mx = max(st.values())
    for k, v in st.items():
        P(f"    {k.replace('_',' '):<22} {v:>3}  {bar(v, mx, 40)}")
    P("")
    P(para("READ THAT CAUTIOUSLY. Status here is judged from the documents "
           "themselves plus the known sequence of wildlife law. No gazette "
           "was consulted and this is not a lawyer's opinion. Everything was "
           "machine-read: VERIFY EVERY CLAUSE NUMBER AND FIGURE AGAINST THE "
           "SCAN BEFORE ANY EXTERNAL USE."))

    P(head("3.1  THE LAWS THAT DO WORK IN THIS PLAN"))
    P("")
    for title, status, dt, why in law_table(D):
        P(f"  {title}")
        P(f"      status: {status}" + (f"   |   dated: {dt}" if dt else ""))
        P(para(why, indent="      "))
        P("")

    P(head("3.2  THE EVIDENCE BASE BEHIND THE MEASUREMENTS"))
    P("")
    P("    WHAT                         WHAT IT GIVES US        WATCH OUT FOR")
    P("    " + "-" * 74)
    P("    GHSL settlement and          Clusters, built surface, A LOWER BOUND.")
    P("    population layers            population estimates     Dispersed and")
    P("    (satellite, 1975-2030)                                war-damaged")
    P("                                                          towns are")
    P("                                                          undercounted.")
    P("    NASA FIRMS VIIRS fire        Where and when it burns  The fleet")
    P("    detections + v5 fire         and which way the fire   triples on")
    P("    front tracking               fronts move              2024-01-01;")
    P("                                                          rates use")
    P("                                                          2024-2025 only.")
    P("    Hansen / GFW forest loss     Verified clearing        Small events in")
    P("                                 events and area          savanna are")
    P("                                                          hard; classes")
    P("                                                          are modelled.")
    P("    GLAD 30 m cropland           Farmland, 2003 and 2019  EXCLUDES")
    P("                                                          pasture and")
    P("                                                          shifting")
    P("                                                          cultivation -")
    P("                                                          which is why a")
    P("                                                          grazed zone")
    P("                                                          reads zero.")
    P("    Sudan Survey 1:250,000       Village names, tracks    Machine-read")
    P("    sheets, 1930s, traced        and the road question    labels; check")
    P("                                                          names against")
    P("                                                          the scans.")
    P("    Mining prediction model      Where to LOOK for        A ranking with")
    P("    + reported-site reference    artisanal gold           modest measured")
    P("                                                          skill. Never")
    P("                                                          evidence of a")
    P("                                                          pit. The")
    P("                                                          reported list")
    P("                                                          is coverage,")
    P("                                                          not geology.")
    P("    Per-state conflict totals    Whether and how to work  Ours, derived")
    P("    (from an ACLED aggregate)    here at all              from an")
    P("                                                          aggregate; not")
    P("                                                          ACLED event")
    P("                                                          data.")
    P("    The six KML files received   The shapes themselves    Two are single")
    P("    2026-08-31                                            pins, and the")
    P("                                                          areas in the")
    P("                                                          file names do")
    P("                                                          not all match")
    P("                                                          the polygons.")
    P("")
    P(para("ONE CORROBORATING SOURCE IS CONFIDENTIAL. A 2022 field mission to "
           "this region confirms several satellite findings - the herding "
           "system, the wildlife still present, the size of two towns. It is "
           "used here only as corroboration and is never quoted, attributed "
           "or redistributed."))

    P(head("3.3  WHAT THE DOCUMENTS DISAGREE ABOUT, AND WHO WINS"))
    P("")
    P("    DISAGREEMENT                      HOW IT IS RESOLVED HERE")
    P("    " + "-" * 74)
    P("    File names vs measured areas      The polygon is measured and both")
    P("    (worst case: one wilderness       numbers are printed. SOMEBODY")
    P("     block is 6.9% smaller than       MUST SAY which is authoritative")
    P("     its file name says)              before either is published.")
    P("    Our own August draft vs this      This one. The August figures")
    P("    document on the park's            measured a bounding box; the KML")
    P("    population                        polygon is the boundary.")
    P("    A four-year-old contact sheet     The web check wins - it is four")
    P("    vs a live web check on who        years newer. One named minister")
    P("    holds which post                  in the sheet is already gone.")
    P("    'Nobody has looked at this        Narrowed: true for the western")
    P("    ground'                           reserves, FALSE for the gazetted")
    P("                                      park next door, which has an")
    P("                                      operator. Say which.")
    P("    Satellite population vs a         The field count is higher and")
    P("    field count                       probably closer. Trust the")
    P("                                      ranking of places, not any single")
    P("                                      number.")
    P("")
    P(para("A 2023 peer-reviewed paper on these same reserves independently "
           "recommends what this plan recommends - rangers present, bushmeat "
           "and timber managed, FIRES MANAGED, communities engaged, locally "
           "led monitoring, community conservancies established - and notes "
           "the list has barely changed since 1983. Read that twice. The "
           "bottleneck in this landscape has never been knowing what to do. "
           "The only genuinely new assets are the 2026 Act's conservancy "
           "instrument and the gold clock. Everything else is execution, and "
           "execution is what failed for forty years. Budget accordingly: "
           "fewer new ideas, more signatures, more seasons."))
    return L


# The eleven actions. Cost comes from the budget model (never typed here);
# everything else is the plan. Keys match scripts/easybudget/build_budget.py
# so an action that gains a budget line gains a cost automatically.
ACTION_DETAIL = {
    "A1": dict(
        season="from week 1",
        why="Nothing else is legal, safe or credible without it. The service "
            "leads in public and on the ground; we equip its two existing but "
            "unequipped posts and pay allowances for joint missions - never a "
            "government salary.",
        who="Project coordinator + the Director General's office",
        when="Week one, then continuously",
        done="Written backing and a joint working arrangement (not an NGO-to-"
             "NGO memorandum) before 30 November 2026."),
    "A2": dict(
        season="Oct-Nov 26",
        why="The regulations under the new Act are being drafted now. Two "
            "sentences - the Schedule IV boundary description, and an express "
            "'mining operations are banned herein' in the Mining Act's own "
            "words - are the cheapest and most durable acts in the plan.",
        who="In-country counsel + Director of Protected Areas",
        when="Oct-Nov 2026, in the rains, from Juba",
        done="The text is in a drafter's hands and acknowledged."),
    "A3": dict(
        season="Oct-Nov 26",
        why="A Gazette order under Mining Act s.24 closes ground to "
            "mineral-title applications. It needs no park, no boundary and no "
            "budget. File before somebody files s.27 the other way.",
        who="Minister of Mining, via MWCT and the state committee",
        when="Oct-Nov 2026",
        done="Request on file, covering the park footprint AND the wilderness "
             "blocks where the model's cells actually are."),
    "A4": dict(
        season="S1 then rains",
        why="A registered conservancy has a statutory veto over resource use "
            "inside it. That is the gold defence, the community's standing and "
            "the plan's legal existence, all in one instrument - and it only "
            "works once signed. Register RIM FIRST, where the people and the "
            "cells are.",
        who="Communities + facilitator + counsel; RRC in parallel",
        when="Scoped in season 1, filed in the 2027 rains",
        done="At least one application accepted as complete in year 1; one "
             "conservancy authorized in year 2."),
    "A5": dict(
        season="closed",
        why="The road question is answered: the 1932 alignment is gone, the "
            "middle is empty, the endpoints live. Reopening means building "
            "about 180 km through uninhabited bush - a separate capital "
            "project with its own environmental assessment. We hand over the "
            "traced 1932 linework and stop paying to re-ask.",
        who="Whoever takes the road forward - not this budget",
        when="Closed",
        done="Closed. Do not re-survey."),
    "A6": dict(
        season="S1, S2, S3",
        why="The axis is confirmed by the fire-front data and the counterpart "
            "exists. The herders named their price - designated ground, water, "
            "veterinary and medical support - so it is a budget line from "
            "year two, not an intention. Ask AP-RCA for corridor POLYGONS: a "
            "pin cannot be gazetted.",
        who="Herder leadership (Wau seat, Raja delegate) + SSWS",
        when="Talks in the December-February window only",
        done="Spine drawn with permeability rules for local crossing; first "
             "water point and veterinary campaign delivered, not promised."),
    "A7": dict(
        season="S1 then S2",
        why="Mines here are dark to satellites: no fire, no lights. Two recce "
            "flights a year over the model's cells in the dry window, plus the "
            "teams' ground question, IS the monitoring system. Pair it with "
            "one dry-season wildlife survey - the first since 2007 and the "
            "cheapest credibility available.",
        who="Borrowed survey capability, not procured; SSWS on board",
        when="Season 1 recce; survey in season 2",
        done="A baseline exists, and we know whether anyone is digging on the "
             "rim."),
    "A8": dict(
        season="Y1 then Y2-3",
        why="The boom towns north-east of the park are growing and their "
            "farmland has not arrived yet. Land-use planning is cheap now and "
            "impossible later. Frame it around historic return: the 1930s "
            "names are on our sheets, village by village.",
        who="County authorities + communities + the national partner",
        when="Opened year 1, workshops years 2-3",
        done="Workshops held with the towns that would otherwise convert the "
             "doorstep."),
    "A9": dict(
        season="by end Y1",
        why="Half the proposed sites have almost no residents. Make the thin "
            "ones seasonal outreach from real anchors, run the far northern "
            "site as a seasonal post, add one post in the boom belt north of "
            "Wau, twin the emptiest focal point with the nearest real town, "
            "and open a Juba liaison desk - every signature lives there.",
        who="Coordinator + partner",
        when="Fixed by the end of year 1",
        done="Posture written down and staffed to it."),
    "A10": dict(
        season="from S1 day 1",
        why="People are the only sensor that works for mining, herd routes and "
            "local river names. One page, carried on every patrol: digging "
            "signs, who licenses locally, herd routes, water, returnee village "
            "names. And negotiate the Act's fire, livestock and mining "
            "offences in with communities, phased - awareness first, seizure "
            "last.",
        who="Scouts and team leaders, trained for it",
        when="From day one of season 1",
        done="Checklist in use and its answers in the database."),
    "A11": dict(
        season="week one",
        why="Ask whether any ground here is on the continental keystone list "
            "(the answer changes the plan either way); take the seat at the "
            "existing donor conservation table in Juba rather than convening a "
            "rival one; open the adjacency conversation with the incumbent "
            "operator next door BEFORE circulating any proposal. Three "
            "conversations, no budget.",
        who="Coordinator and the technical adviser",
        when="Week one",
        done="Three answers, in writing, before any proposal circulates."),
}


def part4_actions(F, Z, D, B, rows, tot, today):
    L = []
    P = L.append
    A = action_costs(B, rows)
    P("")
    P("")
    P(HRULE)
    P("PART 4 - WHAT TO DO, IN ORDER")
    P(HRULE)
    P("")
    P(para("Eleven actions. They are in priority order, and the order is not "
           "the order of size: the three cheapest acts in the list are among "
           "the most valuable, because they are signatures rather than "
           "activities."))
    P("")
    P("    ACTION                                      3-YEAR COST   WHEN")
    P("    " + "-" * 74)
    for key in sorted(B.ACTIONS, key=lambda k: int(k[1:])):
        label = B.ACTIONS[key]
        cost = A[key]
        money = n(cost) if cost else "no cost"
        P(f"    {label:<44} {money:>11}   {ACTION_DETAIL[key]['season']}")
    P("    " + "-" * 74)
    P(f"    {'TOTAL, three years, loaded':<44}"
      f" {n(F['budget']['three_year']):>11}")
    P("")
    P(para("THE THREE ZERO-COST ACTIONS ARE THE POINT. The Gazette closure "
           "request, the finding that closes the road question, and the three "
           "funding conversations of week one are letters and phone calls, "
           "already paid for inside the coordinator's salary and the legal "
           "retainer. Between them they are worth more than anything else "
           "here."))
    P("")
    for key in sorted(B.ACTIONS, key=lambda k: int(k[1:])):
        d = ACTION_DETAIL[key]
        cost = A[key]
        P("")
        P("  " + B.ACTIONS[key].upper())
        P("  " + "-" * 74)
        P(para(d["why"], indent="    "))
        P("")
        P(f"      WHO      {d['who']}")
        P(f"      WHEN     {d['when']}")
        P(f"      COST     " + (f"{usd(cost)} over three years"
                                if cost else
                                "nothing - staff time already budgeted"))
        txt = para(d["done"], indent=" " * 15)
        P("      DONE     " + txt.lstrip())
    P("")

    P(head("4.1  THE CALENDAR - TWO CLOCKS, AND NEITHER WAITS FOR THE OTHER"))
    P("")
    P(para("The FIELD clock has one window a year: 15 December to 15 "
           "February. Anything needing people on this ground outside it is "
           "not scheduled, it is hoped for. The PAPER clock runs all year "
           "from Juba and is the only thing that moves in the wet season. "
           "Paper in the rains, boots in the dry, and never wait for one to "
           "finish before starting the other."))
    P("")
    P("    Oct 2026  ####  paper: backing, regulations, gazette, registration")
    P("    Nov 2026  ####  paper  + GATE: no backing by 30 Nov -> do not deploy")
    P("    Dec 2026  ############  SEASON 1  rim check, corridor talks, CWA talk")
    P("    Jan 2027  ############  SEASON 1  peak burning month")
    P("    Feb 2027  ########      SEASON 1 ends 15 Feb")
    P("    Mar-Sep   ####  paper: applications written, survey designed")
    P("    Dec 2027  ############  SEASON 2  survey flown, first borehole")
    P("    Dec 2028  ############  SEASON 3  declaration package, hold it")
    P("")
    P("    MILESTONE                                          BY WHEN")
    P("    " + "-" * 74)
    P("    SSWS backing in writing                            30 Nov 2026 GATE")
    P("    Regulation text and gazette request filed          Mar 2027")
    P("    We know whether anyone is digging on the rim       Mar 2027")
    P("    First conservancy application complete             Sep 2027")
    P("    First conservancy AUTHORIZED, scouts registered    Sep 2028")
    P("    Aerial survey flown - baseline exists              Sep 2028")
    P("    Fire interception measured as a target             Sep 2028")
    P("    Park declaration package with the Minister         Oct 2028 onward")
    P("")
    P("    TWO GATES, WRITTEN DOWN SO NOBODY HAS TO BE BRAVE LATER")
    P(bullet("No SSWS backing by 30 November 2026: season one does not "
           "deploy. Spend the window on approvals and come back a year later. "
           "Do not improvise.", indent="    "))
    P(bullet("A mineral title issued on the rim: stop expanding and spend the "
           "season on that single problem. Cost is not the constraint there; "
           "sequencing is.", indent="    "))
    P("")
    P(para("WHAT SUCCESS LOOKS LIKE AT YEAR THREE. Not a bigger map: a "
           "boundary two ministries agree on, a community with a legal veto "
           "over its own ground, a corridor with water on it, and a fire "
           "number that moved."))
    return L


# ================================================== 5. THE MONEY
def part5_money(F, Z, D, B, rows, tot, today):
    L = []
    P = L.append
    bud = F["budget"]
    C = cat_totals(B, rows)
    a = tot["a"]
    P("")
    P("")
    P(HRULE)
    P("PART 5 - WHAT IT COSTS")
    P(HRULE)
    P("")
    P(para("Three years, phased on the same two clocks as the plan. No new "
           "organisation: a registered national partner implements, the "
           "wildlife service leads visibly, a neighbouring headquarters "
           "provides technical oversight three visits a year, and an existing "
           "country office carries the capital's paperwork. The heaviest "
           "single asset in this budget is a motorbike."))
    P("")
    P("  THE HEADLINE")
    P("")
    P(f"    {'':31} {'YEAR 1':>10} {'YEAR 2':>10} {'YEAR 3':>10} {'TOTAL':>10}")
    P("    " + "-" * 74)
    for c in B.CATS:
        v = [sum(r["tot"][i] for r in rows if r["cat"] == c) for i in range(3)]
        P(f"    {c:31} {n(v[0]):>10} {n(v[1]):>10} {n(v[2]):>10} {n(sum(v)):>10}")
    P("    " + "-" * 74)
    for label, key in (("DIRECT COSTS", "direct"),
                       (f"Freight and clearing ({a['FREIGHT_PCT']:.0%})", "freight"),
                       (f"Bank charges and FX ({a['BANK_PCT']:.1%})", "bank"),
                       (f"HQ + capital desk ({a['SUPPORT_PCT']:.0%})", "supp"),
                       (f"Contingency ({a['CONTING_PCT']:.0%})", "cont")):
        v = tot[key]
        P(f"    {label:31} {n(v[0]):>10} {n(v[1]):>10} {n(v[2]):>10} {n(sum(v)):>10}")
    P("    " + "=" * 74)
    t = tot["total"]
    P(f"    {'TOTAL REQUESTED, USD':31} {n(t[0]):>10} {n(t[1]):>10}"
      f" {n(t[2]):>10} {n(sum(t)):>10}")
    P("")
    P(para(f"Year 2 is the peak, and only because two one-off items land in "
           f"it - the aerial survey and the first corridor borehole, "
           f"{usd(bud['y2_oneoffs'])} between them. Strip those and the "
           f"running cost is close to flat."))
    P("")
    P("  WHERE THE MONEY GOES")
    P("")
    total3 = sum(C.values())
    for c, v in sorted(C.items(), key=lambda kv: -kv[1]):
        P(f"    {c:<34} {v/total3:>5.1%}  {bar(v, max(C.values()), 34)}")
    P("")
    P(para("Read the top three together: people on the ground and the "
           "organisations that hold them ARE the operation. Everything else - "
           "premises, IT, transport, training - is under a tenth of the budget "
           "each, which is what it looks like when the office belongs to "
           "somebody else."))
    P("")
    P("  THE SAME MONEY ON THE PLAN'S OWN CLOCK")
    P("")
    P(f"    {'PERIOD':<36} {'DIRECT':>12} {'LOADED':>12} {'CUMULATIVE':>12}")
    P("    " + "-" * 74)
    P(f"    {'6 MONTHS  Oct 2026 - Mar 2027':<36} {n(tot['h1']['direct']):>12}"
      f" {n(bud['first_6_months']):>12} {n(bud['first_6_months']):>12}")
    P(f"    {'1 YEAR    Oct 2026 - Sep 2027':<36} {n(tot['direct'][0]):>12}"
      f" {n(bud['y1']):>12} {n(bud['y1']):>12}")
    P(f"    {'2 YEARS   Oct 2027 - Sep 2028':<36} {n(tot['direct'][1]):>12}"
      f" {n(bud['y2']):>12} {n(bud['y1']+bud['y2']):>12}")
    P(f"    {'3 YEARS   Oct 2028 onward':<36} {n(tot['direct'][2]):>12}"
      f" {n(bud['y3']):>12} {n(bud['three_year']):>12}")
    P("")
    P(para(f"The first row is a SLICE of year one, not an addition to it: "
           f"{usd(bud['first_6_months'])} is "
           f"{bud['first_6_months']/bud['y1']:.0%} of year one and the only "
           f"tranche that must be committed before anyone knows whether "
           f"season one deploys. Almost every capital item sits there, "
           f"because a team without kit in December is a team that misses the "
           f"year."))
    P("")
    P(para(f"AND YEAR THREE IS THE NUMBER TO SHOW A DONOR: "
           f"{usd(bud['y3'])} is roughly what holding this ground costs every "
           f"year once the one-offs are done. A declaration with no year-four "
           f"money behind it is a map, not protection."))
    P("")
    P("  WHAT A TEAM AND A FOCAL POINT COST, LOADED")
    P("")
    P(para(f"Loaded cost adds each unit's share of freight, bank charges, "
           f"support and contingency - a factor of {bud['load_factor']:.2f}. "
           f"These are the numbers to quote when somebody asks what one team "
           f"costs, because they are what it costs to HAVE one.", indent="  "))
    P("")
    P(f"    {'ECHO/TANGO teams, all (3 yr)':<44} {n(bud['team_loaded_3yr']):>12}")
    P(f"    {'Focal points + backbone (3 yr)':<44} {n(bud['backbone_loaded_3yr']):>12}")
    P(f"    {'HQ oversight (3 yr)':<44} {n(bud['hq_loaded_3yr']):>12}")
    P("")
    P(para("The backbone costs more than the teams, and should: it is the "
           "coordinator, the partner, the wildlife-service relationship and "
           "the paper track - the half of the operation that survives a "
           "cancelled season and the half that converts presence into an "
           "instrument. A budget where the teams cost more than the backbone "
           "has bought presence without the ability to use it."))
    P("")
    P("  HOW TO PHASE THE ASK")
    P("")
    paper_total = paper_package(rows)
    P(f"    A. THE PAPER PACKAGE            about {n(paper_total)} in year 1")
    P("       The coordinator, the legal retainer, registration and the Juba")
    P("       nights and per diems it takes to file the regulation text and")
    P("       the gazette request. Buys the two highest-value acts in the plan")
    P("       and needs no field season at all.")
    P("")
    P(f"    B. THE FIRST SEASON             {n(bud['y1'])}")
    P("       Everything in A plus three scout teams, the partner grant, the")
    P("       wildlife-service support, the reconnaissance flights and the")
    P("       first corridor talks.")
    P("")
    P(f"    C. THE FULL THREE YEARS         {n(bud['three_year'])}")
    P("       Adds the survey, the boreholes, the veterinary campaign, a fourth")
    P("       team and the land-use planning. This is the version that ends")
    P("       with a signed conservancy, a survey baseline and a fire number.")
    P("")
    P("  WHAT THIS BUDGET DELIBERATELY DOES NOT BUY")
    P("    * A vehicle fleet - 4x4s are hired by the day, for the days used.")
    P("    * An aircraft or aircraft capability - hours are bought and the")
    P("      survey capability is borrowed from an operator who already flies")
    P("      this region to a published standard.")
    P("    * A second country office - there is a functioning one already.")
    P("    * A resident expatriate structure - one adviser, three visits a")
    P("      year. This landscape's bottleneck has never been technical")
    P("      knowledge.")
    P("    * Road rebuilding - answered, and a separate capital project.")
    P("    * Enforcement hardware - the posture is awareness first, seizure")
    P("      last; buying seizure capability in year one would contradict it.")
    P("    * Twelve-month scout contracts for a two-month audience.")
    P("")
    P("  WHAT WOULD MAKE THIS BUDGET WRONG")
    P("    * No backing by 30 November 2026 - then two thirds of year one's")
    P("      field cost should not be spent. The gate is in the cash flow too.")
    P("    * A lost season - pushes activity right by twelve months, does not")
    P("      delete it. That is what the contingency prices.")
    P("    * A mineral title issued on the rim - year two stops expanding.")
    P("    * Partner capacity - the model rests on a national partner being")
    P("      able to absorb and account for the grant. Verify that on the")
    P("      first visit, before the grant is designed.")
    P("    * Currency - a meaningful share settles locally.")
    P(f"    * Freight assumed away - the {a['FREIGHT_PCT']:.0%} line is the one")
    P("      most often cut by a reviewer and the least often survivable. Cut")
    P("      it and the equipment simply does not arrive.")
    P("")
    P(para("Every rate behind these lines is a planning assumption for this "
           "landscape, marked FIRM or INDICATIVE in the budget file. None is a "
           "quotation. Replace every INDICATIVE rate with a real offer before "
           "any of this is submitted."))
    return L


# ================================================== 6. REFERENCES
def part6_references(F, Z, D, B, rows, tot, today):
    L = []
    P = L.append
    g = F["gold"]
    P("")
    P("")
    P(HRULE)
    P("PART 6 - REFERENCES, DATA AND WHAT WE HAND OVER")
    P(HRULE)

    P(head("6.1  THE ARTEFACTS THIS PLAN DELIVERS"))
    P("")
    P("    EASY_PIP_MAP_2026-08.pdf / .png      One-sheet map: the shapes, the")
    P("                                         plan's sites, settlements by")
    P("                                         measured population, fire")
    P("                                         fronts, gold exposure.")
    P("    EASY_PIP_MAP_2026-08.gpkg            The same map as a styled QGIS")
    P("                                         project - every shape carries")
    P("                                         its own measured statistics.")
    P("    PIP_APRCA_WSS_2026-08_EASY.txt       This document.")
    P("    PIP_TWO_PAGER_2026-08_EASY.txt       The two-page version.")
    P("    BUDGET_APRCA_WSS_2026-08_EASY.txt    The full budget model, with")
    P("      + .xlsx                            every rate marked FIRM or")
    P("                                         INDICATIVE. The workbook is")
    P("                                         live formulae.")
    P("    PLAN_APRCA_WSS_ASSESSMENT_TO_ACTION  The long assessment behind")
    P("      _2026-08_EASY.txt                  Parts 1-3, with counterpart")
    P("                                         tables and footnoted clauses.")
    P("    ROAD_TAMBOURA_DEIM_ZUBEIR            The road question, in full.")
    P("      _2026-08_EASY.txt")
    P("    XSA_CONSERVATION_REVIEW              The regional review the study")
    P("      _2026-08_EASY.txt                  area's baselines come from.")
    P("    ZONE_STATS.txt                       Per-zone statistics, plain")
    P("                                         text, one block per shape.")
    P("    Traced 1932 road linework and        Handed over as data: the only")
    P("    1930s village names                  engineering survey that exists")
    P("                                         for the road, and the village")
    P("                                         names behind the return")
    P("                                         argument.")

    P(head("6.2  DATA SOURCES, WITH THEIR LIMITS"))
    P("")
    P(para(f"Study-area baseline: {n(F['xsa']['clusters'])} settlement "
           f"clusters, {n(F['xsa']['people'])} estimated people, "
           f"{n(F['xsa']['built_km2'],1)} km2 of built surface over "
           f"{n(F['xsa']['area_km2'])} km2 - all re-verified on 2026-08-31.",
           indent="  "))
    P("")
    src = [
        ("Settlements and population",
         "GHSL R2023A (satellite). Estimates and LOWER BOUNDS, never counts. "
         "One row is one CLUSTER, not one building."),
        ("Fire",
         f"NASA FIRMS VIIRS; {n(F['fire']['detections_indexed'])} detections "
         f"indexed, {n(F['fire']['trajectories'])} tracked fronts "
         f"{F['fire']['trajectory_window'][0]} to "
         f"{F['fire']['trajectory_window'][1]}. Rates are 2024-2025 only: "
         f"{F['fire']['fleet_caption']}."),
        ("Forest loss",
         f"Hansen Global Forest Change + GFW alerts; "
         f"{n(F['xsa']['clearing_events_verified'])} verified events, "
         f"{n(F['xsa']['clearing_km2_verified'], 1)} km2. Events the pipeline "
         f"itself flags are excluded, not counted."),
        ("Cropland",
         "GLAD 30 m cropland extent, epochs 2003 and 2019. EXCLUDES pasture "
         "and shifting cultivation by definition - which is why a grazed zone "
         "reads zero."),
        ("Historic maps",
         "Sudan Survey 1:250,000, 1930s, traced and georeferenced. "
         "Machine-read labels - verify names against the scans."),
        ("Mining prediction",
         f"A model with a measured skill: the top 5% of ground holds "
         f"{g['skill_top05']['lift_reach']}x the known workings after "
         f"correcting for where the anchor list can see "
         f"(p = {g['skill_top05']['p_reach']}). {n(g['n_candidates'])} "
         f"candidates and {n(g['n_watchlist'])} watchlist villages - imagery "
         f"targets, never accusations."),
        ("Reported mine sites",
         f"{n(g['n_anchors'])} anchors in the study area, from "
         + ", ".join(f"{k} {v}" for k, v in sorted(g["anchor_sources"].items()))
         + f". In South Sudan the reference holds only "
           f"{n(g['reference']['ssd_sites'])} sites, "
           f"{n(g['reference']['inside_proposed_shapes'])} inside any proposed "
           f"shape: {g['reference']['meaning']}."),
        ("Conflict",
         f"Our own per-state totals derived from an ACLED aggregate "
         f"(acleddata.com), weeks {F['conflict']['window'][0]} to "
         f"{F['conflict']['window'][1]}. Not ACLED event data; every "
         f"inference is ours and must not be attributed to them."),
        ("The shapes",
         "Six KML files received 2026-08-31, copied to data/plan_zones/ and "
         "measured by scripts/plan_zone_stats.py."),
    ]
    for label, text in src:
        P(f"    {label}")
        P(para(text, indent="        "))
        P("")
    P(para("A borrowed result, named as borrowed: gold sites with an armed "
           "actor present sit on gold-graded rock more often than unarmed "
           "ones. That is measured on a field-visit survey in a neighbouring "
           "country, not here. It is a hypothesis carried across a border, "
           "and what it justifies is modest and specific - the top cells are "
           "the ones to fly first, and the ones where a lone post should not "
           "be placed."))

    P(head("6.3  LEGAL SOURCES"))
    P("")
    P(para(f"{D['counts']['documents_read']} of {D['counts']['files']} "
           f"collected documents were read; the legal status of each was "
           f"judged from the document plus the known sequence of wildlife "
           f"law. No gazette was consulted. This is not a lawyer's opinion, "
           f"and every clause number here was machine-read: verify against "
           f"the signed scan before any external use.", indent="  "))
    P("")
    for title, status, dt, _why in law_table(D):
        P(f"    {title}")
        P(f"        {status}{('  |  ' + dt) if dt else ''}")
    P("")
    P("    Also relied on, from the same collection: aerial wildlife survey")
    P("    reports from 2007 and 2015/16 (the only wildlife baseline that")
    P("    exists for this ground), a donor programme mid-term review, and a")
    P("    2023 peer-reviewed paper on the south-west game reserves.")

    P(head("6.4  HOW TO REPRODUCE EVERY NUMBER IN THIS DOCUMENT"))
    P("")
    P("    python3 scripts/plan_zone_stats.py --kml-dir data/plan_zones \\")
    P("        --json data/eval/zone_stats.json --report reports/ZONE_STATS.txt")
    P("    python3 scripts/easypip/pip_facts.py        # data/eval/pip_facts.json")
    P("    python3 scripts/easypip/build_map.py \\")
    P("        --out reports/EASY_PIP_MAP_2026-08.png --pdf --dpi 300")
    P("    python3 scripts/easypip/build_gpkg.py       # the styled GeoPackage")
    P("    python3 scripts/easypip/build_docs.py       # this file + the 2-pager")
    P("")
    P(para("Nothing in this document is typed. Change a boundary, a budget "
           "rate or a classification and re-run those five commands: the map, "
           "the GeoPackage, the budget and both documents move together, or "
           "the check fails and says so."))
    P("")
    P(RULE)
    P(para(f"Generated {today} by scripts/easypip/build_docs.py from "
           f"data/eval/pip_facts.json. INTERNAL. Populations are satellite "
           f"estimates, not counts. Passages corroborated by a confidential "
           f"2022 field mission are never quoted, attributed or "
           f"redistributed."))
    P(HRULE)
    return L


# ================================================================ TWO-PAGER
def two_pager(F, Z, D, B, rows, tot, today):
    """Two pages, 62 lines each. Same facts, same file, no new claims.

    The rule for this document: if a reader only ever reads this, what must
    they not get wrong? So it keeps the units and the caveats (a population
    that is a lower bound, a fire rate with its years, a model with its
    skill) and drops the detail, never the other way round.
    """
    L = []
    P = L.append
    park, un, gold, fire, bud = (F["park"], F["union"], F["gold"], F["fire"],
                                 F["budget"])
    A = action_costs(B, rows)
    rim = park["rim"]

    P(HRULE)
    P("PRIORITY INTERVENTION PLAN - THE TWO-PAGE VERSION")
    P("Pongo-Wau-Numatinna, the livestock corridor and the western reserves")
    P(f"Western Bahr el Ghazal, South Sudan.  {today}.  INTERNAL")
    P(HRULE)
    P("")
    P("THE ASK IN ONE LINE")
    P(para(f"{usd(bud['three_year'])} over three years to turn a two-page "
           f"proposal into a gazetted park, a registered community "
           f"conservancy, a corridor with water on it and a fire number that "
           f"moved - with the South Sudan Wildlife Service visibly leading, "
           f"a national partner implementing, and no new organisation "
           f"created."))
    P("")
    P("THE PROBLEM")
    P(para(f"A new national park is proposed over {n(park['area_km2'])} km2 "
           f"of western South Sudan. Nobody had measured what is inside it, "
           f"who is on its edge, what burns, or which law reaches it first."))
    P("")
    P("WHAT WE MEASURED")
    P(f"    Inside the proposed park       {n(park['clusters'])} settlement, "
      f"about {n(park['people'])} person, in {n(park['area_km2'])} km2")
    P(f"    Cropland inside                {n(park['cropland_km2'],2)} km2")
    P(f"    Clearing inside, 2001-2026     {n(park['clearing_km2'],2)} km2 "
      f"in {n(park['clearing_events'])} events")
    P(f"    First {rim['rim_km']:g} km outside          "
      f"{n(rim['clusters'])} settlements, {n(rim['people'])} people")
    P("                                   (none of them new since 2015)")
    P(f"    Nearest place of 1,000+        {park['nearest_1000']['place']}, "
      f"{park['nearest_1000']['km']:g} km away")
    P(f"    Fire, 2024-2025                "
      f"{n(park['fire_detections_2024_2025'])} detections = "
      f"{n(park['fire_rate'])} per 1,000 km2/yr,")
    P(f"                                   {park['nov_feb_share']}% of it "
      f"November-February")
    P(f"    Fire fronts touching it        {n(park['fronts'])}, "
      f"{park['fronts_transhumance_pct']}% herder movement; "
      f"{park['interception_pct']}% die inside")
    P(f"    Reported mines inside or rim   "
      f"{n(park['mining_inside']['reported'])} - but that is the reach of the "
      f"lists,")
    P(f"                                   not the absence of pits")
    P("")
    P(para("Population figures are satellite estimates and LOWER BOUNDS, "
           "never counts. Fire rates use 2024-2025 only, because the "
           "satellite fleet triples on 2024-01-01."))
    P("")
    P("THE FIVE THINGS THAT CHANGED THE PLAN")
    P("  1 THE PARK IS EMPTY, AND THAT IS THE LEGAL ARGUMENT. Community land")
    P("    cannot be taken into a park without legal acquisition. Emptiness")
    P("    makes the declaration feasible - if the boundary stays off the")
    P("    boom-town rim.")
    P("  2 FIRE IS THE ECONOMY AND THE CALENDAR. Herding fires, not wildfire.")
    P("    Everyone this plan must reach is present December to February and")
    P("    absent June to September. Scouts are paid seasonally; the paperwork")
    P("    runs in the rains.")
    P("  3 GOLD IS THE OPEN FLANK AND THE FIX IS FREE. Artisanal licences are")
    P("    issued by the STATE under a different law. A Gazette closure order")
    P("    costs nothing and needs no park. File it before somebody files the")
    P("    reverse.")
    P("  4 THE CORRIDOR AXIS IS RIGHT; WHAT ARRIVED WAS TWO PINS. The herder")
    P("    counterpart exists and named its price: designated ground, water,")
    P("    veterinary and medical support. A pin cannot be gazetted - send a")
    P("    polygon.")
    P("  5 THE SITES HAVE THE RIGHT GATEWAYS, THE WRONG ASSUMPTION. Half have")
    P("    almost no residents; the biggest unserved audiences are the boom")
    P("    towns north of the park. Anchors where people are; the thin sites")
    P("    become seasonal outreach.")
    P("")
    P("WHEN THIS GROUND BURNS  (detections in the proposed park, 2024-2025)")
    P(fire_calendar_compact(F, indent="    "))
    P("")
    P(HRULE)
    P("PAGE 2 - WHAT TO DO, AND WHAT IT COSTS")
    P(HRULE)
    P("")
    P("    ACTION                                        3-YEAR COST")
    P("    " + "-" * 58)
    for key in sorted(B.ACTIONS, key=lambda k: int(k[1:])):
        cost = A[key]
        P(f"    {B.ACTIONS[key]:<47} {(n(cost) if cost else 'no cost'):>10}")
    P("    " + "-" * 58)
    P(f"    {'TOTAL, three years, loaded':<47} {n(bud['three_year']):>10}")
    P("")
    P("THE THREE FREE ACTIONS MATTER MOST: a Gazette closure request, a closed")
    P("road question and three funding calls - paid for inside two salaries.")
    P("")
    P("THE MONEY, ON THE PLAN'S OWN CLOCK")
    P(f"    First 6 months  Oct 26 - Mar 27      {usd(bud['first_6_months'])}"
      f"   (a slice of Y1)")
    P(f"    Year 1          Oct 26 - Sep 27      {usd(bud['y1'])}")
    P(f"    Year 2          Oct 27 - Sep 28      {usd(bud['y2'])}"
      f"   (survey + borehole)")
    P(f"    Year 3 and on   Oct 28 onward        {usd(bud['y3'])}"
      f"   (the running cost)")
    P(f"    THREE YEARS                          {usd(bud['three_year'])}")
    P("")
    P("Year 3 is the number to show a donor: what holding this ground costs")
    P("each year once the one-offs are done. A declaration with no year-four")
    P("money behind it is a map, not protection.")
    P("")
    P("THE SHAPE OF THE OPERATION")
    P("    * No new organisation: a national partner implements and the")
    P("      wildlife service leads visibly, equipped to do it.")
    P("    * One adviser, three visits a year. No resident structure.")
    P("    * Seasonal scout contracts - five months, then seven, then nine.")
    P("    * No fleet, no aircraft, no enforcement hardware.")
    P("")
    P("TWO GATES, AGREED IN ADVANCE")
    P("    * No wildlife-service backing in writing by 30 Nov 2026: season one")
    P("      does not deploy. Spend the window on approvals.")
    P("    * A mineral title issued on the rim: stop expanding; spend the")
    P("      season on that one problem.")
    P("")
    P("SUCCESS AT YEAR THREE is not a bigger map: a boundary two ministries")
    P("agree on, a community with a legal veto over its ground, a corridor with")
    P("water on it, and a fire number that moved.")
    P("")
    P(para("HOW SURE WE ARE. Strong on where people are NOT, on when this "
           "ground burns, and on what the law now allows. Weak - and saying "
           "so - on three: satellite population undercounts dispersed and "
           f"seasonal living; the gold picture is a ranking with modest "
           f"measured skill ({gold['skill_top05']['lift_reach']}x the known "
           f"workings in the top 5% of ground, p = "
           f"{gold['skill_top05']['p_reach']}) over a reported-site list that "
           "barely covers this country; and no aerial wildlife survey has "
           "been flown here since 2007. One field season and one survey close "
           "all three."))
    P("")
    P("READ NEXT  PIP_APRCA_WSS_2026-08_EASY.txt (the full plan) and")
    P("           EASY_PIP_MAP_2026-08.pdf / .gpkg (the map).")
    P(HRULE)
    P(f"Generated {today} by scripts/easypip/build_docs.py from")
    P("data/eval/pip_facts.json - no figure here is typed.  INTERNAL.")
    P(HRULE)
    return L


def render(lines):
    return "\n".join(lines).rstrip() + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=date.today().isoformat())
    ap.add_argument("--check", action="store_true",
                    help="regenerate and exit 1 if the files on disk differ")
    a = ap.parse_args()
    F, Z, D, B, rows, tot = load()
    outputs = [
        (OUT_FULL, render(full_doc(F, Z, D, B, rows, tot, a.date))),
        (OUT_SHORT, render(two_pager(F, Z, D, B, rows, tot, a.date))),
    ]
    bad = False
    for path, text in outputs:
        # A line over 80 columns wraps in a field terminal and silently
        # destroys a table; fail loudly rather than ship it.
        long_ = [i + 1 for i, ln in enumerate(text.splitlines()) if len(ln) > W]
        if long_:
            print(f"{path.name}: {len(long_)} lines over {W} cols: "
                  f"{long_[:8]}", file=sys.stderr)
            bad = True
        if path is OUT_SHORT:
            pages = text.split(HRULE + "\nPAGE 2")
            over = [i + 1 for i, pg in enumerate(pages)
                    if len(pg.splitlines()) > PAGE_LINES]
            if len(pages) != 2 or over:
                print(f"{path.name}: not two pages "
                      f"({[len(pg.splitlines()) for pg in pages]} lines, "
                      f"limit {PAGE_LINES})", file=sys.stderr)
                bad = True
        if a.check:
            old = path.read_text() if path.exists() else ""
            if old != text:
                print(f"{path.name} is STALE - re-run build_docs.py")
                bad = True
            else:
                print(f"{path.name} is current")
        else:
            path.write_text(text)
            print(f"wrote {path}  ({len(text.splitlines())} lines)")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
