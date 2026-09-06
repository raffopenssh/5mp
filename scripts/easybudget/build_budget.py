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
from openpyxl.utils import get_column_letter

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
    ("BOREHOLE", "Borehole, drilled and equipped", "unit", 16900,
     "Drilled, cased, hand-pump equipped, with a water-committee handover. "
     "INDICATIVE - depth and haulage distance drive it"),
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
]
GOODS_CATS = ("5 Equipment and supplies", "6 Transport")

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
    A(("4 Field activities", "A6", "Water points on the corridor (boreholes)",
       f"{s(P['boreholes'])}. The herders named the price: ground, water, veterinary and medical support - delivered, not promised",
       "BOREHOLE", P["boreholes"], 0.0, None))
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
       f"One per TANGO team per year ({tango_s}) plus one in the survey/borehole year", "CHARTER",
       [tango[y] + (1 if P["boreholes"][y] or (y + 1 == sy) else 0) for y in Y], 1.0, "TEAM"))
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
    per_year = [stack(sum(r["tot"][y] for r in rows), sum(r["tot"][y] for r in rows if r["cat"] in GOODS_CATS)) for y in Y]
    t = {k: [p[k] for p in per_year] for k in per_year[0]}
    t["a"] = a; t["N"] = N
    t["load"] = t["total"][0] / t["direct"][0] if t["direct"][0] else 1.0
    t["h1"] = stack(sum(r["tot"][0] * r["h1"] for r in rows),
                    sum(r["tot"][0] * r["h1"] for r in rows if r["cat"] in GOODS_CATS))
    t["drv"] = {}
    for key in ("TEAM", "FP", "HQ"):
        d = [sum(r["tot"][y] for r in rows if r["drv"] == key) for y in Y]
        t["drv"][key] = dict(direct=d, loaded=[x * t["load"] for x in d])
    t["by_action"] = {k: sum(sum(r["tot"]) for r in rows if r["act"] == k) for k in ACTIONS}
    t["by_cat"] = {c: [sum(r["tot"][y] for r in rows if r["cat"] == c) for y in Y] for c in CATS}
    t["corridor_price"] = sum(sum(r["tot"]) for r in rows if r["act"] == "A6")
    t["oneoffs"] = [sum(r["tot"][y] for r in rows if any(w in r["item"].lower() for w in ("survey", "borehole", "water point"))) for y in Y]
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


def build_xlsx(path, F, P):
    rows, t = compute(F, P); N = t["N"]; a = t["a"]
    wb = openpyxl.Workbook()
    # Rates
    ws = wb.active; ws.title = "Rates"
    ws["A1"] = "UNIT COST CATALOGUE - every rate below is used by formula in the Budget sheet"; ws["A1"].font = Font(bold=True, size=12)
    ws["A2"] = "Planning assumptions for this landscape, FIRM or INDICATIVE. None is a quotation."; ws["A2"].font = Font(italic=True, size=9)
    ws.append([]); ws.append(["Code", "Item", "Unit", "Unit cost USD", "What the rate assumes / how firm it is"]); style_header(ws, 4, 5)
    for code, label, unit, usd, src in RATES:
        ws.append([code, label, unit, usd, src]); r = ws.max_row
        ws.cell(row=r, column=4).number_format = '#,##0.000' if usd < 1 else MONEY
        for i in (2, 5): ws.cell(row=r, column=i).alignment = Alignment(wrap_text=True, vertical="top")
    rate_first, rate_last = 5, ws.max_row
    for col, w in zip("ABCDE", (14, 46, 16, 14, 78)): ws.column_dimensions[col].width = w
    ws.freeze_panes = "A5"
    # Assumptions
    wa = wb.create_sheet("Assumptions"); wa["A1"] = "RATES APPLIED TO THE WHOLE BUDGET"; wa["A1"].font = Font(bold=True, size=12)
    wa.append([]); wa.append(["Code", "What it covers", "Rate", "Basis"]); style_header(wa, 3, 4); keycell = {}
    for code, label, val, src in ASSUMPTIONS:
        wa.append([code, label, val, src]); r = wa.max_row
        wa.cell(row=r, column=3).number_format = '0.0%'; wa.cell(row=r, column=4).alignment = Alignment(wrap_text=True, vertical="top")
        keycell[code] = f"Assumptions!$C${r}"
    for col, w in zip("ABCD", (16, 52, 10, 86)): wa.column_dimensions[col].width = w
    # Budget: columns A..F fixed, then per year (Qty, Total), then Total, h1 share, h1 amount, delivery unit
    wb_ = wb.create_sheet("Budget")
    wb_["A1"] = f"EASY / AP-RCA WESTERN SOUTH SUDAN - {N}-YEAR BUDGET ({P['year_labels'][0].split(' - ')[0]} - {P['year_labels'][-1].split(' - ')[-1]})"
    wb_["A1"].font = Font(bold=True, size=13)
    wb_["A2"] = ("Quantities are derived from the deployment (teams per year) and docs/plan/plan.yaml; unit costs are "
                 "formulae into the Rates sheet - change a rate there, not here."); wb_["A2"].font = Font(italic=True, size=9)
    hdr = ["Category", "Plan action", "Line item", "Basis / quantity logic", "Rate code", "Unit", "Unit cost USD"]
    for y in range(N): hdr += [f"Qty Y{y+1}", f"Total Y{y+1}"]
    hdr += [f"Total {N} yr", "Share in first 6 months", "Of which first 6 months", "Delivery unit"]
    wb_.append([]); wb_.append(hdr); style_header(wb_, 4, len(hdr))
    qcol = [get_column_letter(8 + 2 * y) for y in range(N)]; tcol = [get_column_letter(9 + 2 * y) for y in range(N)]
    TOT = get_column_letter(8 + 2 * N); H1S = get_column_letter(9 + 2 * N); H1 = get_column_letter(10 + 2 * N); DRV = get_column_letter(11 + 2 * N)
    first = 5
    for cat, act, item, basis, code, qty, h1, drv in lines(F, P):
        row = [cat, ACTIONS[act], item, basis, code, "", ""]
        for y in range(N): row += [qty[y], ""]
        row += ["", h1, "", drv or ""]
        wb_.append(row); r = wb_.max_row
        wb_.cell(row=r, column=6).value = f'=VLOOKUP($E{r},Rates!$A${rate_first}:$E${rate_last},3,FALSE)'
        wb_.cell(row=r, column=7).value = f'=VLOOKUP($E{r},Rates!$A${rate_first}:$D${rate_last},4,FALSE)'
        for y in range(N):
            wb_[f"{tcol[y]}{r}"] = f"=$G{r}*{qcol[y]}{r}"; wb_[f"{tcol[y]}{r}"].number_format = MONEY
        wb_[f"{TOT}{r}"] = "=" + "+".join(f"{c}{r}" for c in tcol); wb_[f"{TOT}{r}"].number_format = MONEY
        wb_[f"{H1S}{r}"].number_format = '0%'
        wb_[f"{H1}{r}"] = f"={tcol[0]}{r}*{H1S}{r}"; wb_[f"{H1}{r}"].number_format = MONEY
        for c in (3, 4): wb_.cell(row=r, column=c).alignment = Alignment(wrap_text=True, vertical="top")
    last = wb_.max_row
    cols = tcol + [TOT, H1]
    def totrow(label, formula_for):
        wb_.append([label]); r = wb_.max_row; wb_.cell(row=r, column=1).font = Font(bold=True)
        for c in cols:
            wb_[f"{c}{r}"] = formula_for(c, r); wb_[f"{c}{r}"].number_format = MONEY; wb_[f"{c}{r}"].font = Font(bold=True)
        return r
    wb_.append([])
    r_direct = totrow("DIRECT COSTS", lambda c, r: f"=SUM({c}{first}:{c}{last})")
    r_goods = totrow("of which equipment and transport (freight base)",
                     lambda c, r: "=" + "+".join(f'SUMIF($A${first}:$A${last},"{g}",{c}${first}:{c}${last})' for g in GOODS_CATS))
    r_freight = totrow("Freight, customs, clearing", lambda c, r: f"={c}{r_goods}*{keycell['FREIGHT_PCT']}")
    r_bank = totrow("Bank charges and FX", lambda c, r: f"=({c}{r_direct}+{c}{r_freight})*{keycell['BANK_PCT']}")
    r_sub = totrow("Subtotal", lambda c, r: f"={c}{r_direct}+{c}{r_freight}+{c}{r_bank}")
    r_supp = totrow("Chinko HQ + AP South Sudan support", lambda c, r: f"={c}{r_sub}*{keycell['SUPPORT_PCT']}")
    r_cont = totrow("Contingency", lambda c, r: f"=({c}{r_sub}+{c}{r_supp})*{keycell['CONTING_PCT']}")
    r_tot = totrow("TOTAL REQUESTED, USD", lambda c, r: f"={c}{r_sub}+{c}{r_supp}+{c}{r_cont}")
    for c in cols: wb_[f"{c}{r_tot}"].fill = T_FILL
    for col, w in zip("ABCDEFG", (26, 34, 44, 60, 13, 13, 12)): wb_.column_dimensions[col].width = w
    wb_.freeze_panes = "C5"
    # Summary
    ws2 = wb.create_sheet("Summary"); ws2["A1"] = "SUMMARY BY COST CATEGORY (formulae over the Budget sheet)"; ws2["A1"].font = Font(bold=True, size=12)
    ws2.append([]); ws2.append(["Category"] + [f"Y{y+1}" for y in range(N)] + [f"Total {N} yr", "% of direct"]); style_header(ws2, 3, N + 3)
    for cat in CATS:
        ws2.append([cat]); r = ws2.max_row
        for y in range(N):
            ws2.cell(row=r, column=2 + y).value = f'=SUMIF(Budget!$A${first}:$A${last},$A{r},Budget!${tcol[y]}${first}:${tcol[y]}${last})'
            ws2.cell(row=r, column=2 + y).number_format = MONEY
        ws2.cell(row=r, column=N + 2).value = f"=SUM(B{r}:{get_column_letter(N+1)}{r})"; ws2.cell(row=r, column=N + 2).number_format = MONEY
    cf, cl = 4, ws2.max_row
    ws2.append(["DIRECT TOTAL"]); rt = ws2.max_row
    for i in range(2, N + 3):
        L_ = get_column_letter(i); ws2.cell(row=rt, column=i).value = f"=SUM({L_}{cf}:{L_}{cl})"
        ws2.cell(row=rt, column=i).number_format = MONEY; ws2.cell(row=rt, column=i).font = Font(bold=True)
    for r in range(cf, cl + 1):
        ws2.cell(row=r, column=N + 3).value = f"={get_column_letter(N+2)}{r}/{get_column_letter(N+2)}${rt}"; ws2.cell(row=r, column=N + 3).number_format = '0.0%'
    ws2.append([])
    for label, src in (("Freight, customs, clearing", r_freight), ("Bank charges and FX", r_bank),
                       ("Chinko HQ + AP South Sudan support", r_supp), ("Contingency", r_cont), ("TOTAL REQUESTED, USD", r_tot)):
        ws2.append([label]); r = ws2.max_row
        for i, c in enumerate(tcol + [TOT], start=2):
            ws2.cell(row=r, column=i).value = f"=Budget!${c}${src}"; ws2.cell(row=r, column=i).number_format = MONEY
            if src == r_tot: ws2.cell(row=r, column=i).font = Font(bold=True); ws2.cell(row=r, column=i).fill = T_FILL
    ws2.column_dimensions["A"].width = 40
    # By action
    ws3 = wb.create_sheet("By action"); ws3["A1"] = "COST BY PLAN ACTION"; ws3["A1"].font = Font(bold=True, size=12)
    ws3.append([]); ws3.append(["Plan action"] + [f"Y{y+1}" for y in range(N)] + [f"Total {N} yr"]); style_header(ws3, 3, N + 2)
    for key in sorted(ACTIONS, key=lambda k: int(k[1:])):
        ws3.append([ACTIONS[key]]); r = ws3.max_row
        for y in range(N):
            ws3.cell(row=r, column=2 + y).value = f'=SUMIF(Budget!$B${first}:$B${last},$A{r},Budget!${tcol[y]}${first}:${tcol[y]}${last})'
            ws3.cell(row=r, column=2 + y).number_format = MONEY
        ws3.cell(row=r, column=N + 2).value = f"=SUM(B{r}:{get_column_letter(N+1)}{r})"; ws3.cell(row=r, column=N + 2).number_format = MONEY
    ws3.column_dimensions["A"].width = 44
    # Loaded cost per delivery unit
    ws5 = wb.create_sheet("Loaded cost"); ws5["A1"] = "LOADED COST OF EACH DELIVERY UNIT (direct x loading factor)"; ws5["A1"].font = Font(bold=True, size=12)
    ws5.append([]); ws5.append(["Loading factor (total requested / direct, year 1)", "", f"=Budget!${tcol[0]}${r_tot}/Budget!${tcol[0]}${r_direct}"])
    lf = f"$C${ws5.max_row}"; ws5.cell(row=ws5.max_row, column=3).number_format = '0.000'
    ws5.append([]); ws5.append(["Delivery unit", "Units per year"] + [f"Loaded Y{y+1}" for y in range(N)] + [f"Loaded per unit Y{y+1}" for y in range(N)])
    style_header(ws5, ws5.max_row, 2 + 2 * N)
    by = F["deploy"]["by_year"]
    for key, label, counts in (("TEAM", "ECHO/TANGO scout teams (all teams)", [by[y]["echo"] + by[y]["tango"] for y in range(N)]),
                               ("FP", "Focal points and the Wau/Juba backbone", [by[y]["fp"] for y in range(N)]),
                               ("HQ", "Chinko HQ technical oversight", [1] * N)):
        ws5.append([label, "/".join(map(str, counts))]); r = ws5.max_row
        for y in range(N):
            c = ws5.cell(row=r, column=3 + y)
            c.value = f'=SUMIF(Budget!${DRV}${first}:${DRV}${last},"{key}",Budget!${tcol[y]}${first}:${tcol[y]}${last})*{lf}'; c.number_format = MONEY
            u = ws5.cell(row=r, column=3 + N + y); u.value = f"=IF({counts[y]}=0,\"\",{get_column_letter(3+y)}{r}/{counts[y]})"; u.number_format = MONEY
    ws5.column_dimensions["A"].width = 40
    wb.move_sheet("Summary", offset=-4)
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
    Pp("  2  Shape                 No new organisation. A national partner implements,")
    Pp("                           SSWS leads visibly, Chinko HQ oversees, AP South")
    Pp("                           Sudan carries Juba.")
    Pp(f"  3  People on the ground  {staff} field staff. " + "; ".join(f"Y{y+1} {by[y]['echo']} ECHO + {by[y]['tango']} TANGO + {by[y]['fp']} FP" for y in Y) + ".")
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
    Pp(wrap(f"Year {peak+1} is the peak year: it carries the one-offs (aerial survey, first borehole: USD "
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
    for key, label in (("TEAM", "ECHO/TANGO teams, all"), ("FP", "Focal points + Wau/Juba backbone"), ("HQ", "Chinko HQ oversight")):
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
