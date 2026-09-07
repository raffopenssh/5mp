#!/usr/bin/env python3
"""Build the EASY budget: facts + plan.yaml -> budget.xlsx (live formulae) + budget .txt.

Called by scripts/easyplan.py (do not run alone unless you pass --facts). The
QUANTITIES are derived: team counts per year come from the deployment
(data/plan_zones/solver/deploy.json via facts.json), calendar and per-team
ratios from docs/plan/plan.yaml. Nothing downstream is a typed number
(AGENTS.md invariant 2). Horizon = len(plan.year_labels).

Every rate is a stated PLANNING ASSUMPTION for this landscape (FIRM or
INDICATIVE); none is a quotation. Operating model: implementation through
local partners and the South Sudan Wildlife Service, oversight from Chinko HQ,
Juba handled by African Parks South Sudan's country representative.

Workbook: unit cost is a VLOOKUP into Rates, line totals are products,
subtotals are SUMIFs, support and contingency are percentage cells.
"""
import argparse
import datetime
import json
import textwrap

import openpyxl
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter, column_index_from_string

# --------------------------------------------------------------------------
# RATES: code, label, unit, USD, note (what the rate assumes; firmness)
# --------------------------------------------------------------------------
RATES = [
    # -- people ------------------------------------------------------------
    ("SAL_SCOUT", "Community wildlife scout (ECHO/TANGO), all-in", "person-month", 360,
     "Seasonal community scout on a national contract: basic pay plus statutory "
     "contributions and end-of-season bonus, excluding the food allowance which "
     "is budgeted separately. The AP SSD conservancy model pays 250 for a "
     "year-round Eco/Tango; a seasonal contract costs more per month. FIRM to about +/-15%"),
    ("SAL_LEAD", "Scout team leader, all-in", "person-month", 550,
     "One leader per four-person team, roughly half again a member's package. "
     "FIRM to about +/-15%"),
    ("SAL_OFFICER", "National project officer (field liaison, admin)", "person-month", 850,
     "Experienced national officer able to work with county authorities and the "
     "wildlife service unsupervised. INDICATIVE - set against offers received"),
    ("SAL_COORD", "National project coordinator, Wau", "person-month", 1400,
     "Senior national hire carrying a government-facing mandate; the single most "
     "important recruitment in the plan. INDICATIVE - expect to pay to keep"),
    ("SAL_INTL", "Technical adviser from Chinko HQ, all-in (excl. travel)", "person-month", 5500,
     "Charged only for months actually worked on this landscape: salary, "
     "insurance and medevac cover. Not a resident expatriate post. INDICATIVE"),
    ("SSWS_ALLOW", "SSWS staff field allowance, joint patrol/mission", "person-day", 25,
     "Allowance for wildlife-service staff on joint missions. It is an "
     "allowance and never a government salary - the distinction matters to both "
     "the ministry and the donor. FIRM"),
    ("RATION", "Field food allowance", "person-month", 155,
     "Cash food allowance per scout per month in the field, at prevailing "
     "market prices in the western towns (the AP SSD conservancy model uses 90 "
     "for a resident post). FIRM"),
    ("PERDIEM_F", "Per diem, field mission", "person-day", 15, "Field rate. FIRM"),
    ("PERDIEM_J", "Per diem, Juba/Wau", "person-day", 40, "Town rate. FIRM"),
    ("HEALTH_N", "National staff healthcare/medevac contribution", "person-month", 20,
     "Per capita contribution to treatment, referral and evacuation. FIRM"),
    ("FACILITATOR", "Conservancy-constitution facilitator (national consultant)", "day", 250,
     "National consultant day rate for facilitating a community constitution and "
     "collective-decision rules. INDICATIVE"),
    # -- movement ----------------------------------------------------------
    ("FLT_REG", "Regional return flight, Bangui - Juba (via Addis/Nairobi)", "flight", 1400,
     "There is no direct routing; the trip is two regional legs each way. "
     "Movement is CAR <-> South Sudan only - no European travel is budgeted. "
     "INDICATIVE, volatile with season"),
    ("FLT_DOM", "Domestic flight, one way (Juba - Wau/Tambura)", "flight", 440,
     "Scheduled domestic leg. INDICATIVE, volatile"),
    ("CHARTER", "Caravan charter, one rotation", "flight", 4200,
     "A single Caravan rotation out to the corridor with team and cargo. "
     "INDICATIVE - the cheapest way to reach ground the roads do not"),
    ("FLY_HR", "Light-aircraft flying hour (C182/C206/ULM), wet", "hour", 930,
     "Wet hourly cost for a light aircraft on reconnaissance or survey. A "
     "microlight is roughly half this and a helicopter roughly double. INDICATIVE"),
    ("VEH_HIRE", "4x4 with driver, local hire", "day", 150,
     "Hired, not bought: at this scale a fleet is a liability, not an asset. FIRM"),
    ("MBIKE", "Motorbike, 125cc, delivered and registered", "unit", 4100,
     "Delivered price in South Sudan including transport, registration and "
     "plates. Note this is several times a capital-city street price elsewhere "
     "in the region - do not budget this line from a Bangui or Nairobi quote. FIRM"),
    ("MBIKE_RUN", "Motorbike running (fuel, oil, maintenance)", "vehicle-month", 120,
     "Per bike per active month. FIRM"),
    ("FREIGHT_PCT", "Freight, customs and clearing, applied to imported goods", "share", 0.40,
     "Landed cost of anything bought outside the country runs far above its "
     "invoice: air freight, customs, clearing and inland transport. Budgeting "
     "under 40% on this ground is fiction. FIRM"),
    # -- equipment ---------------------------------------------------------
    ("PHONE", "Rugged smartphone (data collection)", "unit", 250, "Landed. FIRM"),
    ("POWER", "Solar power bank / field charger", "unit", 60, "Landed. FIRM"),
    ("LAPTOP", "Laptop (business class), landed", "unit", 1700,
     "Business-class machine landed in country. FIRM"),
    ("INREACH", "Satellite messenger with mount", "unit", 450,
     "Deliberately a messenger, not a satellite PTT handset: the handsets cost "
     "three times this and the teams need position and a panic button, not "
     "voice. FIRM"),
    ("INREACH_SUB", "Satellite messenger subscription", "unit-month", 35, "FIRM"),
    ("STARLINK_KIT", "Starlink terminal", "unit", 500, "Current hardware price, 2026"),
    ("STARLINK_MO", "Starlink subscription", "unit-month", 40,
     "Current 2026 tariff. Older programmes in the region still carry legacy "
     "business plans at USD 700-900/month - do not copy those figures across"),
    ("AIRTIME", "Mobile airtime", "person-month", 15, "Per staff member. FIRM"),
    ("UNIFORM", "Scout uniform and personal kit", "person-year", 250,
     "Two sets, boots, pack and badges, excluding freight. FIRM"),
    ("CBO_SEED", "Conservancy CBO governance seed (board, stakeholder meetings, office)", "conservancy-year", 17000,
     "Board 5,000 + stakeholder relationship 2,000 + office 10,000 from the AP SSD "
     "conservancy model: what a community body needs to exist before it has "
     "its own funding. The model's 50,000 direct community fund is NOT here - "
     "it is the conservancy's own budget once registered. INDICATIVE"),
    ("FIELDKIT", "Team field kit (tent, tarp, cooking, torches, pangas)", "team", 1400,
     "Per four-person team, excluding freight. FIRM"),
    ("CAMTRAP", "Camera trap", "unit", 450, "Landed, per unit. FIRM"),
    ("GEN", "Generator, 5 kVA", "unit", 2500, "Installed. FIRM"),
    ("SOLAR", "Office solar and battery backup", "set", 4000, "Installed. INDICATIVE"),
    # -- premises and running ----------------------------------------------
    ("OFFICE_WAU", "Office/compound rent, Wau, shared with the national partner", "month", 800,
     "A shared room and yard in the partner's existing premises. No Juba office "
     "is budgeted: African Parks South Sudan hosts the Juba desk. FIRM"),
    ("OFFICE_RUN", "Office running (fuel, power, water, cleaning, consumables)", "month", 350,
     "FIRM"),
    ("ACC_JUBA", "Accommodation, Juba", "night", 100, "FIRM"),
    ("ACC_FIELD", "Accommodation, field/town", "night", 35, "FIRM"),
    # -- programme ----------------------------------------------------------
    ("TRAIN_PD", "Training, per participant-day (venue, board, allowance, materials)",
     "person-day", 30,
     "All-in per participant per training day: board and lodging, the trainee "
     "allowance, hall hire and materials. FIRM"),
    ("MEETING", "Community/stakeholder meeting (refreshments, transport, hall)", "meeting", 450,
     "One community or leadership meeting including participants' transport. FIRM"),
    ("SOLAR_PUMP", "Solar pump fitted to an existing borehole", "unit", 7500,
     "Submersible solar pump, panels, riser and trough fitted to an existing borehole, with a "
     "water-committee handover; no drilling. INDICATIVE - head and trough length drive it"),
    ("VET_CAMP", "Veterinary campaign, one dry season (drugs, vaccination, handling)",
     "campaign", 14000,
     "One dry-season campaign along the corridor, delivered by the veterinary "
     "partner. INDICATIVE"),
    ("SURVEY_TECH", "Aerial survey technical package (design, crew, analysis)", "survey", 25000,
     "Design, observers, data handling and the written result - flying hours "
     "are a separate line. Assumes the aircraft and protocol are BORROWED from "
     "an operator that already has them, not procured. INDICATIVE"),
    ("LEGAL", "Legal retainer, in-country counsel", "month", 500,
     "Monthly retainer, not a per-matter fee. FIRM"),
    ("REG_NGO", "NGO/CBO registration, renewals, no-objection letters", "year", 3500,
     "Annual cycle of registration, renewal and the letters that unlock "
     "operations. FIRM"),
    ("PERMIT", "Work and residence permit, per expatriate", "person-year", 1000,
     "NGO Act 2016 s.18 regime. FIRM"),
    ("AUDIT", "Annual audit and statutory accounts", "year", 12000,
     "Independent audit, required at renewal by NGO Act 2016 s.13. FIRM"),
    ("GRANT_HARD", "Partner grant: national NGO, Wau/Raga base and mobilisation", "year", 60000,
     "The implementing body on the ground: premises, mobilisers, county-level "
     "registration and community logistics in both anchor towns. Buying into an "
     "organisation that is already registered and already present is cheaper "
     "and faster than building one. INDICATIVE - set by negotiation"),
    ("GRANT_VET", "Partner grant: veterinary delivery on the corridor", "year", 35000,
     "The veterinary half of the price the herders named. INDICATIVE"),
    ("GRANT_LOCAL", "Partner grant: local CBO at a single site", "year", 15000,
     "A county-registered local body on the south-east approach. INDICATIVE"),
    ("SSWS_SUPP", "SSWS institutional support (posts, fuel, radios, office kit)", "year", 20000,
     "Equipping the two existing but unequipped posts and the state office: "
     "radios and solar, fuel, a computer, connectivity. This is what makes SSWS "
     "the visible lead rather than a logo. INDICATIVE"),
]


CATS = [
    "1 International staff", "2 National staff", "3 Local partners and SSWS",
    "4 Field activities", "5 Equipment and supplies", "6 Transport",
    "7 Travel and accommodation", "8 Premises and utilities",
    "9 Communications and IT", "10 Training", "11 Legal, registration and audit",
    "12 CAR conservancies (separate budget)", "13 DRC conservancies (separate budget)", "14 Sudan conservancies (separate budget)",
]
# the foreign STRANDS (plan_deploy.py STRANDS → facts deploy.strands): each is its own category, delivery unit and total.
# Order fixed so the category numbers are stable whether or not a strand has teams this run.
STRAND_CAT = {"CAR": "12 CAR conservancies (separate budget)", "COD": "13 DRC conservancies (separate budget)", "SDN": "14 Sudan conservancies (separate budget)"}
STRAND_KEYS = tuple(STRAND_CAT)
GOODS_CATS = ("5 Equipment and supplies", "6 Transport")
GOODS_CODES = ("UNIFORM", "FIELDKIT", "PHONE", "POWER", "MBIKE", "INREACH", "LAPTOP", "GEN", "SOLAR", "STARLINK_KIT", "CAMTRAP")   # imported goods wherever they sit (a strand's category holds both)
def is_goods(r): return r["cat"] in GOODS_CATS or (r["cat"] in STRAND_CAT.values() and r["code"] in GOODS_CODES)

ACTIONS = {
    "A1": "1  Secure SSWS backing first",
    "A2": "2  Get into the regulations being drafted",
    "A3": "3  Ask for the s.24 gazette closure order",
    "A4": "4  Register under the 2026 Act early, rim first",
    "A5": "5  Treat the road answer as final",
    "A6": "6  Keep the corridor axis, budget its price",
    "A7": "7  Field-check and fly the rim",
    "A8": "8  Land-use planning with the boom towns",
    "A9": "9  Fix the site list",
    "A10": "10 Ground-truth checklist for the teams",
    "A11": "11 Attach to the funding architecture",
}

ASSUMPTIONS = [
    ("FREIGHT_PCT", "Freight, customs and clearing on equipment and transport", 0.40,
     "Applied to categories 5 and 6. Anything imported arrives at well above its "
     "invoice: air freight, customs, clearing agent and inland transport. "
     "Budgeting under 40% here is fiction"),
    ("SUPPORT_PCT", "Chinko HQ oversight and AP South Sudan Juba desk", 0.08,
     "Shared services actually consumed: finance and payroll systems, "
     "procurement, Juba representation and approvals, HR, safety. A rate against "
     "a service the project uses, not a second structure"),
    ("CONTING_PCT", "Contingency", 0.07,
     "One field window a year in a low-frequency, high-amplitude state: the risk "
     "priced is a lost season, plus SSP/USD movement on locally settled costs"),
    ("BANK_PCT", "Bank charges and FX cost", 0.015,
     "Transfer fees and the spread on moving USD into a country that settles "
     "much of this budget in local currency"),
]


# --------------------------------------------------------------------------
# LINES: derived from facts (team counts per year) + plan.yaml
# --------------------------------------------------------------------------
def lines(F, P):
    """Return [(cat, action, item, basis, rate_code, qty_per_year, h1_share, delivery_unit)].
    F = facts.json (needs F['deploy']['by_year']), P = plan.yaml. qty is a list of len N."""
    N = len(P["year_labels"])
    by = F["deploy"]["by_year"]                  # [{echo, tango, fp, staff, new_echo, new_tango, new_fp}, ...]
    Y = list(range(N))
    echo = [by[y]["echo"] for y in Y]; tango = [by[y]["tango"] for y in Y]; fp = [by[y]["fp"] for y in Y]
    teams = [echo[y] + tango[y] for y in Y]
    n_echo = [by[y]["new_echo"] for y in Y]; n_tango = [by[y]["new_tango"] for y in Y]; n_fp = [by[y]["new_fp"] for y in Y]
    n_teams = [n_echo[y] + n_tango[y] for y in Y]
    T = P["team"]; tsize = T["scouts"] + T["leader"]
    me, mt = P["months_paid"]["echo"], P["months_paid"]["tango"]
    team_months = [echo[y] * me[y] + tango[y] * mt[y] for y in Y]          # team-months in the field
    scout_pm = [team_months[y] * T["scouts"] for y in Y]
    lead_pm = [team_months[y] * T["leader"] for y in Y]
    fp_pm = [fp[y] * 12 for y in Y]
    coord_pm = P["coordinator_months"]; fin_pm = P["finance_officer_months"]
    natl_pm = [scout_pm[y] + lead_pm[y] + fp_pm[y] + coord_pm[y] + fin_pm[y] for y in Y]
    field_staff = [teams[y] * tsize + fp[y] for y in Y]
    new_field_staff = [n_teams[y] * tsize + n_fp[y] for y in Y]
    fp_away = [max(fp[y] - 1, 0) for y in Y]                               # focal points outside Wau
    sy = P["survey_year"]
    def yr(v, year):  # one-off in a given year (1-based), else 0
        return [v if y + 1 == year else 0 for y in Y]
    def s(lst):
        return ", ".join(f"Y{y+1} {lst[y]:g}" for y in Y)
    echo_s = s(echo); tango_s = s(tango); fp_s = s(fp)
    # ---- the foreign strands: ECHO teams on CAR / DRC / Sudan community zones (K1.., D1.., S1..), costed as their own lines
    #      with the strand code as delivery unit so each subtotals separately (not the South Sudan request). Same rates, same calendar.
    SB = F["deploy"].get("strands") or {}

    L = []
    A = L.append
    # ---- 1 International staff
    A(("1 International staff", "A1", "Technical adviser, based Chinko HQ (oversight visits plus remote)",
       f"{s(P['adviser_months'])} months. Oversight from Chinko, not a resident expatriate structure",
       "SAL_INTL", P["adviser_months"], 0.67, "HQ"))
    A(("1 International staff", "A1", "Adviser work and residence permits", "NGO Act 2016 s.18", "PERMIT", [1] * N, 1.0, "HQ"))
    # ---- 2 National staff
    A(("2 National staff", "A1", "Project coordinator (national), Juba/Wau",
       f"{s(coord_pm)} months - the permanent face of the project", "SAL_COORD", coord_pm, 0.5, "FP"))
    A(("2 National staff", "A1", "Focal points (one person each), 12 months",
       f"Focal points {fp_s} ({', '.join(F['deploy']['fp_places'])}); county and traditional authorities; year-round",
       "SAL_OFFICER", fp_pm, 0.5, "FP"))
    A(("2 National staff", "A1", "Finance/admin officer, 0.5 FTE hosted by AP South Sudan, Juba",
       "Half-time. Banking and approvals run through the AP South Sudan country representative's office",
       "SAL_OFFICER", fin_pm, 0.5, "FP"))
    A(("2 National staff", "A4", "Community wildlife scouts (ECHO/TANGO members)",
       f"Seasonal contracts, {T['scouts']} scouts per team. ECHO teams {echo_s} x {'/'.join(map(str, me))} months; "
       f"TANGO teams {tango_s} x {'/'.join(map(str, mt))} months (herd onset to end of dry season)",
       "SAL_SCOUT", scout_pm, 1.0, "TEAM"))
    A(("2 National staff", "A4", "Scout team leaders", "One per team, same months", "SAL_LEAD", lead_pm, 1.0, "TEAM"))
    A(("2 National staff", "A4", "Scout and leader food allowance", "Same person-months as scouts plus leaders",
       "RATION", [scout_pm[y] + lead_pm[y] for y in Y], 1.0, "TEAM"))
    A(("2 National staff", "A1", "National staff healthcare contribution", "All national person-months",
       "HEALTH_N", natl_pm, 0.6, "TEAM"))
    # ---- 3 Local partners and SSWS
    A(("3 Local partners and SSWS", "A4", "Partner grant: national NGO, Wau/Raga base and mobilisation",
       "The implementing body on the ground; registered, present, inside the NGO Act's staffing rule",
       "GRANT_HARD", [1] * N, 0.5, "FP"))
    A(("3 Local partners and SSWS", "A6", "Partner grant: veterinary delivery on the corridor",
       "Once the corridor talks have a counterpart and a route", "GRANT_VET", [1 if v else 0 for v in P["vet_campaigns"]], 0.5, None))
    A(("3 Local partners and SSWS", "A9", "Partner grant: local partner, Tambura (FFI working area)",
       "The Tambura conservancy is negotiated with the partner already present", "GRANT_LOCAL", [1] * N, 0.5, "FP"))
    A(("3 Local partners and SSWS", "A1", "SSWS institutional support (existing posts, radios, fuel)",
       "Visible SSWS ownership is the premise of the plan; the posts exist and are unequipped", "SSWS_SUPP", [1] * N, 0.7, "FP"))
    A(("3 Local partners and SSWS", "A1", "SSWS field allowances, joint patrols and missions",
       f"{P['ssws_days_per_team']} staff-days per team-year; teams {s(teams)}. Joint or SSWS-embedded, never parallel",
       "SSWS_ALLOW", [teams[y] * P["ssws_days_per_team"] for y in Y], 1.0, "TEAM"))
    # ---- 4 Field activities
    A(("4 Field activities", "A7", "Rim reconnaissance flights (Busseri headwaters, Nahr al Jur)",
       f"{s(P['recon_flight_hours'])} hours in the Dec-Feb window; gold targets here are dark to satellites",
       "FLY_HR", P["recon_flight_hours"], 1.0, None))
    if sy:
        A(("4 Field activities", "A7", "Aerial wildlife survey, Numatina-Boro: technical package",
           f"Y{sy} only. First survey since 2007; capability borrowed, not procured", "SURVEY_TECH", yr(1, sy), 0.0, None))
        A(("4 Field activities", "A7", "Aerial survey flying hours", f"Y{sy}: {P['survey_hours']} hours at survey altitude",
           "FLY_HR", yr(P["survey_hours"], sy), 0.0, None))
    A(("4 Field activities", "A7", "Ground field-check missions (rim cells, gold-target cells, uncertain sites)",
       f"{s(P['field_check_missions'])} missions x 12 vehicle-days", "VEH_HIRE", [m * 12 for m in P["field_check_missions"]], 1.0, None))
    A(("4 Field activities", "A6", "Corridor and conservancy meetings with herder leadership and communities",
       f"{P['meetings_per_team']} per team per year, Dec-Feb only; teams {s(teams)}", "MEETING",
       [teams[y] * P["meetings_per_team"] for y in Y], 1.0, "FP"))
    A(("4 Field activities", "A6", "Water points on the corridor (solar pumps on existing boreholes)",
       f"{s(P['solar_pumps'])}. The herders named the price: ground, water, veterinary and medical support - delivered, not promised",
       "SOLAR_PUMP", P["solar_pumps"], 0.0, None))
    A(("4 Field activities", "A6", "Veterinary campaign, dry season", f"{s(P['vet_campaigns'])}, through the veterinary partner",
       "VET_CAMP", P["vet_campaigns"], 0.0, None))
    fd = P["facilitator_days_per_conservancy"]
    A(("4 Field activities", "A4", "Conservancy constitution facilitation",
       f"{fd} days to scope each new conservancy (new ECHO zones {s(n_echo)}) plus {fd} per conservancy in each year it is being filed "
       f"(ECHO zones {echo_s}). The constitution is the slow part",
       "FACILITATOR", [n_echo[y] * fd + echo[y] * fd for y in Y], 0.5, None))
    A(("4 Field activities", "A4", "Conservancy CBO governance seed",
       f"One per conservancy in the year its application is filed (new ECHO zones {s(n_echo)} lag one year: {s([0]+n_echo[:-1])}); "
       "grows into the AP SSD conservancy model (docs/plan/reference_conservancy_budget.md)",
       "CBO_SEED", [0] + n_echo[:-1], 0.0, None))
    A(("4 Field activities", "A10", "Camera traps for community biomonitoring", f"{s(P['camera_traps'])} units on the academic partner's protocol",
       "CAMTRAP", P["camera_traps"], 0.0, "TEAM"))
    # ---- 5 Equipment
    A(("5 Equipment and supplies", "A4", "Scout uniforms and personal kit", f"Per scout and leader per year ({tsize} x teams {s(teams)})",
       "UNIFORM", [teams[y] * tsize for y in Y], 1.0, "TEAM"))
    A(("5 Equipment and supplies", "A4", "Team field kit", f"One per new team ({s(n_teams)})", "FIELDKIT", n_teams, 1.0, "TEAM"))
    A(("5 Equipment and supplies", "A10", "Rugged smartphones for the ground-truth checklist",
       "Two per new team plus one per new focal point", "PHONE", [2 * n_teams[y] + n_fp[y] for y in Y], 1.0, "TEAM"))
    A(("5 Equipment and supplies", "A10", "Solar power banks", "One with each phone", "POWER", [2 * n_teams[y] + n_fp[y] for y in Y], 1.0, "TEAM"))
    A(("5 Equipment and supplies", "A10", "Laptops (coordinator, focal points, partner)",
       f"Y1 coordinator + partner + focal points; then one per new focal point", "LAPTOP",
       [n_fp[y] + (2 if y == 0 else 0) for y in Y], 1.0, "FP"))
    A(("5 Equipment and supplies", "A1", "Generator, Wau base", "Y1 only", "GEN", yr(1, 1), 1.0, "FP"))
    A(("5 Equipment and supplies", "A1", "Solar and battery backup, Wau base", "Y1 only", "SOLAR", yr(1, 1), 1.0, "FP"))
    # ---- 6 Transport
    A(("6 Transport", "A4", "Motorbikes for team movement", f"One per new team ({s(n_teams)}); ECHO rides, TANGO walks with the herds but needs resupply",
       "MBIKE", n_teams, 1.0, "TEAM"))
    A(("6 Transport", "A4", "Motorbike running costs", f"Team-months in the field: {s(team_months)}", "MBIKE_RUN", team_months, 1.0, "TEAM"))
    A(("6 Transport", "A4", "Charter rotations (teams and equipment to the corridor)",
       f"One per TANGO team per year ({tango_s}) plus one in the survey/pump year", "CHARTER",
       [tango[y] + (1 if P["solar_pumps"][y] or (y + 1 == sy) else 0) for y in Y], 1.0, "TEAM"))
    # ---- 7 Travel
    A(("7 Travel and accommodation", "A1", "Regional flights, Bangui - Juba (adviser and Chinko oversight)",
       f"{s(P['adviser_rotations'])} rotations. CAR <-> South Sudan only", "FLT_REG", P["adviser_rotations"], 0.67, "HQ"))
    A(("7 Travel and accommodation", "A1", "Domestic flights, Juba - Wau/Tambura/Aweil",
       f"{s(P['domestic_legs_base'])} legs plus 6 per focal point outside Wau ({s(fp_away)})", "FLT_DOM",
       [P["domestic_legs_base"][y] + 6 * fp_away[y] for y in Y], 0.6, "FP"))
    A(("7 Travel and accommodation", "A2", "Juba accommodation (paper track: regulations, gazette request, RRC)",
       f"{s(P['juba_nights'])} nights - ten weeks of paper time before season one", "ACC_JUBA", P["juba_nights"], 0.83, "FP"))
    A(("7 Travel and accommodation", "A2", "Juba per diems", "Same days as above", "PERDIEM_J", P["juba_nights"], 0.83, "FP"))
    fn = [30 * m for m in P["field_check_missions"]]
    A(("7 Travel and accommodation", "A7", "Field accommodation, missions", f"30 person-nights per field-check mission ({s(fn)})",
       "ACC_FIELD", fn, 1.0, None))
    A(("7 Travel and accommodation", "A7", "Field per diems", "Same days as above", "PERDIEM_F", fn, 1.0, None))
    # ---- 8 Premises
    A(("8 Premises and utilities", "A1", "Wau office/compound, shared with the national partner",
       "12 months/yr. No Juba office: AP South Sudan hosts the desk", "OFFICE_WAU", [12] * N, 0.5, "FP"))
    A(("8 Premises and utilities", "A1", "Office running costs (Wau, plus each focal point's desk)",
       f"12 months x focal-point locations ({fp_s})", "OFFICE_RUN", [12 * fp[y] for y in Y], 0.5, "FP"))
    # ---- 9 Comms
    A(("9 Communications and IT", "A1", "Starlink terminals (one per focal-point base)", f"New focal points {s(n_fp)}",
       "STARLINK_KIT", n_fp, 1.0, "FP"))
    A(("9 Communications and IT", "A1", "Starlink subscriptions", f"12 unit-months per focal-point base ({fp_s})",
       "STARLINK_MO", [12 * fp[y] for y in Y], 0.5, "FP"))
    A(("9 Communications and IT", "A10", "Satellite messengers (team safety and track logging)",
       f"One per new team plus focal point ({s([n_teams[y] + n_fp[y] for y in Y])})", "INREACH",
       [n_teams[y] + n_fp[y] for y in Y], 1.0, "TEAM"))
    A(("9 Communications and IT", "A10", "Satellite messenger subscriptions", f"Team-months in the field plus 12 per focal point",
       "INREACH_SUB", [team_months[y] + 12 * fp[y] for y in Y], 1.0, "TEAM"))
    A(("9 Communications and IT", "A1", "Mobile airtime", "All national person-months", "AIRTIME", natl_pm, 0.6, "FP"))
    # ---- 10 Training
    A(("10 Training", "A4", "Scout induction and community-engagement training",
       f"{P['train_induction_days']} days per new field staff ({s(new_field_staff)}) + {P['train_refresher_days']}-day refresher for the rest. "
       "Community engagement is explicit: the wildlife service descends from an armed force", "TRAIN_PD",
       [new_field_staff[y] * P["train_induction_days"] + (field_staff[y] - new_field_staff[y]) * P["train_refresher_days"] for y in Y],
       1.0, "TEAM"))
    A(("10 Training", "A10", "Ground-truth checklist and data training (fire, gold, herd routes, river names)",
       s(P["train_data_pd"]) + " participant-days", "TRAIN_PD", P["train_data_pd"], 1.0, "TEAM"))
    A(("10 Training", "A8", "Land-use planning workshops with the boom towns north-east of the park",
       s(P["landuse_workshop_pd"]) + " participant-days (4 workshops x 25 x 2 days)", "TRAIN_PD", P["landuse_workshop_pd"], 0.0, None))
    # ---- 11 Legal
    A(("11 Legal, registration and audit", "A2", "In-country counsel: regulations text, s.24 gazette request, boundary descriptions",
       "Retainer, 12 months/yr. Actions 2 and 3 are the cheapest and most durable acts in the plan", "LEGAL", [12] * N, 0.5, None))
    A(("11 Legal, registration and audit", "A4", "NGO/CBO registration, RRC renewal, no-objection letters",
       "Annual. Two clocks, both slow - start them in parallel", "REG_NGO", [1] * N, 1.0, None))
    A(("11 Legal, registration and audit", "A1", "Annual audit and statutory accounts", "NGO Act 2016 s.13", "AUDIT", [1] * N, 0.0, None))
    # ---- 12/13/14 foreign strands (separate budgets), one block each
    for k in STRAND_KEYS:
        sb = SB.get(k) or {}
        car = [by[y].get(f"echo_{k.lower()}", 0) for y in Y]; n_car = [by[y].get(f"new_echo_{k.lower()}", 0) for y in Y]
        if not any(car): continue
        car_months = [car[y] * me[y] for y in Y]; car_scout_pm = [car_months[y] * T["scouts"] for y in Y]; car_lead_pm = [car_months[y] * T["leader"] for y in Y]
        car_s = s(car); C = STRAND_CAT[k]; nm = {"CAR": "CAR", "COD": "DRC", "SDN": "Sudan"}[k]; law = sb.get("law", "")
        A((C, "A4", f"{nm}: community wildlife scouts", f"ECHO teams in {nm} {car_s} x {'/'.join(map(str, me))} months (same seasonal contract)", "SAL_SCOUT", car_scout_pm, 1.0, k))
        A((C, "A4", f"{nm}: scout team leaders", f"One per {nm} team, same months", "SAL_LEAD", car_lead_pm, 1.0, k))
        A((C, "A4", f"{nm}: food allowance", "Scouts plus leaders", "RATION", [car_scout_pm[y] + car_lead_pm[y] for y in Y], 1.0, k))
        A((C, "A1", f"{nm}: healthcare contribution", f"All {nm} person-months", "HEALTH_N", [car_scout_pm[y] + car_lead_pm[y] for y in Y], 0.6, k))
        A((C, "A4", f"{nm}: uniforms and personal kit", f"{tsize} x teams {car_s}", "UNIFORM", [car[y] * tsize for y in Y], 1.0, k))
        A((C, "A4", f"{nm}: team field kit", f"One per new team ({s(n_car)})", "FIELDKIT", n_car, 1.0, k))
        A((C, "A10", f"{nm}: rugged smartphones + power banks", "Two per new team", "PHONE", [2 * n for n in n_car], 1.0, k))
        A((C, "A10", f"{nm}: solar power banks", "One with each phone", "POWER", [2 * n for n in n_car], 1.0, k))
        A((C, "A4", f"{nm}: motorbikes", f"One per new team ({s(n_car)})", "MBIKE", n_car, 1.0, k))
        A((C, "A4", f"{nm}: motorbike running costs", f"Team-months: {s(car_months)}", "MBIKE_RUN", car_months, 1.0, k))
        A((C, "A10", f"{nm}: satellite messengers", f"One per new team ({s(n_car)})", "INREACH", n_car, 1.0, k))
        A((C, "A10", f"{nm}: satellite messenger subscriptions", "Team-months in the field", "INREACH_SUB", car_months, 1.0, k))
        A((C, "A6", f"{nm}: community meetings", f"{P['meetings_per_team']} per team per year; teams {car_s}", "MEETING", [car[y] * P["meetings_per_team"] for y in Y], 1.0, k))
        A((C, "A4", f"{nm}: conservancy facilitation ({law})" if law else f"{nm}: conservancy facilitation", f"{fd} days per new conservancy + {fd} per conservancy per filing year", "FACILITATOR", [n_car[y] * fd + car[y] * fd for y in Y], 0.5, k))
        A((C, "A4", f"{nm}: conservancy governance seed", "One per conservancy the year after scoping", "CBO_SEED", [0] + n_car[:-1], 0.0, k))
        A((C, "A4", f"{nm}: scout induction and refresher training", f"{P['train_induction_days']} days per new field staff + {P['train_refresher_days']} refresher", "TRAIN_PD",
           [n_car[y] * tsize * P["train_induction_days"] + (car[y] - n_car[y]) * tsize * P["train_refresher_days"] for y in Y], 1.0, k))
    return L


# --------------------------------------------------------------------------
def compute(F, P):
    N = len(P["year_labels"]); Y = range(N)
    rate = {r[0]: r[3] for r in RATES}; unit = {r[0]: r[2] for r in RATES}
    a = {k: v for k, _l, v, _s in ASSUMPTIONS}
    rows = []
    for cat, act, item, basis, code, qty, h1, drv in lines(F, P):
        tot = [q * rate[code] for q in qty]
        rows.append(dict(cat=cat, act=act, item=item, basis=basis, code=code, unit=unit[code],
                         rate=rate[code], qty=qty, tot=tot, h1=h1, drv=drv))
    def stack(direct, goods):
        freight = goods * a["FREIGHT_PCT"]; bank = (direct + freight) * a["BANK_PCT"]
        sub = direct + freight + bank; supp = sub * a["SUPPORT_PCT"]; cont = (sub + supp) * a["CONTING_PCT"]
        return dict(direct=direct, freight=freight, bank=bank, sub=sub, supp=supp, cont=cont, total=sub + supp + cont)
    per_year = [stack(sum(r["tot"][y] for r in rows), sum(r["tot"][y] for r in rows if is_goods(r))) for y in Y]
    t = {k: [p[k] for p in per_year] for k in per_year[0]}
    t["a"] = a; t["N"] = N
    t["load"] = t["total"][0] / t["direct"][0] if t["direct"][0] else 1.0
    t["h1"] = stack(sum(r["tot"][0] * r["h1"] for r in rows),
                    sum(r["tot"][0] * r["h1"] for r in rows if is_goods(r)))
    t["drv"] = {}
    for key in ("TEAM", "FP", "HQ") + STRAND_KEYS:
        d = [sum(r["tot"][y] for r in rows if r["drv"] == key) for y in Y]
        t["drv"][key] = dict(direct=d, loaded=[x * t["load"] for x in d])
    t["by_action"] = {k: sum(sum(r["tot"]) for r in rows if r["act"] == k) for k in ACTIONS}
    t["by_cat"] = {c: [sum(r["tot"][y] for r in rows if r["cat"] == c) for y in Y] for c in CATS}
    t["corridor_price"] = sum(sum(r["tot"]) for r in rows if r["act"] == "A6" and r["drv"] not in STRAND_KEYS)
    # each foreign strand as its own loaded total, and the South Sudan request without them (several budgets, one workbook)
    t["strand"] = {}
    for k in STRAND_KEYS:
        sd = [sum(r["tot"][y] for r in rows if r["drv"] == k) for y in Y]; sg = [sum(r["tot"][y] for r in rows if r["drv"] == k and is_goods(r)) for y in Y]
        t["strand"][k] = {kk: [v[kk] for v in (stack(sd[y], sg[y]) for y in Y)] for kk in ("direct", "total")}
    t["car"] = t["strand"]["CAR"]
    t["ssd_total"] = [t["total"][y] - sum(t["strand"][k]["total"][y] for k in STRAND_KEYS) for y in Y]
    t["oneoffs"] = [sum(r["tot"][y] for r in rows if any(w in r["item"].lower() for w in ("survey", "solar pump", "water point"))) for y in Y]
    return rows, t


# --------------------------------------------------------------------------
# workbook
# --------------------------------------------------------------------------
THIN = Side(style="thin", color="BBBBBB"); BORDER = Border(bottom=THIN)
H_FILL = PatternFill("solid", fgColor="1F3864"); T_FILL = PatternFill("solid", fgColor="FFE699")
MONEY = '#,##0'


def style_header(ws, row, ncol):
    for i in range(1, ncol + 1):
        c = ws.cell(row=row, column=i)
        c.font = Font(bold=True, color="FFFFFF", size=10); c.fill = H_FILL
        c.alignment = Alignment(vertical="center", wrap_text=True)
    ws.row_dimensions[row].height = 30


def _pivot_cache(src_ws, header_row, ncols, rows):
    """One pivot cache over the Allocation table, shared by every pivot sheet (refreshOnLoad: Excel recomputes it from
    the live formulae when the file is opened). rows = the current values. Returns (cache, hdr, shared_items)."""
    from openpyxl.pivot.cache import CacheDefinition, CacheSource, WorksheetSource, CacheField, SharedItems
    from openpyxl.pivot.record import RecordList, Record
    from openpyxl.pivot.fields import Text, Number, Index
    hdr = [src_ws.cell(row=header_row, column=c).value for c in range(1, ncols + 1)]
    last = header_row + len(rows)
    ref = f"A{header_row}:{get_column_letter(ncols)}{last}"
    cfields, shared = [], {}
    for h in hdr:
        vals = [r[h] for r in rows]
        if vals and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in vals):
            cfields.append(CacheField(name=h, numFmtId=0, sharedItems=SharedItems(
                containsSemiMixedTypes=False, containsString=False, containsNumber=True,
                containsInteger=all(float(v).is_integer() for v in vals), minValue=min(vals), maxValue=max(vals))))
        else:
            uniq = list(dict.fromkeys(str(v) for v in vals)); shared[h] = uniq
            cfields.append(CacheField(name=h, sharedItems=SharedItems(count=len(uniq), _fields=[Text(v=u) for u in uniq])))
    recs = [Record(_fields=[Index(v=shared[h].index(str(r[h]))) if h in shared else Number(v=float(r[h])) for h in hdr]) for r in rows]
    cache = CacheDefinition(refreshOnLoad=True, saveData=True, recordCount=len(recs), createdVersion=6, refreshedVersion=6,
                            minRefreshableVersion=3, cacheSource=CacheSource(type="worksheet", worksheetSource=WorksheetSource(ref=ref, sheet=src_ws.title)),
                            cacheFields=cfields)
    cache.records = RecordList(r=recs)
    return cache, hdr, shared


def _pivot(cache, hdr, shared, target_ws, anchor, row_field, data_fields, name, col_field=None):
    """A pivot table on target_ws reading the shared cache (workbook cacheId 1)."""
    from openpyxl.pivot.table import (TableDefinition, Location, PivotField, FieldItem, RowColField, RowColItem, DataField, PivotTableStyle)
    from openpyxl.pivot.fields import Index
    rf = hdr.index(row_field); n_rows = len(shared[row_field])
    cf = hdr.index(col_field) if col_field else None; n_cols = len(shared[col_field]) if col_field else 0
    pfields = []
    for i, h in enumerate(hdr):
        if i == rf:
            pfields.append(PivotField(axis="axisRow", showAll=False, items=[FieldItem(x=k) for k in range(n_rows)] + [FieldItem(t="default")]))
        elif cf is not None and i == cf:
            pfields.append(PivotField(axis="axisCol", showAll=False, items=[FieldItem(x=k) for k in range(n_cols)] + [FieldItem(t="default")]))
        elif h in data_fields:
            pfields.append(PivotField(dataField=True, showAll=False))
        else:
            pfields.append(PivotField(showAll=False))
    nd = len(data_fields)
    width = (n_cols + 1) * nd if col_field else nd
    col, row0 = anchor
    c0 = column_index_from_string(col)
    loc = Location(ref=f"{col}{row0}:{get_column_letter(c0 + width)}{row0 + 2 + n_rows}", firstHeaderRow=1, firstDataRow=2, firstDataCol=1)
    if col_field:
        col_fields = [RowColField(x=cf)] + ([RowColField(x=-2)] if nd > 1 else [])
        col_items = [RowColItem(x=[Index(v=k)] + ([Index(v=d)] if nd > 1 else [])) for k in range(n_cols) for d in range(nd)]
        col_items += [RowColItem(t="grand", x=[Index(v=0)] + ([Index(v=d)] if nd > 1 else [])) for d in range(nd)]
    else:
        col_fields = [RowColField(x=-2)] if nd > 1 else []
        col_items = [RowColItem(x=[Index(v=k)]) for k in range(nd)]
    pt = TableDefinition(name=name, cacheId=1, dataCaption="Values", updatedVersion=6, minRefreshableVersion=3, createdVersion=6,
                         useAutoFormatting=True, itemPrintTitles=True, indent=0, outline=True, outlineData=True, location=loc,
                         pivotFields=pfields, rowFields=[RowColField(x=rf)],
                         rowItems=[RowColItem(x=[Index(v=k)]) for k in range(n_rows)] + [RowColItem(t="grand", x=[Index(v=0)])],
                         colFields=col_fields, colItems=col_items,
                         dataFields=[DataField(name=f"Sum of {h}", fld=hdr.index(h), numFmtId=3) for h in data_fields],
                         pivotTableStyleInfo=PivotTableStyle(name="PivotStyleLight16", showRowHeaders=True, showColHeaders=True, showLastColumn=True))
    pt.cache = cache
    target_ws._pivots.append(pt)
    return pt


def _locations(F, P):
    """Every place the plan puts money, from facts: team sites (ECHO/TANGO/FP by strand) plus Chinko HQ.
    weights[y] = months active in year y (an FP is 12, HQ is 1); the Allocation sheet splits each budget line's
    delivery unit across its locations in proportion to these weights, so a line for 'all teams' becomes a cost per site."""
    N = len(P["year_labels"]); me, mt = P["months_paid"]["echo"], P["months_paid"]["tango"]
    out = []
    for tid, t in sorted(F["deploy"]["teams"].items()):
        kind, strand = t["kind"], t.get("strand", "SSD")
        if kind == "FP": drv, w = "FP", [12 if t["year"] <= y + 1 else 0 for y in range(N)]
        elif strand != "SSD": drv, w = strand, [me[y] if t["year"] <= y + 1 else 0 for y in range(N)]
        else: drv, w = "TEAM", [(me if kind == "ECHO" else mt)[y] if t["year"] <= y + 1 else 0 for y in range(N)]
        out.append(dict(id=tid, place=t["place"], kind=kind, drv=drv, country=t.get("country") or {"CAR": "CAF", "COD": "COD", "SDN": "SDN"}.get(strand, "SSD"), year=t["year"], w=w))
    out.append(dict(id="HQ", place="Chinko HQ", kind="HQ", drv="HQ", country="CAF", year=1, w=[1] * N))
    return out


def build_xlsx(path, F, P):
    from openpyxl.worksheet.datavalidation import DataValidation
    rows, t = compute(F, P); N = t["N"]; a = t["a"]
    EDIT = PatternFill("solid", fgColor="FFF2CC")
    wb = openpyxl.Workbook()
    # ---- Rates (editable: unit cost)
    ws = wb.active; ws.title = "Rates"
    ws["A1"] = "UNIT COST CATALOGUE - every rate below is used by formula in the Budget sheet"; ws["A1"].font = Font(bold=True, size=12)
    ws["A2"] = "Planning assumptions for this landscape, FIRM or INDICATIVE. None is a quotation. Yellow cells are the ones to edit."; ws["A2"].font = Font(italic=True, size=9)
    ws.append([]); ws.append(["Code", "Item", "Unit", "Unit cost USD", "What the rate assumes / how firm it is"]); style_header(ws, 4, 5)
    for code, label, unit, usd, src in RATES:
        ws.append([code, label, unit, usd, src]); r = ws.max_row
        ws.cell(row=r, column=4).number_format = '#,##0.000' if usd < 1 else MONEY; ws.cell(row=r, column=4).fill = EDIT
        for i in (2, 5): ws.cell(row=r, column=i).alignment = Alignment(wrap_text=True, vertical="top")
    rate_first, rate_last = 5, ws.max_row
    for col, w in zip("ABCDE", (14, 46, 16, 14, 78)): ws.column_dimensions[col].width = w
    ws.freeze_panes = "A5"
    # ---- Assumptions (editable: the four loading rates)
    wa = wb.create_sheet("Assumptions"); wa["A1"] = "RATES APPLIED TO THE WHOLE BUDGET"; wa["A1"].font = Font(bold=True, size=12)
    wa.append([]); wa.append(["Code", "What it covers", "Rate", "Basis"]); style_header(wa, 3, 4); keycell = {}
    for code, label, val, src in ASSUMPTIONS:
        wa.append([code, label, val, src]); r = wa.max_row
        wa.cell(row=r, column=3).number_format = '0.0%'; wa.cell(row=r, column=3).fill = EDIT; wa.cell(row=r, column=4).alignment = Alignment(wrap_text=True, vertical="top")
        keycell[code] = f"Assumptions!$C${r}"
    for col, w in zip("ABCD", (16, 52, 10, 86)): wa.column_dimensions[col].width = w
    # ---- Budget: THE line table. Editable: rate code (dropdown), quantities, first-6-month share, delivery unit (dropdown).
    wb_ = wb.create_sheet("Budget")
    wb_["A1"] = f"EASY / AP-RCA WESTERN SOUTH SUDAN - {N}-YEAR BUDGET ({P['year_labels'][0].split(' - ')[0]} - {P['year_labels'][-1].split(' - ')[-1]})"
    wb_["A1"].font = Font(bold=True, size=13)
    wb_["A2"] = ("THE SHEET TO EDIT (with Rates and Assumptions). Yellow cells: pick a rate code from the dropdown, type a quantity, "
                 "pick a delivery unit. Unit cost, totals, the Allocation sheet and every pivot follow by formula; pivots refresh on opening "
                 "(or Data > Refresh All). Quantities are derived from the deployment (teams per year) and docs/plan/plan.yaml.")
    wb_["A2"].font = Font(italic=True, size=9)
    hdr = ["Category", "Plan action", "Line item", "Basis / quantity logic", "Rate code", "Unit", "Unit cost USD"]
    for y in range(N): hdr += [f"Qty Y{y+1}", f"Total Y{y+1}"]
    hdr += [f"Total {N} yr", "Share in first 6 months", "Of which first 6 months", "Delivery unit", "helper: goods flag (freight base)"]
    wb_.append([]); wb_.append(hdr); style_header(wb_, 4, len(hdr))
    qcol = [get_column_letter(8 + 2 * y) for y in range(N)]; tcol = [get_column_letter(9 + 2 * y) for y in range(N)]
    TOT = get_column_letter(8 + 2 * N); H1S = get_column_letter(9 + 2 * N); H1 = get_column_letter(10 + 2 * N); DRV = get_column_letter(11 + 2 * N)
    GOODS = get_column_letter(12 + 2 * N)          # helper column, last and hidden: the user never edits it
    wb_.column_dimensions[GOODS].hidden = True
    first = 5
    goods_cats = ",".join(f'"{g}"' for g in GOODS_CATS); goods_codes = ",".join(f'"{g}"' for g in GOODS_CODES)
    for cat, act, item, basis, code, qty, h1, drv in lines(F, P):
        row = [cat, ACTIONS[act], item, basis, code, "", ""]
        for y in range(N): row += [qty[y], ""]
        row += ["", h1, "", drv or "NONE", ""]
        wb_.append(row); r = wb_.max_row
        wb_.cell(row=r, column=5).fill = EDIT
        wb_.cell(row=r, column=6).value = f'=VLOOKUP($E{r},Rates!$A${rate_first}:$E${rate_last},3,FALSE)'
        wb_.cell(row=r, column=7).value = f'=VLOOKUP($E{r},Rates!$A${rate_first}:$D${rate_last},4,FALSE)'; wb_.cell(row=r, column=7).number_format = MONEY
        # goods flag as the sheet's own formula: equipment/transport categories, or an imported-goods code inside a strand category
        strand_cats = ",".join(f'"{c}"' for c in STRAND_CAT.values())
        wb_[f"{GOODS}{r}"] = (f'=IF(OR(ISNUMBER(MATCH($A{r},{{{goods_cats}}},0)),AND(ISNUMBER(MATCH($A{r},{{{strand_cats}}},0)),'
                              f'ISNUMBER(MATCH($E{r},{{{goods_codes}}},0)))),1,0)')
        for y in range(N):
            wb_[f"{qcol[y]}{r}"].fill = EDIT
            wb_[f"{tcol[y]}{r}"] = f"=$G{r}*{qcol[y]}{r}"; wb_[f"{tcol[y]}{r}"].number_format = MONEY
        wb_[f"{TOT}{r}"] = "=" + "+".join(f"{c}{r}" for c in tcol); wb_[f"{TOT}{r}"].number_format = MONEY
        wb_[f"{H1S}{r}"].number_format = '0%'; wb_[f"{H1S}{r}"].fill = EDIT
        wb_[f"{H1}{r}"] = f"={tcol[0]}{r}*{H1S}{r}"; wb_[f"{H1}{r}"].number_format = MONEY
        wb_[f"{DRV}{r}"].fill = EDIT
        for c in (3, 4): wb_.cell(row=r, column=c).alignment = Alignment(wrap_text=True, vertical="top")
    last = wb_.max_row
    dv_rate = DataValidation(type="list", formula1=f"=Rates!$A${rate_first}:$A${rate_last}", allow_blank=False); wb_.add_data_validation(dv_rate)
    dv_rate.add(f"E{first}:E{last}")
    dv_drv = DataValidation(type="list", formula1='"TEAM,FP,HQ,' + ",".join(STRAND_KEYS) + ',NONE"', allow_blank=False); wb_.add_data_validation(dv_drv)
    dv_drv.add(f"{DRV}{first}:{DRV}{last}")
    cols = tcol + [TOT, H1]
    def totrow(label, formula_for):
        wb_.append([label]); r = wb_.max_row; wb_.cell(row=r, column=1).font = Font(bold=True)
        for c in cols:
            wb_[f"{c}{r}"] = formula_for(c, r); wb_[f"{c}{r}"].number_format = MONEY; wb_[f"{c}{r}"].font = Font(bold=True)
        return r
    wb_.append([])
    r_direct = totrow("DIRECT COSTS", lambda c, r: f"=SUM({c}{first}:{c}{last})")
    r_goods = totrow("of which equipment and transport (freight base)", lambda c, r: f"=SUMPRODUCT(${GOODS}${first}:${GOODS}${last},{c}${first}:{c}${last})")
    r_freight = totrow("Freight, customs, clearing", lambda c, r: f"={c}{r_goods}*{keycell['FREIGHT_PCT']}")
    r_bank = totrow("Bank charges and FX", lambda c, r: f"=({c}{r_direct}+{c}{r_freight})*{keycell['BANK_PCT']}")
    r_sub = totrow("Subtotal", lambda c, r: f"={c}{r_direct}+{c}{r_freight}+{c}{r_bank}")
    r_supp = totrow("Chinko HQ + AP South Sudan support", lambda c, r: f"={c}{r_sub}*{keycell['SUPPORT_PCT']}")
    r_cont = totrow("Contingency", lambda c, r: f"=({c}{r_sub}+{c}{r_supp})*{keycell['CONTING_PCT']}")
    r_tot = totrow("TOTAL REQUESTED, USD", lambda c, r: f"={c}{r_sub}+{c}{r_supp}+{c}{r_cont}")
    for c in cols: wb_[f"{c}{r_tot}"].fill = T_FILL
    for col, w in zip("ABCDEFG", (26, 34, 44, 60, 13, 13, 12)): wb_.column_dimensions[col].width = w
    for c in qcol + tcol + [TOT, H1S, H1]: wb_.column_dimensions[c].width = 12
    wb_.column_dimensions[DRV].width = 13
    for r in range(first, last + 1): wb_.row_dimensions[r].height = 30
    wb_.freeze_panes = "C5"
    # ---- Locations: where the plan puts people (from facts), with months active per year as the allocation weight
    locs = _locations(F, P)
    wl = wb.create_sheet("Locations"); wl["A1"] = "LOCATIONS - every site the plan staffs, from the deployment; weights split each budget line across its delivery unit's sites"
    wl["A1"].font = Font(bold=True, size=12)
    wl["A2"] = "Weight = months active in the year (an FP 12, Chinko HQ 1). Edit a weight to move money between sites; a site with 0 in a year gets nothing that year."; wl["A2"].font = Font(italic=True, size=9)
    wl.append([]); wl.append(["Team", "Place", "Kind", "Delivery unit", "Country", "Starts year"] + [f"Weight Y{y+1}" for y in range(N)]); style_header(wl, 4, 6 + N)
    loc_first = 5
    for L_ in locs:
        wl.append([L_["id"], L_["place"], L_["kind"], L_["drv"], L_["country"], L_["year"]] + L_["w"]); r = wl.max_row
        for y in range(N): wl.cell(row=r, column=7 + y).fill = EDIT
    loc_last = wl.max_row
    wl.append([]); wl.append(["Weight sums by delivery unit"]); wl.cell(row=wl.max_row, column=1).font = Font(bold=True)
    wsum_row = {}
    for key in ("TEAM", "FP", "HQ") + STRAND_KEYS:
        wl.append([key]); r = wl.max_row; wsum_row[key] = r
        for y in range(N):
            wc = get_column_letter(7 + y)
            wl.cell(row=r, column=7 + y).value = f'=SUMIF($D${loc_first}:$D${loc_last},$A{r},{wc}${loc_first}:{wc}${loc_last})'
    for col, w in zip("ABCDEF", (8, 24, 8, 14, 10, 12)): wl.column_dimensions[col].width = w
    for y in range(N): wl.column_dimensions[get_column_letter(7 + y)].width = 11
    # ---- Allocation: the long table every pivot reads. One row per budget line x location x year; amounts are live formulae.
    #      Loaded = direct x (1 + freight if goods) x (1 + bank) x (1 + support) x (1 + contingency): the loading stack is linear,
    #      so these rows sum exactly to the Budget sheet's TOTAL REQUESTED and to each strand's total in the text.
    wal = wb.create_sheet("Allocation"); wal["A1"] = "ALLOCATION - each budget line split over its delivery unit's locations and years (formulae; the source of every pivot). Do not edit."
    wal["A1"].font = Font(bold=True, size=12)
    ahdr = ["Category", "Plan action", "Line item", "Delivery unit", "Strand", "Team", "Location", "Country", "Year", "Direct USD", "Loaded USD"]
    wal.append([]); wal.append(ahdr); style_header(wal, 3, len(ahdr)); a_hdr_row = 3
    strand_name = {"CAR": "CAR conservancies (separate budget)", "COD": "DRC conservancies (separate budget)", "SDN": "Sudan conservancies (separate budget)"}
    line_rows = list(range(first, last + 1)); line_vals = rows
    def load_formula(r_b):
        return (f"(1+Budget!${GOODS}${r_b}*{keycell['FREIGHT_PCT']})*(1+{keycell['BANK_PCT']})"
                f"*(1+{keycell['SUPPORT_PCT']})*(1+{keycell['CONTING_PCT']})")
    arows = []
    for r_b, rv in zip(line_rows, line_vals):
        drv = rv["drv"] or "NONE"
        targets = [(i + loc_first, L_) for i, L_ in enumerate(locs) if L_["drv"] == drv] or [(None, dict(id="-", place="Unallocated (Juba/Wau backbone)", country="SSD"))]
        strand = strand_name.get(drv, "South Sudan request")
        for y in range(N):
            for r_l, L_ in targets:
                wal.append([f"=Budget!$A${r_b}", f"=Budget!$B${r_b}", f"=Budget!$C${r_b}", f"=Budget!${DRV}${r_b}", strand,
                            L_["id"], L_["place"], L_["country"], f"Y{y+1}", "", ""]); r = wal.max_row
                if r_l is None:
                    share = "1"
                else:
                    wc = get_column_letter(7 + y)
                    share = f"IF(Locations!${wc}${wsum_row[drv]}=0,0,Locations!${wc}${r_l}/Locations!${wc}${wsum_row[drv]})"
                wal.cell(row=r, column=10).value = f"=Budget!${tcol[y]}${r_b}*{share}"; wal.cell(row=r, column=10).number_format = MONEY
                wal.cell(row=r, column=11).value = f"=J{r}*{load_formula(r_b)}"; wal.cell(row=r, column=11).number_format = MONEY
                # cache values for the pivot definitions (Excel refreshes them from the formulae on open)
                w_ = 1.0 if r_l is None else ((L_["w"][y] / sum(x["w"][y] for x in locs if x["drv"] == drv)) if sum(x["w"][y] for x in locs if x["drv"] == drv) else 0.0)
                direct = rv["tot"][y] * w_
                load = (1 + (a["FREIGHT_PCT"] if is_goods(rv) else 0)) * (1 + a["BANK_PCT"]) * (1 + a["SUPPORT_PCT"]) * (1 + a["CONTING_PCT"])
                arows.append(dict(zip(ahdr, [rv["cat"], ACTIONS[rv["act"]], rv["item"], drv, strand, L_["id"], L_["place"], L_["country"], f"Y{y+1}", direct, direct * load])))
    for col, w in zip("ABCDEFGHIJK", (26, 34, 44, 12, 34, 8, 26, 8, 6, 14, 14)): wal.column_dimensions[col].width = w
    wal.sheet_properties.tabColor = "BBBBBB"
    wal.freeze_panes = "A4"
    # ---- Pivots (real pivot tables over Allocation; refresh on load)
    cache, phdr, pshared = _pivot_cache(wal, a_hdr_row, len(ahdr), arows)
    def pivot_sheet(title, caption, row_field, name, data_fields=("Direct USD", "Loaded USD"), col_field="Year"):
        p = wb.create_sheet(title); p["A1"] = caption; p["A1"].font = Font(bold=True, size=12)
        p["A2"] = "Pivot table over the Allocation sheet - refreshes when the file is opened (Excel: Data > Refresh All if not)."; p["A2"].font = Font(italic=True, size=9)
        _pivot(cache, phdr, pshared, p, ("A", 4), row_field, list(data_fields), name, col_field=col_field)
        p.column_dimensions["A"].width = 44
        for c in "BCDEFGHIJ": p.column_dimensions[c].width = 16
        return p
    pivot_sheet("Summary", "SUMMARY BY COST CATEGORY, direct and loaded, by year", "Category", "PivotCategory")
    pivot_sheet("By action", "COST BY PLAN ACTION", "Plan action", "PivotAction")
    pivot_sheet("By location", "COST PER LOCATION (as per the plan's team sites; lines for 'all teams' split by months active)", "Location", "PivotLocation")
    pivot_sheet("By strand", "COST BY BUDGET STRAND (South Sudan request vs the separate CAR / DRC / Sudan conservancy budgets)", "Strand", "PivotStrand")
    pivot_sheet("By delivery unit", "LOADED COST OF EACH DELIVERY UNIT (teams, focal points and backbone, Chinko HQ, strands)", "Delivery unit", "PivotUnit")
    # ---- Phasing (plain formulae: the first six months are a slice of year one)
    ws7 = wb.create_sheet("Phasing"); ws7["A1"] = "PHASING ON THE PLAN'S OWN CLOCK (first six months are a slice of year one, not an addition)"
    ws7["A1"].font = Font(bold=True, size=12); ws7.append([]); ws7.append(["Period", "Direct", "Loaded"]); style_header(ws7, 3, 3)
    ws7.append([f"First 6 months ({P['first_6_months']})", f"=Budget!${H1}${r_direct}", f"=Budget!${H1}${r_tot}"])
    for y in range(N):
        ws7.append([f"Year {y+1} ({P['year_labels'][y]})", f"=Budget!${tcol[y]}${r_direct}", f"=Budget!${tcol[y]}${r_tot}"])
    ws7.append(["Total", f"=Budget!${TOT}${r_direct}", f"=Budget!${TOT}${r_tot}"])
    for rr in range(4, ws7.max_row + 1):
        for c in (2, 3): ws7.cell(row=rr, column=c).number_format = MONEY
    ws7.column_dimensions["A"].width = 40; ws7.column_dimensions["B"].width = 14; ws7.column_dimensions["C"].width = 14
    wb.move_sheet("Budget", offset=-2)
    wb.save(path); return path


# --------------------------------------------------------------------------
# text version
# --------------------------------------------------------------------------
W = 80
def wrap(text, width=W, indent=""):
    return textwrap.fill(text, width=width, initial_indent=indent, subsequent_indent=indent)
def m(x):
    return f"{x:,.0f}"


def build_txt(path, xlsx_name, F, P):
    rows, t = compute(F, P); N = t["N"]; Y = range(N); a = t["a"]; by = F["deploy"]["by_year"]
    tot = sum(t["total"]); o = []; Pp = o.append
    yl = P["year_labels"]
    def row(label, vals, total=True):
        Pp(f"    {label:38s}" + "".join(f" {m(v):>12s}" for v in vals) + (f" {m(sum(vals)):>12s}" if total else ""))
    Pp("=" * W); Pp("")
    Pp("        WHAT IT COSTS")
    Pp(f"        A {N}-year budget for the EASY priority intervention, western South Sudan")
    Pp("        - lean, partner-delivered, seasonal. Companion to the PIP summary.")
    Pp(f"        {datetime.date.today():%B %Y} - INTERNAL. Live workbook: {xlsx_name}")
    Pp(""); Pp("=" * W); Pp(""); Pp("")
    Pp("THE BUDGET IN TEN LINES"); Pp("-" * W)
    natl = sum(sum(t["by_cat"][c]) for c in ("2 National staff", "3 Local partners and SSWS"))
    staff = " -> ".join(str(b["staff"]) for b in by)
    Pp(f"  1  {N}-year total          USD {m(tot)}  ({' / '.join(m(v) for v in t['total'])})")
    if any(any(t["strand"][k]["total"]) for k in STRAND_KEYS):
        for k in STRAND_KEYS:
            if any(t["strand"][k]["total"]):
                Pp(f"     of which {k} strand   USD {m(sum(t['strand'][k]['total']))}  ({' / '.join(m(v) for v in t['strand'][k]['total'])}) - a SEPARATE budget")
        Pp(f"     South Sudan request   USD {m(sum(t['ssd_total']))}  ({' / '.join(m(v) for v in t['ssd_total'])})")
    Pp("  2  Shape                 No new organisation. A national partner implements,")
    Pp("                           SSWS leads visibly, Chinko HQ oversees, AP South")
    Pp("                           Sudan carries Juba.")
    Pp(f"  3  People on the ground  {staff} field staff. " + "; ".join(f"Y{y+1} {by[y]['echo']} ECHO + {by[y]['tango']} TANGO + {by[y]['fp']} FP" for y in Y) + ".")
    for k in STRAND_KEYS:
        if any(b.get(f"echo_{k.lower()}") for b in by):
            Pp(f"                           {k} strand, separately: " + "; ".join(f"Y{y+1} {by[y].get(f'echo_{k.lower()}', 0)} ECHO ({by[y].get(f'staff_{k.lower()}', 0)} staff)" for y in Y) + ".")
    Pp("                           A team is 4 scouts + 1 leader; a focal point one person.")
    Pp(f"  4  Money on the ground   {natl/tot:.0%} of the total is national staff, partner")
    Pp("                           grants and SSWS support.")
    Pp(f"  5  Seasonal payroll      ECHO paid {'/'.join(map(str, P['months_paid']['echo']))} months a year, TANGO "
       f"{'/'.join(map(str, P['months_paid']['tango']))} (herd onset")
    Pp("                           to end of dry season), not a calendar year.")
    Pp(f"  6  The corridor's price  Water, veterinary and meetings: USD {m(t['corridor_price'])} -")
    Pp("                           the herders named it, so it is a line, not an intention.")
    Pp(f"  7  One expatriate        {'/'.join(map(str, P['adviser_months']))} months a year, from Chinko, "
       f"{sum(t['by_cat']['1 International staff'])/tot:.0%} of the budget.")
    Pp(f"  8  Freight is {a['FREIGHT_PCT']:.0%}        Of everything imported. A line, not a rounding error.")
    Pp(f"  9  Support is a rate     {a['SUPPORT_PCT']:.0%} for Chinko HQ and the Juba desk - services")
    Pp("                           consumed, not a second structure.")
    Pp(" 10  Actions 3, 5 and 11   Cost nothing but staff time, already paid for in the")
    Pp("                           coordinator's salary and the legal retainer.")
    Pp(""); Pp("")
    Pp("THE OPERATION THIS BUDGET PAYS FOR"); Pp("-" * W)
    for para in [
        "Four organisations already exist on or beside this ground, and the budget's first decision is to use them. "
        "A registered national NGO with offices in Wau and Raja implements: premises, mobilisers, and it is already inside "
        "the NGO Act's 80%-national staffing rule. The South Sudan Wildlife Service leads in public and on the ground; we "
        "equip its existing but unequipped posts and pay allowances for joint missions, never a government salary. Chinko "
        "HQ provides technical oversight - visits, not a resident expatriate. African Parks South Sudan carries Juba: "
        "banking, approvals, representation and the half-time finance officer.",
        f"What that leaves us to pay for directly is small and deliberate: one national coordinator, "
        f"{'/'.join(str(b['fp']) for b in by)} focal points ({', '.join(F['deploy']['fp_places'])}), "
        f"{'/'.join(str(b['echo']+b['tango']) for b in by)} seasonal scout teams, and the activities themselves. "
        "There is no vehicle fleet - 4x4s are hired by the day. No Juba office. No headquarters. The heaviest single asset is a motorbike.",
        "The calendar does the rest. Every audience is present November to February and absent June to September, so "
        "ECHO teams are contracted for the rains' village outreach plus the Dec-Feb boundary walks, TANGO teams from the "
        "month their herds arrive to the end of the dry season, and the paper track runs in the rains from Juba.",
    ]:
        Pp(wrap(para)); Pp("")
    Pp(""); Pp("THE HEADLINE"); Pp("-" * W); Pp("")
    Pp(f"    {'':38s}" + "".join(f" {('YEAR ' + str(y+1)):>12s}" for y in Y) + f" {'TOTAL':>12s}")
    Pp("    " + "-" * (W - 8))
    for cat in CATS: row(cat, t["by_cat"][cat])
    Pp("    " + "-" * (W - 8))
    row("DIRECT COSTS", t["direct"])
    row(f"Freight, customs, clearing ({a['FREIGHT_PCT']:.0%})", t["freight"])
    row(f"Bank charges and FX ({a['BANK_PCT']:.1%})", t["bank"])
    row(f"Chinko HQ + Juba desk ({a['SUPPORT_PCT']:.0%})", t["supp"])
    row(f"Contingency ({a['CONTING_PCT']:.0%})", t["cont"])
    Pp("    " + "=" * (W - 8))
    row("TOTAL REQUESTED, USD", t["total"])
    Pp("")
    peak = max(Y, key=lambda y: t["total"][y])
    Pp(wrap(f"Year {peak+1} is the peak year: it carries the one-offs (aerial survey, first solar pump: USD "
            f"{m(t['oneoffs'][peak])} direct) and the full-strength deployment of {by[peak]['staff']} field staff."))
    Pp(""); Pp("")
    Pp("PHASING ON THE PLAN'S OWN CLOCK"); Pp("-" * W)
    Pp(wrap("The FIELD clock has one window a year (Dec-Feb); the PAPER clock runs all year from Juba. "
            "The first row is a slice of year one, not an addition to it."))
    Pp(""); Pp(f"    {'PERIOD':38s} {'DIRECT':>12s} {'LOADED':>12s} {'CUMULATIVE':>12s}"); Pp("    " + "-" * (W - 8))
    h = t["h1"]
    Pp(f"    {'6 MONTHS  ' + P['first_6_months']:38s} {m(h['direct']):>12s} {m(h['total']):>12s} {m(h['total']):>12s}")
    cum = 0
    for y in Y:
        cum += t["total"][y]
        Pp(f"    {f'YEAR {y+1}    ' + yl[y]:38s} {m(t['direct'][y]):>12s} {m(t['total'][y]):>12s} {m(cum):>12s}")
    Pp("")
    Pp(wrap(f"The first six months ({P['first_6_months']}) are USD {m(h['total'])} loaded - {h['total']/t['total'][0]:.0%} of year one, "
            "and the only tranche committed before anyone knows whether season one deploys: ten weeks of paper time in Juba "
            "(coordinator, legal retainer, registration, the regulation text and the s.24 gazette request), the partner grant's "
            "first half, SSWS posts equipped, the first teams recruited, trained and kitted, and season one itself. "
            f"The gate stays in the cash flow: no SSWS backing by {P['gate_date']} and the field half of year one is not spent."))
    Pp(""); Pp("")
    Pp("WHAT A TEAM AND A FOCAL POINT ACTUALLY COST - LOADED"); Pp("-" * W)
    Pp(wrap(f"LOADED cost adds each unit's share of freight, bank charges, HQ/Juba support and contingency - a factor of "
            f"{t['load']:.2f} on year one. These are the numbers to quote when somebody asks what one team costs to have."))
    Pp(""); d = t["drv"]
    Pp(f"    {'':38s}" + "".join(f" {('YEAR ' + str(y+1)):>12s}" for y in Y) + f" {f'{N} YR':>12s}"); Pp("    " + "-" * (W - 8))
    for key, label in (("TEAM", "ECHO/TANGO teams, all (South Sudan)"), ("FP", "Focal points + Wau/Juba backbone"), ("HQ", "Chinko HQ oversight"),
                       *[(k, f"{ {'CAR': 'CAR', 'COD': 'DRC', 'SDN': 'Sudan'}[k] } conservancy teams (separate budget)") for k in STRAND_KEYS]):
        if key in STRAND_KEYS and not any(d[key]["loaded"]): continue
        row(label, d[key]["loaded"])
    Pp("")
    yf = N - 1; nt = by[yf]["echo"] + by[yf]["tango"]; tm = d["TEAM"]["loaded"][yf]; fp = d["FP"]["loaded"][yf]
    Pp(f"    PER UNIT, AT FULL STRENGTH (year {yf+1}: {nt} teams, {by[yf]['fp']} focal points)")
    Pp(f"      One ECHO/TANGO team, {P['team']['scouts']} scouts + {P['team']['leader']} leader, one season   {m(tm/nt):>10s}")
    Pp(f"      One focal point (share of the Wau/Juba backbone)              {m(fp/by[yf]['fp']):>10s}")
    Pp(f"      Chinko HQ oversight, per year                                 {m(d['HQ']['loaded'][yf]):>10s}")
    Pp("")
    Pp(wrap("The ECHO and TANGO strands are costed as one kind of team: the same contract, kit and training, under one "
            "registration as community scouts (Wildlife Act 2026 ss.15-16). What differs is the calendar and the ground."))
    Pp(""); Pp("")
    Pp("WHERE THE MONEY ACTUALLY GOES"); Pp("-" * W); Pp("")
    shares = sorted(((sum(t["by_cat"][c]), c) for c in CATS), reverse=True)
    for v, c in shares:
        if v: Pp(f"    {c:34s} {v/sum(t['direct']):5.1%}  {'#' * int(round(v / shares[0][0] * 34))}")
    Pp(""); Pp("")
    Pp("THE LINES, BY CATEGORY"); Pp("-" * W)
    Pp(wrap("Every number below is qty x unit cost; the quantity logic states where the quantity comes from. "
            "The workbook holds the same lines as live formulae."))
    for cat in CATS:
        sel = [r for r in rows if r["cat"] == cat]
        Pp(""); Pp(f"  {cat.upper()}   ({N}-yr {m(sum(t['by_cat'][cat]))})"); Pp("  " + "-" * (W - 4))
        for r in sel:
            Pp(f"  {r['item']}"); Pp(wrap(r["basis"], indent="      "))
            Pp(f"      {m(r['rate']) if r['rate'] >= 1 else f'{r['rate']:.0%}'}/{r['unit']}   "
               + "   ".join(f"Y{y+1} {r['qty'][y]:g} = {m(r['tot'][y]):>9s}" for y in Y))
    Pp(""); Pp("")
    Pp("WHAT EACH PLAN ACTION COSTS"); Pp("-" * W); Pp("")
    for key in sorted(ACTIONS, key=lambda k: int(k[1:])):
        Pp(f"    {ACTIONS[key]:44s} {m(t['by_action'][key]):>12s}")
    Pp("")
    Pp(wrap("Actions 3, 5 and 11 carry no line of their own: the s.24 gazette request, the road finding and the funding "
            "conversations are signatures, letters and phone calls, paid for inside the coordinator's salary and the legal retainer."))
    Pp(""); Pp("")
    Pp("THE UNIT COSTS, AND HOW MUCH TO TRUST THEM"); Pp("-" * W)
    Pp(wrap("Every rate is a planning assumption for this landscape, FIRM (expect to pay about this) or INDICATIVE (get a "
            "quote before you sign). The workbook's Rates sheet is the only place they live."))
    Pp("")
    for code, label, unit, usd, note in RATES:
        Pp(f"  {label}"); Pp(f"      {f'{usd:.0%}' if usd < 1 else m(usd)} per {unit}"); Pp(wrap(note, indent="      "))
    Pp(""); Pp("")
    Pp("THE FOUR RATES APPLIED TO EVERYTHING"); Pp("-" * W); Pp("")
    for code, label, val, note in ASSUMPTIONS:
        Pp(f"  {label}: {val:.1%}"); Pp(wrap(note, indent="      ")); Pp("")
    Pp("")
    Pp("WHAT THIS BUDGET DELIBERATELY DOES NOT BUY"); Pp("-" * W)
    for item, why in [
        ("A vehicle fleet", "Six months of the year the field is shut and a fleet still costs money, drivers and theft risk. 4x4s are hired by the day."),
        ("An aircraft", "Two reconnaissance flights a year and one survey do not justify an airframe. Hours are bought; the survey capability is borrowed."),
        ("A Juba office", "There is a functioning one already, belonging to a partner in the same group."),
        ("A resident expatriate structure", "One adviser, a few visits a year, from Chinko. This landscape's bottleneck has never been technical knowledge."),
        ("Road rebuilding", "Reopening the 1932 alignment is a separate capital project with its own EIA."),
        ("Enforcement hardware", "No weapons, no interception vehicles, no detention infrastructure. The posture is awareness first, seizure last."),
        ("Twelve-month scout contracts", "The audience is seasonal. Paying a full year for a two-month window is how money disappears here."),
    ]:
        Pp(f"  * {item.upper()}"); Pp(wrap(why, indent="    "))
    Pp(""); Pp("")
    Pp("WHAT WOULD MAKE THIS BUDGET WRONG"); Pp("-" * W)
    for item, why in [
        (f"No SSWS backing by {P['gate_date']}", "Then season one does not deploy and the field cost of year one should not be spent."),
        ("A lost season", "One field window a year in a state whose conflict record is quiet with bad days. A cancelled season pushes activity right by twelve months; that is what the contingency is for."),
        ("A mineral title issued on the rim", "Then year two stops expanding and spends itself on that single problem."),
        ("Partner capacity", "The model rests on a national partner able to absorb and account for the grant. Verify that in the first Wau visit."),
        ("Currency", "A meaningful share settles locally; a sharp move in the local rate moves purchasing power, not just the accounts."),
        ("Freight assumed away", "Cut the freight line to tidy the total and the equipment simply does not arrive."),
    ]:
        Pp(f"  * {item.upper()}"); Pp(wrap(why, indent="    "))
    Pp(""); Pp("-" * W)
    Pp(wrap("HOW THIS FILE AND THE WORKBOOK RELATE. Both are generated from one specification (scripts/easybudget/build_budget.py "
            "+ docs/plan/plan.yaml + the deployment in data/plan_zones/solver/deploy.json), so they cannot disagree. Edit rates or "
            "the plan there - never by typing over a total. Figures are USD."))
    Pp(f"Generated {datetime.date.today().isoformat()}."); Pp("=" * W)
    open(path, "w").write("\n".join(o) + "\n"); return path


def summary(F, P):
    """The numbers other documents quote (into facts.json)."""
    rows, t = compute(F, P)
    return dict(years=t["N"], total=round(sum(t["total"])), per_year=[round(v) for v in t["total"]],
                direct=[round(v) for v in t["direct"]], first_6_months=round(t["h1"]["total"]), load_factor=round(t["load"], 4),
                by_action={ACTIONS[k]: round(v) for k, v in t["by_action"].items()},
                action4_register=round(t["by_action"]["A4"]), corridor_price=round(t["corridor_price"]),
                team_loaded=[round(v) for v in t["drv"]["TEAM"]["loaded"]], backbone_loaded=[round(v) for v in t["drv"]["FP"]["loaded"]],
                car_total=round(sum(t["car"]["total"])), car_per_year=[round(v) for v in t["car"]["total"]], car_direct=[round(v) for v in t["car"]["direct"]],
                strands={k: dict(total=round(sum(t["strand"][k]["total"])), per_year=[round(v) for v in t["strand"][k]["total"]], direct=[round(v) for v in t["strand"][k]["direct"]]) for k in STRAND_KEYS if any(t["strand"][k]["total"])},
                ssd_total=round(sum(t["ssd_total"])), ssd_per_year=[round(v) for v in t["ssd_total"]],
                rates={k: v for k, _l, v, _s in ASSUMPTIONS})


def main():
    import yaml
    ap = argparse.ArgumentParser()
    ap.add_argument("--facts", default="data/plan_zones/solver/facts.json")
    ap.add_argument("--plan", default="docs/plan/plan.yaml")
    ap.add_argument("--xlsx", default="reports/BUDGET_EASY_2026-09.xlsx")
    ap.add_argument("--txt", default="reports/BUDGET_EASY_2026-09.txt")
    a = ap.parse_args()
    F = json.load(open(a.facts)); P = yaml.safe_load(open(a.plan))
    build_xlsx(a.xlsx, F, P); build_txt(a.txt, a.xlsx.rsplit("/", 1)[-1], F, P)
    print(json.dumps(summary(F, P), indent=1))


if __name__ == "__main__":
    main()
