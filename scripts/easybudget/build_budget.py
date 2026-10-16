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
     "Not used since 2026-10-01: the post is merged into the Project Manager / National Coordinator "
     "(SAL_INTL). Kept as a rate for comparison. INDICATIVE"),
    ("SAL_INTL", "Project Manager / National Coordinator, based in Wau, all-in (excl. travel)", "person-month", 5500,
     "One post (author's decision, 1 Oct 2026): project management and the national, government-facing "
     "coordination are the same person. Full-time for the whole 18 months, reporting to the Chinko Landscape Coordinator. "
     "Salary and allowances; the AP regional expatriate package (and any insurance it "
     "already carries - see the group life and medevac lines) is to be costed by HR. INDICATIVE"),
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
     "Wet hourly cost for a light aircraft on reconnaissance or survey; the benchmark the two "
     "rates below are measured against. A microlight/STOL is roughly half this and a helicopter "
     "roughly double. Not used by a line since the 2026-10 review. INDICATIVE"),
    ("SAV_HR", "Savannah STOL surveillance / livestock-count flying hour", "hour", 150,
     "Reviewed plan (ET, 16 Sep 2026): several surveillance and livestock-count flights with a "
     "light STOL in the 2027-28 dry season rather than one standardised survey. This is African Parks' "
     "own Savannah operated out of Chinko, so the rate is the DRY direct operating cost (maintenance "
     "reserve, oil, consumables; the pilot is already on the Chinko payroll) and fuel is the separate "
     "AVGAS_BBL line. A commercial wet charter of a comparable STOL runs about three times this (~465/h) "
     "and is the wrong benchmark for an in-house aircraft. Positioning Chinko-Wau is inside the hours. "
     "AP Aviation rate, INDICATIVE"),
    ("HELI_HR", "R44 helicopter, operational support", "hour", 700,
     "Reviewed plan (ET): 15 hours of R44 support in the dry season. African Parks' own R44 from "
     "Chinko: DRY direct operating cost (maintenance reserve, the R44's 2,200-hour overhaul accrual, "
     "pilot on the Chinko payroll), fuel in the AVGAS_BBL line; a commercial wet charter (~1,860/h) is "
     "not the basis. Positioning to and from Chinko is inside the hours. AP Aviation rate, INDICATIVE"),
    ("AVGAS_BBL", "Avgas, one barrel positioned at a field location (transport, storage)", "barrel", 2000,
     "Provisional allowance including transport, positioning and secure storage; AP Aviation to "
     "validate barrel size and landed cost. ESTIMATE"),
    ("VEH_HIRE", "4x4 with driver, local hire", "day", 150,
     "Hired, not bought: at this scale a fleet is a liability, not an asset. FIRM"),
    ("MBIKE", "Motorbike, 125cc, delivered and registered (CAR / DRC / Sudan strands)", "unit", 4100,
     "Delivered price including transport, registration and plates; kept for the regional "
     "strands. Several times a capital-city street price - do not budget from a Bangui or Nairobi quote. FIRM"),
    ("MBIKE_SSD", "Motorbike, 125cc, delivered in South Sudan (reviewed price)", "unit", 2000,
     "Reviewed plan (ET, 16 Sep 2026): African Parks South Sudan's delivered price for a 125cc "
     "Boxer/TVS-class bike bought in Juba and trucked to Wau, registered, with helmets. It sits where "
     "the East African retail price (USD 1,200-1,600) plus Juba-Wau transport and plates lands; the "
     "regional strands' USD 4,100 (MBIKE) is a Bangui/Bangassou delivered price and not comparable. "
     "No freight is applied on top. FIRM to about +/-15%"),
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
    ("STARLINK_MO", "Starlink subscription", "unit-month", 25,
     "USD 25/month (author, 16 Oct 2026; was 40). Older programmes in the region still carry legacy "
     "business plans at USD 700-900/month - do not copy those figures across"),
    ("FIELD_SOLAR", "Field solar kit for a camp Starlink (100 W panel, controller, ~300 Wh LiFePO4 power station, cables)", "kit", 400,
     "Local market, Juba/Wau, delivered price - no freight line. A Starlink Mini draws 25-40 W; the kit runs it through the "
     "working day and charges the team's phones. Author's figure, 16 Oct 2026. INDICATIVE"),
    ("AIRTIME", "Mobile airtime", "person-month", 15, "Per staff member. FIRM"),
    ("UNIFORM", "Scout uniform and personal kit", "person-year", 250,
     "Two sets, boots, pack and badges, excluding freight. FIRM"),
    ("CBO_SEED", "Conservancy CBO governance seed (board, stakeholder meetings, office)", "conservancy-year", 17000,
     "Board 5,000 + stakeholder relationship 2,000 + office 10,000 from the AP SSD "
     "conservancy model: what a community body needs to exist before it has "
     "its own funding. The model's 50,000 direct community fund is NOT here - "
     "it is the conservancy's own budget once registered. INDICATIVE"),
    ("CBO_SEED_SSD", "Community outreach and engagement per conservancy zone, South Sudan (reviewed)", "conservancy", 15000,
     "Reviewed plan (ET): 15,000 per conservancy zone, against the 17,000 catalogue rate kept for the "
     "strands. Redirected 16 Oct 2026 (review C6/C7, author's decision) from a CBO governance seed to "
     "outreach: meetings, materials and the community's own events in the staffed zones; still available "
     "to seed a governance body if one is constituted inside the phase. INDICATIVE"),
    ("FIELDKIT", "Team field kit (tent, tarp, cooking, torches, pangas)", "team", 1400,
     "Per four-person team, excluding freight. FIRM"),
    ("CAMTRAP", "Camera trap", "unit", 450, "Not used since 2026-10-16 (review B8: the 30 units are dropped, track-count training with local hunters and herders replaces them). Kept as a rate for comparison. FIRM"),
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
    ("AUDIT", "Grant-specific audit / certified expenditure statement", "audit", 12000,
     "One incremental engagement near close-out: the grant-specific certified statement a donor "
     "requires even when folded into AP South Sudan's entity audit (NGO Act 2016 s.13). "
     "Placeholder - confirm scope and fee with AP Finance"),
    ("GRANT_HARD", "Partner grant: national NGO, Wau/Raga base and mobilisation", "project grant", 90000,
     "The implementing body on the ground: premises, mobilisers, county-level "
     "registration and community logistics in both anchor towns. Reviewed plan (ET): "
     "capped at 90,000 for the 18 months, phased 30,000 / 60,000 by phase length; detailed "
     "scope, deliverables and partner budget still required. INDICATIVE - set by negotiation"),
    ("GRANT_VET", "Partner grant: veterinary delivery on the corridor", "year", 35000,
     "The veterinary half of the price the herders named. INDICATIVE"),
    ("GRANT_LOCAL", "Partner grant: local CBO at a single site", "year", 15000,
     "A county-registered local body on the south-east approach. INDICATIVE"),
    ("SSWS_SUPP", "SSWS institutional support (posts, fuel, radios, office kit)", "year", 20000,
     "Equipping the two existing but unequipped posts and the state office: "
     "radios and solar, fuel, a computer, connectivity. This is what makes SSWS "
     "the visible lead rather than a logo. INDICATIVE"),
    # -- gap-fill lines from the 16 Sep 2026 review (ET): all ESTIMATE, none a quote --------------
    ("GAP_INTL_GLI", "Group life insurance, international PM", "person-month", 388,
     "ESTIMATE, not a quote. Benchmarked on AP Chad RO's rate for an equivalent international post "
     "(4,657.5/yr). May overlap with the AP regional expatriate package once costed - drop if so"),
    ("GAP_INTL_MED", "Medevac insurance, international PM", "person-month", 150,
     "ESTIMATE, not a quote. AP Chad RO benchmark (1,800/yr). The national healthcare line does not "
     "cover this post. May overlap with the expatriate package - drop if so"),
    ("GAP_PA_NAT", "Personal-accident cover, national staff", "person-month", 12,
     "ESTIMATE, not a quote. Group personal-accident market pricing runs 12-24 per employee-year "
     "for moderate risk; placed at the top of the band for South Sudan. No broker quote yet. The "
     "Labour Act 2017 sets no insurance mandate"),
    ("GAP_MBIKE_INS", "Motorbike third-party and theft insurance", "vehicle-year", 150,
     "ESTIMATE, not a quote. Placeholder regional rate; no insurance line existed before the review. "
     "Not an imported good: no freight"),
    ("GAP_RECRUIT_F", "Recruitment, field and focal-point hire", "hire", 100,
     "ESTIMATE, not a quote. Local advertising and interview logistics per hire"),
    ("GAP_RECRUIT_SR", "Recruitment, senior/technical hire", "hire", 2000,
     "ESTIMATE, not a quote. Advertising and shortlist-interview travel for the Project Manager / "
     "National Coordinator, the finance/admin officer and one further senior post"),
    ("GAP_SECRISK", "Security risk assessment and incident-response plan", "assignment", 10000,
     "ESTIMATE, not a quote. One external/regional security consultancy in Phase 1; no risk "
     "register existed in the first PIP draft"),
    ("GAP_CIT", "Cash-in-transit / remote disbursement run, Wau to a field base", "site-month", 175,
     "ESTIMATE built bottom-up: one hired vehicle-day (VEH_HIRE 150) plus one escort/handling day "
     "at the SSWS allowance (25). No commercial cash-in-transit service operates here. Security "
     "posture of the runs to be set by the risk assessment"),
    ("GAP_GIS", "GIS / data-management continuity (remote, part-time)", "person-month", 1100,
     "ESTIMATE, not a quote: about 3 days a month at a 300-450/day remote-GIS consultant rate to "
     "keep the gold-target and herd models current as ground truth comes in. Wide band "
     "(600-1,800/month); may be partly covered by the existing consultancy - confirm scope"),
]


RATE = {r[0]: r[3] for r in RATES}

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
NO_FREIGHT_CODES = ("MBIKE_SSD", "GAP_MBIKE_INS")   # in category 6 but not an import: a delivered price, an insurance premium
def is_goods(r): return r["code"] not in NO_FREIGHT_CODES and (r["cat"] in GOODS_CATS or (r["cat"] in STRAND_CAT.values() and r["code"] in GOODS_CODES))
EQUIPMENT_CODES = GOODS_CODES + ("MBIKE_SSD", "FIELD_SOLAR")   # what facts.budget.equipment lists (South Sudan side; FIELD_SOLAR is bought locally, no freight - review C4)

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
def derive(F, P):
    """Every derived quantity the lines and facts.budget.headcount share. Periods are PHASES (2026-10):
    deploy 'year 1' = the first wave, 'year 2' = the second wave; each KIND has its own deploy month per wave
    (plan.yaml wave_dates_by_kind: TANGO Nov 2027 before the herds, ECHO Dec 2027 / Jan 2028, FP Nov / Jan).
    A unit already in place is paid months_paid[kind][y] in phase y; a unit first deployed in phase y is paid
    months_first_phase[kind][y]. 2026-10-01: tango [0, 13], echo [0, 12] / first-phase [0, 11]: 5 x 13 + 2 x 12 + 3 x 11 = 122."""
    N = len(P["year_labels"]); Y = list(range(N))
    by = F["deploy"]["by_year"]                  # [{echo, tango, fp, staff, new_echo, new_tango, new_fp}, ...]
    T = P["team"]; tsize = T["scouts"] + T["leader"]
    mp, mf = P["months_paid"], P.get("months_first_phase") or P["months_paid"]
    def months(kind, y):   # unit-months of `kind` in phase y: units in place before the phase + units joining in it
        prev = by[y - 1][kind] if y else 0
        return prev * mp[kind][y] + by[y][f"new_{kind}"] * mf[kind][y]
    d = dict(N=N, Y=Y, by=by, tsize=tsize, pm_=P.get("phase_months") or [12] * N)
    d["echo"] = [by[y]["echo"] for y in Y]; d["tango"] = [by[y]["tango"] for y in Y]; d["fp"] = [by[y]["fp"] for y in Y]
    d["teams"] = [d["echo"][y] + d["tango"][y] for y in Y]
    d["n_echo"] = [by[y]["new_echo"] for y in Y]; d["n_tango"] = [by[y]["new_tango"] for y in Y]; d["n_fp"] = [by[y]["new_fp"] for y in Y]
    d["n_teams"] = [d["n_echo"][y] + d["n_tango"][y] for y in Y]
    d["echo_months"] = [months("echo", y) for y in Y]; d["tango_months"] = [months("tango", y) for y in Y]
    d["team_months"] = [d["echo_months"][y] + d["tango_months"][y] for y in Y]
    d["fp_pm"] = [months("fp", y) for y in Y]
    d["scout_pm"] = [d["team_months"][y] * T["scouts"] for y in Y]; d["lead_pm"] = [d["team_months"][y] * T["leader"] for y in Y]
    d["coord_pm"] = P["coordinator_months"]; d["fin_pm"] = P["finance_officer_months"]
    d["pm_pre"] = P.get("pm_prelaunch_months", 0)                                   # PM months before 1 Jun 2027, booked to Phase 1
    d["pm_pm"] = [P["pm_months"][y] + (d["pm_pre"] if y == 0 else 0) for y in Y]
    d["pm_start"] = "1 Jan 2027" if d["pm_pre"] == 5 else ("1 Jun 2027" if d["pm_pre"] == 0 else f"{d['pm_pre']} months before 1 Jun 2027")   # C1: 18 months from 1 Jun 2027
    d["natl_pm"] = [d["scout_pm"][y] + d["lead_pm"][y] + d["fp_pm"][y] + d["coord_pm"][y] + d["fin_pm"][y] for y in Y]
    d["field_staff"] = [d["teams"][y] * tsize + d["fp"][y] for y in Y]
    d["new_field_staff"] = [d["n_teams"][y] * tsize + d["n_fp"][y] for y in Y]
    # paid focal points outside Wau (flights): by PLACE, so a PM-as-FP at Wau (plan.yaml pm_fp_town; pm=True in deploy.teams, never counted in fp/new_fp) does not shift the count
    tm = (F["deploy"].get("teams") or {}).values()
    d["fp_away"] = [sum(1 for t in tm if t["kind"] == "FP" and not t.get("pm") and t["year"] <= y + 1 and "wau" not in t["place"].lower()) for y in Y]
    d["pm_fp"] = [1 if any(t["kind"] == "FP" and t.get("pm") and t["year"] <= y + 1 for t in tm) else 0 for y in Y]   # the PM's Wau desk months are still Wau base months
    d["base_pm"] = [mp["fp"][y] * d["pm_fp"][y] for y in Y]
    d["field_phase"] = [1 if d["team_months"][y] else 0 for y in Y]        # phases with teams on the ground
    # equipment: everything bought once, in the first phase, for every unit the plan will field (reviewed plan: no per-cohort split)
    d["all_teams"] = sum(d["n_teams"]); d["all_fp"] = sum(d["n_fp"])
    d["one_off"] = lambda v: [v if y == 0 else 0 for y in Y]
    return d


def lines(F, P):
    """Return [(cat, action, item, basis, rate_code, qty_per_phase, delivery_unit)].
    F = facts.json (needs F['deploy']['by_year']), P = plan.yaml. qty is a list of len N (one per phase)."""
    d = derive(F, P); N, Y, by, tsize = d["N"], d["Y"], d["by"], d["tsize"]; WD = P["wave_dates"]; WK = P.get("wave_dates_by_kind") or {k: WD for k in ("echo", "tango", "fp")}
    echo, tango, fp, teams = d["echo"], d["tango"], d["fp"], d["teams"]
    n_echo, n_tango, n_fp, n_teams = d["n_echo"], d["n_tango"], d["n_fp"], d["n_teams"]
    team_months, fp_pm, scout_pm, lead_pm = d["team_months"], d["fp_pm"], d["scout_pm"], d["lead_pm"]
    coord_pm, fin_pm, pm_pm, natl_pm = d["coord_pm"], d["fin_pm"], d["pm_pm"], d["natl_pm"]
    field_staff, new_field_staff, fp_away, fphase = d["field_staff"], d["new_field_staff"], d["fp_away"], d["field_phase"]
    T = P["team"]; pm_ = d["pm_"]; one = d["one_off"]
    me, mt = P["months_paid"]["echo"], P["months_paid"]["tango"]; mfe = P["months_first_phase"]["echo"]
    sy = P["survey_year"]
    legal_m = P.get("legal_retainer_months") or list(pm_)      # null = every month of the project (docs/plan/scenarios.yaml options)
    gis_m = P.get("gis_support_months") or list(pm_)
    secrisk = P.get("security_assessment", 1)
    mpt = P.get("mbikes_per_team", 1); fp_mb = P.get("fp_mbikes", 0)                       # review C7 "more motorbike": options, defaults = plan as it stands
    sl_team = P.get("starlink_team_bases", 0); trk_tango = P.get("trackers_per_tango_team", 1)   # review C4 comms options
    ir_echo = P.get("inreach_echo_teams", 1); ir_fp = P.get("inreach_fp", 1)                       # 1 = as it stands; 0 = Starlink + phones replace the messenger
    base_veh = P.get("base_vehicle_days_per_month", 0)                                      # review C5: 4x4 hire for the PM / Wau base
    all_echo, all_tango = sum(n_echo), sum(n_tango); tango_months = d["tango_months"]; echo_months = d["echo_months"]
    def yr(v, year):  # one-off in a given phase (1-based), else 0
        return [v if y + 1 == year else 0 for y in Y]
    def s(lst):
        return ", ".join(f"P{y+1} {lst[y]:g}" for y in Y)
    echo_s = s(echo); tango_s = s(tango); fp_s = s(fp)
    per_team_year = lambda rate_per_year: [team_months[y] * rate_per_year / 12 for y in Y]   # a per-team-YEAR ratio applied to team-months
    # ---- the foreign strands: ECHO teams on CAR / DRC / Sudan community zones (K1.., D1.., S1..), costed as their own lines
    #      with the strand code as delivery unit so each subtotals separately (not the South Sudan request). Their own (unchanged) calendar.
    SB = F["deploy"].get("strands") or {}
    sme = P.get("strand_months_paid") or me

    L = []
    A = L.append
    # ---- 1 International staff
    A(("1 International staff", "A1", "Project Manager (international), full-time, based in South Sudan",
       f"{s(pm_pm)} months: in post from {d['pm_start']}" + (f" ({d['pm_pre']} pre-launch months - presentations to SSWS, FFI and the local NGOs, "
       f"the endorsement before the gate - booked to Phase 1)" if d['pm_pre'] else "") + f" then every month to Nov 2028; USD {RATE['SAL_INTL']:,} a month all-in (EM C11, Oct 2026). Runs the "
       "assessment and engagement phase on the ground, is the Wau focal point, and reports to the Chinko Landscape Coordinator", "SAL_INTL", pm_pm, "HQ"))
    A(("1 International staff", "A1", "PM work and residence permits", "NGO Act 2016 s.18; two cycles, pending confirmation of their treatment in the expatriate package", "PERMIT", [1] * N, "HQ"))
    A(("1 International staff", "A1", "Group life insurance, international PM (ESTIMATE, gap-fill)", f"PM months {s(pm_pm)}; drop if the expatriate package already carries it", "GAP_INTL_GLI", pm_pm, "HQ"))
    A(("1 International staff", "A1", "Medevac insurance, international PM (ESTIMATE, gap-fill)", f"PM months {s(pm_pm)}; the national healthcare line does not cover this post", "GAP_INTL_MED", pm_pm, "HQ"))
    # ---- 2 National staff
    if any(coord_pm):
        A(("2 National staff", "A1", "Project coordinator (national), Juba/Wau",
           f"{s(coord_pm)} months - kept beside the PM for national and government liaison", "SAL_COORD", coord_pm, "FP"))
    A(("2 National staff", "A1", "Focal points (one person each)",
       f"Focal points {fp_s} ({', '.join(F['deploy']['fp_places'])}); " + ("the Project Manager is the wave-1 focal point (no pay here), the paid ones join from the second wave" if any(d["pm_fp"]) else "the first for the whole project, the others from the second wave") + f": {s(fp_pm)} person-months",
       "SAL_OFFICER", fp_pm, "FP"))
    A(("2 National staff", "A1", "Finance/admin officer, 0.5 FTE hosted by AP South Sudan, Juba",
       f"Half-time, {s(fin_pm)} FTE-months. Banking and approvals run through the AP South Sudan country representative's office",
       "SAL_OFFICER", fin_pm, "FP"))
    A(("2 National staff", "A4", "Community wildlife scouts (ECHO/TANGO members)",
       f"{T['scouts']} scouts per team. No field teams in Phase 1 (wet season). TANGO corridor teams ({by[0]['tango']}) x {mt[1]} months from {WK['tango'][0]}, before the herds; "
       f"ECHO conservancy teams {by[0]['echo']} x {me[1]} months from {WK['echo'][0]} and {n_echo[1]} x {mfe[1]} months from {WK['echo'][1]}: team-months {s(team_months)}",
       "SAL_SCOUT", scout_pm, "TEAM"))
    A(("2 National staff", "A4", "Scout team leaders", "One per team, same months", "SAL_LEAD", lead_pm, "TEAM"))
    A(("2 National staff", "A4", "Scout and leader food allowance", "Same person-months as scouts plus leaders",
       "RATION", [scout_pm[y] + lead_pm[y] for y in Y], "TEAM"))
    A(("2 National staff", "A1", "National staff healthcare contribution", f"All national person-months ({s(natl_pm)})",
       "HEALTH_N", natl_pm, "TEAM"))
    A(("2 National staff", "A1", "Personal-accident cover, national staff (ESTIMATE, gap-fill)", "Same national person-months as the healthcare line; no broker quote yet",
       "GAP_PA_NAT", natl_pm, "TEAM"))
    A(("2 National staff", "A1", "Recruitment, field and focal-point hires (ESTIMATE, gap-fill)", f"One per new field staff or focal point ({s(new_field_staff)})",
       "GAP_RECRUIT_F", new_field_staff, "FP"))
    A(("2 National staff", "A1", "Recruitment, senior and technical hires (ESTIMATE, gap-fill)", f"{s(P['senior_hires'])} hires: PM / national coordinator, finance/admin officer, one further senior post",
       "GAP_RECRUIT_SR", P["senior_hires"], "HQ"))
    # ---- 3 Local partners and SSWS
    A(("3 Local partners and SSWS", "A4", "Partner grant: national NGO, Wau/Raga base and mobilisation",
       f"One project grant of {RATE['GRANT_HARD']:,} phased by phase length ({'/'.join(str(m) for m in pm_)} of {sum(pm_)} months); the implementing body on the ground, registered, present, inside the NGO Act's staffing rule",
       "GRANT_HARD", [m / sum(pm_) for m in pm_], "FP"))
    A(("3 Local partners and SSWS", "A6", "Partner grant: veterinary delivery on the corridor",
       "Once the corridor talks have a counterpart and a route", "GRANT_VET", [1 if v else 0 for v in P["vet_campaigns"]], None))
    A(("3 Local partners and SSWS", "A9", "Partner grant: local partner, Tambura (FFI working area)",
       "The Tambura conservancy is negotiated with the partner already present; one tranche per phase", "GRANT_LOCAL", [1] * N, "FP"))
    A(("3 Local partners and SSWS", "A1", "SSWS institutional support (existing posts, radios, fuel)",
       "Visible SSWS ownership is the premise of the plan; the posts exist and are unequipped. One tranche per phase", "SSWS_SUPP", [1] * N, "FP"))
    A(("3 Local partners and SSWS", "A1", "SSWS field allowances, joint patrols and missions",
       f"{P['ssws_days_per_team']} staff-days per team-year applied to team-months ({s(team_months)}). Joint or SSWS-embedded, never parallel",
       "SSWS_ALLOW", per_team_year(P["ssws_days_per_team"]), "TEAM"))
    # ---- 4 Field activities
    if any(P["recon_flight_hours"]):
        A(("4 Field activities", "A7", "Rim reconnaissance flights (Busseri headwaters, Nahr al Jur)",
           f"{s(P['recon_flight_hours'])} hours in the Dec-Feb window", "FLY_HR", P["recon_flight_hours"], None))
    A(("4 Field activities", "A7", "Savannah STOL flying: surveillance, livestock counts and rim reconnaissance, 2027-28 dry season",
       f"{s(P['savannah_hours'])} hours. Several flights over the season rather than one standardised survey; the rim's gold-target cells are dark to satellites. "
       "AP Aviation to confirm rate inclusions and availability", "SAV_HR", P["savannah_hours"], None))
    if sy:
        A(("4 Field activities", "A7", "Aerial wildlife survey, Numatina-Boro: technical package",
           f"P{sy}. First structured count since 2007, flown within the Savannah hours; design, observers, analysis and the written result", "SURVEY_TECH", yr(1, sy), None))
    if P["survey_hours"]:
        A(("4 Field activities", "A7", "Aerial survey flying hours", f"P{sy}: {P['survey_hours']} hours at survey altitude", "FLY_HR", yr(P["survey_hours"], sy), None))
    A(("4 Field activities", "A7", "R44 helicopter support, operational needs",
       f"{s(P['heli_hours'])} hours. AP Aviation to confirm availability and positioning", "HELI_HR", P["heli_hours"], None))
    A(("4 Field activities", "A7", "Avgas positioned at field locations (ESTIMATE)",
       f"{s(P['avgas_barrels'])} barrels, bought and positioned in the wet season ahead of the flying season", "AVGAS_BBL", P["avgas_barrels"], None))
    A(("4 Field activities", "A7", "Ground field-check missions (rim cells, gold-target cells, uncertain sites)",
       f"{s(P['field_check_missions'])} missions x 12 vehicle-days", "VEH_HIRE", [m * 12 for m in P["field_check_missions"]], None))
    A(("4 Field activities", "A6", "Corridor and conservancy meetings with herder leadership and communities",
       f"{P['meetings_per_team']} per team-year applied to team-months ({s(team_months)}), Dec-Feb only", "MEETING",
       per_team_year(P["meetings_per_team"]), "FP"))
    if any(P["solar_pumps"]):
        A(("4 Field activities", "A6", "Solar pumps on existing boreholes along the corridor (no new water point, no drilling)",
           f"{s(P['solar_pumps'])}. The herders named the price: ground, water, veterinary and medical support - delivered, not promised",
           "SOLAR_PUMP", P["solar_pumps"], None))
    A(("4 Field activities", "A6", "Veterinary campaign, dry season", f"{s(P['vet_campaigns'])}, through the veterinary partner",
       "VET_CAMP", P["vet_campaigns"], None))
    A(("4 Field activities", "A1", "Security risk assessment and incident-response plan (ESTIMATE, gap-fill)",
       "One external assignment in Phase 1, before anyone deploys", "GAP_SECRISK", one(secrisk), None))
    fs, fc = P["facilitation_scoping_days"], P["facilitation_consultation_days"]
    A(("4 Field activities", "A4", "Conservancy constitution facilitation (preparatory)",
       f"{fs} days to scope each new conservancy (new ECHO zones {s(n_echo)}) plus {fc} per conservancy in a phase with teams in the field "
       f"(ECHO zones {echo_s}). Scoping and consultation with communities and traditional authorities, not full legal constitution - "
       "that is not expected to complete inside 18 months",
       "FACILITATOR", [n_echo[y] * fs + echo[y] * fc * fphase[y] for y in Y], None))
    A(("4 Field activities", "A4", "Community outreach and engagement in the conservancy zones",
       f"One per conservancy zone scoped in the previous phase (new ECHO zones {s(n_echo)} lag one phase: {s([0]+n_echo[:-1])}); "
       "outreach meetings, materials and community events (EM C6); may still seed a governance body if one is constituted "
       "(docs/plan/reference_conservancy_budget.md)",
       "CBO_SEED_SSD", [0] + n_echo[:-1], None))
    if any(P["camera_traps"]):   # B8 (TA email / EM C7, Oct 2026): dropped; the track-count training line in cat. 10 replaces it
        A(("4 Field activities", "A10", "Camera traps for community biomonitoring", f"{s(P['camera_traps'])} units on the academic partner's protocol",
           "CAMTRAP", P["camera_traps"], "TEAM"))
    # ---- 5 Equipment: all bought at project start (Phase 1) for every team and focal point the plan fields
    A(("5 Equipment and supplies", "A4", "Scout uniforms and personal kit", f"One set per member of every team ({tsize} x {d['all_teams']} teams), bought at start; no replacement inside the grant",
       "UNIFORM", one(d["all_teams"] * tsize), "TEAM"))
    A(("5 Equipment and supplies", "A4", "Team field kit", f"One per team ({d['all_teams']}), bought at start", "FIELDKIT", one(d["all_teams"]), "TEAM"))
    A(("5 Equipment and supplies", "A10", "Rugged smartphones for the ground-truth checklist",
       f"Two per team plus one per focal point (2 x {d['all_teams']} + {d['all_fp']}), bought at start", "PHONE", one(2 * d["all_teams"] + d["all_fp"]), "TEAM"))
    A(("5 Equipment and supplies", "A10", "Solar power banks", "One with each phone", "POWER", one(2 * d["all_teams"] + d["all_fp"]), "TEAM"))
    A(("5 Equipment and supplies", "A10", "Laptops (project manager, partner, focal points)",
       f"Project manager + partner + one per focal point ({d['all_fp']}), bought at start", "LAPTOP", one(d["all_fp"] + 2), "FP"))
    A(("5 Equipment and supplies", "A1", "Generator, Wau base", "Phase 1", "GEN", one(1), "FP"))
    A(("5 Equipment and supplies", "A1", "Solar and battery backup, Wau base", "Phase 1", "SOLAR", one(1), "FP"))
    # ---- 6 Transport
    n_bikes = mpt * d["all_teams"] + fp_mb * d["all_fp"]
    A(("6 Transport", "A4", "Motorbikes for team movement (delivered price, no freight)",
       f"{mpt} per team ({d['all_teams']} teams){f' plus {fp_mb} per focal point ({d['all_fp']})' if fp_mb else ''}, bought at start; ECHO rides, TANGO walks with the herds but needs resupply",
       "MBIKE_SSD", one(n_bikes), "TEAM"))
    bike_months = [mpt * team_months[y] + fp_mb * fp_pm[y] for y in Y]
    A(("6 Transport", "A4", "Motorbike running costs", f"Bike-months in the field: {s(bike_months)}", "MBIKE_RUN", bike_months, "TEAM"))
    A(("6 Transport", "A4", "Motorbike insurance, third party and theft (ESTIMATE, gap-fill)",
       f"{n_bikes} bikes held the whole project: vehicle-years by phase length ({'/'.join(str(m) for m in pm_)} months)", "GAP_MBIKE_INS", [n_bikes * m / 12 for m in pm_], "TEAM"))
    if base_veh:
        A(("6 Transport", "A1", "4x4 hire for the PM / Wau base (review C5)", f"{base_veh} hired vehicle-days per month, every month ({s(pm_)}); no vehicle is bought",
           "VEH_HIRE", [base_veh * m for m in pm_], "FP"))
    charters = [(tango[y] if fphase[y] else 0) + (1 if y + 1 == sy else 0) + (1 if P["solar_pumps"][y] else 0) + (1 if P["heli_hours"][y] else 0) for y in Y]
    A(("6 Transport", "A4", "Charter rotations (teams and equipment to the corridor)",
       f"One per TANGO team in a field phase ({tango_s}) plus one each for the survey{', the pump' if any(P['solar_pumps']) else ''} and the helicopter positioning: {s(charters)}", "CHARTER", charters, "TEAM"))
    # ---- 7 Travel
    A(("7 Travel and accommodation", "A1", "Regional flights, Bangui - Juba (PM and Chinko oversight)",
       f"{s(P['oversight_rotations'])} rotations. CAR <-> South Sudan only", "FLT_REG", P["oversight_rotations"], "HQ"))
    A(("7 Travel and accommodation", "A1", "Domestic flights, Juba - Wau/Tambura/Aweil",
       f"{s(P['domestic_legs_base'])} legs plus 6 per focal point outside Wau ({s(fp_away)}); set-up and government liaison front-loaded", "FLT_DOM",
       [P["domestic_legs_base"][y] + 6 * fp_away[y] for y in Y], "FP"))
    A(("7 Travel and accommodation", "A2", "Juba accommodation (paper track: regulations, gazette request, RRC)",
       f"{s(P['juba_nights'])} nights - the paper time runs in the wet season, before the field season", "ACC_JUBA", P["juba_nights"], "FP"))
    A(("7 Travel and accommodation", "A2", "Juba per diems", "Same days as above", "PERDIEM_J", P["juba_nights"], "FP"))
    fn = [30 * m for m in P["field_check_missions"]]
    A(("7 Travel and accommodation", "A7", "Field accommodation, missions", f"30 person-nights per field-check mission ({s(fn)})",
       "ACC_FIELD", fn, None))
    A(("7 Travel and accommodation", "A7", "Field per diems", "Same days as above", "PERDIEM_F", fn, None))
    A(("7 Travel and accommodation", "A1", "Cash-in-transit / remote disbursement runs, Wau to field bases (ESTIMATE, gap-fill)",
       f"Remote bases beyond Wau ({s(P['cash_run_sites'])}) x months in the phase: one hired vehicle-day plus an escort day per site-month. "
       "No commercial service operates here", "GAP_CIT", [P["cash_run_sites"][y] * pm_[y] for y in Y], "FP"))
    # ---- 8 Premises
    A(("8 Premises and utilities", "A1", "Wau office/compound, shared with the national partner",
       f"Every month of the project ({s(pm_)}). No Juba office: AP South Sudan hosts the desk", "OFFICE_WAU", list(pm_), "FP"))
    A(("8 Premises and utilities", "A1", "Office running costs (Wau, plus each focal point's desk)",
       f"Focal-point months ({s(fp_pm)})" + (f" plus the PM's Wau desk ({s(d['base_pm'])})" if any(d["pm_fp"]) else ""), "OFFICE_RUN", [fp_pm[y] + d["base_pm"][y] for y in Y], "FP"))
    # ---- 9 Comms
    n_fp_base = d["all_fp"] + max(d["pm_fp"])
    A(("9 Communications and IT", "A1", "Starlink terminals (one per focal-point base)", f"All {n_fp_base} bases" + (" (incl. the PM's Wau base)" if max(d["pm_fp"]) else "") + ", bought at start",
       "STARLINK_KIT", one(n_fp_base), "FP"))
    A(("9 Communications and IT", "A1", "Starlink subscriptions", f"Focal-point base-months ({s([fp_pm[y] + d['base_pm'][y] for y in Y])})", "STARLINK_MO", [fp_pm[y] + d["base_pm"][y] for y in Y], "FP"))
    if sl_team:
        A(("9 Communications and IT", "A1", "Starlink terminals, team bases (review C4)",
           f"One per team, ECHO and TANGO ({d['all_teams']}), bought at start: broadband, calls and data upload from every camp - this is the control-room link to Chinko / Wau", "STARLINK_KIT", one(d["all_teams"]), "TEAM"))
        A(("9 Communications and IT", "A1", "Starlink subscriptions, team bases (review C4)", f"Team-months ({s(team_months)})", "STARLINK_MO", list(team_months), "TEAM"))
        A(("9 Communications and IT", "A1", "Field solar kits for the team-base Starlinks (local purchase)",
           f"One per team ({d['all_teams']}), bought at start in Juba/Wau: a terminal without power is not a link; the kit also charges the team's phones", "FIELD_SOLAR", one(d["all_teams"]), "TEAM"))
    n_ir = trk_tango * all_tango + ir_echo * all_echo + ir_fp * d["all_fp"]
    ir_months = [trk_tango * tango_months[y] + ir_echo * echo_months[y] + ir_fp * fp_pm[y] for y in Y]
    A(("9 Communications and IT", "A10", "Satellite messengers (team safety and track logging)",
       f"{trk_tango} per TANGO team ({all_tango}: SOS and track log in the pocket when walking with the herds, away from camp and power), "
       f"{ir_echo} per ECHO team ({all_echo}), {ir_fp} per focal point ({d['all_fp']}), bought at start", "INREACH", one(n_ir), "TEAM"))
    A(("9 Communications and IT", "A10", "Satellite messenger subscriptions", f"Device-months: {s(ir_months)}", "INREACH_SUB", ir_months, "TEAM"))
    A(("9 Communications and IT", "A1", "Mobile airtime", "All national person-months", "AIRTIME", natl_pm, "FP"))
    A(("9 Communications and IT", "A10", "GIS / data-management continuity (ESTIMATE, gap-fill)",
       f"Part-time remote support every month ({s(gis_m)}) to keep the gold-target and herd models current as ground truth comes in; "
       "may be partly covered by the existing consultancy", "GAP_GIS", list(gis_m), None))
    # ---- 10 Training
    first_wave_staff = n_teams[0] * tsize
    A(("10 Training", "A4", "Scout induction and community-engagement training",
       f"{P['train_induction_days']} days per new team member ({s([n_teams[y] * tsize for y in Y])}) in the phase they are recruited - the first wave is trained in the wet season, "
       f"before anyone deploys - plus a {P['train_refresher_days']}-day refresher for the first wave ({first_wave_staff}) in Phase 2. "
       "Community engagement is explicit: the wildlife service descends from an armed force", "TRAIN_PD",
       [n_teams[y] * tsize * P["train_induction_days"] + (first_wave_staff * P["train_refresher_days"] if y else 0) for y in Y],
       "TEAM"))
    A(("10 Training", "A10", "Ground-truth checklist and data training (fire, gold, herd routes, river names)",
       s(P["train_data_pd"]) + " participant-days", "TRAIN_PD", P["train_data_pd"], "TEAM"))
    A(("10 Training", "A10", "Track-count training for ECHO/TANGO with local hunters and herders (replaces camera traps)",
       s(P.get("track_training_pd", [0, 0])) + " participant-days; tracks documented with the teams' smartphones, Yata-Ngaya precedent (TA, Oct 2026)", "TRAIN_PD", list(P.get("track_training_pd", [0, 0])), "TEAM"))
    A(("10 Training", "A8", "Preparatory land-use consultations with the boom towns north-east of the park",
       s(P["landuse_workshop_pd"]) + " participant-days (4 consultations x 25 x 2 days); preparatory, not formal classification workshops", "TRAIN_PD", P["landuse_workshop_pd"], None))
    # ---- 11 Legal
    A(("11 Legal, registration and audit", "A2", "In-country counsel: regulations text, s.24 gazette request, boundary descriptions",
       f"Retainer, {'every month of the project' if legal_m == list(pm_) else 'months'} ({s(legal_m)}). Actions 2 and 3 are the cheapest and most durable acts in the plan", "LEGAL", list(legal_m), None))
    A(("11 Legal, registration and audit", "A4", "NGO/CBO registration, RRC renewal, no-objection letters",
       "Two cycles. Two clocks, both slow - start them in parallel", "REG_NGO", [1] * N, None))
    A(("11 Legal, registration and audit", "A1", "Grant-specific audit near close-out", f"{s(P['audits'])}; NGO Act 2016 s.13", "AUDIT", P["audits"], None))
    # the strands' calendar and ratios (unchanged from the 2-year plan)
    me = sme; fd = P["facilitator_days_per_conservancy"]
    # ---- 12/13/14 foreign strands (separate budgets), one block each
    for k in STRAND_KEYS:
        sb = SB.get(k) or {}
        car = [by[y].get(f"echo_{k.lower()}", 0) for y in Y]; n_car = [by[y].get(f"new_echo_{k.lower()}", 0) for y in Y]
        if not any(car): continue
        car_months = [car[y] * me[y] for y in Y]; car_scout_pm = [car_months[y] * T["scouts"] for y in Y]; car_lead_pm = [car_months[y] * T["leader"] for y in Y]
        car_s = s(car); C = STRAND_CAT[k]; nm = {"CAR": "CAR", "COD": "DRC", "SDN": "Sudan"}[k]; law = sb.get("law", "")
        A((C, "A4", f"{nm}: community wildlife scouts", f"ECHO teams in {nm} {car_s} x {'/'.join(map(str, me))} months (same seasonal contract)", "SAL_SCOUT", car_scout_pm, k))
        A((C, "A4", f"{nm}: scout team leaders", f"One per {nm} team, same months", "SAL_LEAD", car_lead_pm, k))
        A((C, "A4", f"{nm}: food allowance", "Scouts plus leaders", "RATION", [car_scout_pm[y] + car_lead_pm[y] for y in Y], k))
        A((C, "A1", f"{nm}: healthcare contribution", f"All {nm} person-months", "HEALTH_N", [car_scout_pm[y] + car_lead_pm[y] for y in Y], k))
        A((C, "A4", f"{nm}: uniforms and personal kit", f"{tsize} x teams {car_s}", "UNIFORM", [car[y] * tsize for y in Y], k))
        A((C, "A4", f"{nm}: team field kit", f"One per new team ({s(n_car)})", "FIELDKIT", n_car, k))
        A((C, "A10", f"{nm}: rugged smartphones + power banks", "Two per new team", "PHONE", [2 * n for n in n_car], k))
        A((C, "A10", f"{nm}: solar power banks", "One with each phone", "POWER", [2 * n for n in n_car], k))
        A((C, "A4", f"{nm}: motorbikes", f"One per new team ({s(n_car)})", "MBIKE", n_car, k))
        A((C, "A4", f"{nm}: motorbike running costs", f"Team-months: {s(car_months)}", "MBIKE_RUN", car_months, k))
        A((C, "A10", f"{nm}: satellite messengers", f"One per new team ({s(n_car)})", "INREACH", n_car, k))
        A((C, "A10", f"{nm}: satellite messenger subscriptions", "Team-months in the field", "INREACH_SUB", car_months, k))
        A((C, "A6", f"{nm}: community meetings", f"{P['meetings_per_team']} per team per year; teams {car_s}", "MEETING", [car[y] * P["meetings_per_team"] for y in Y], k))
        A((C, "A4", f"{nm}: conservancy facilitation ({law})" if law else f"{nm}: conservancy facilitation", f"{fd} days per new conservancy + {fd} per conservancy per filing year", "FACILITATOR", [n_car[y] * fd + car[y] * fd for y in Y], k))
        A((C, "A4", f"{nm}: conservancy governance seed", "One per conservancy the year after scoping", "CBO_SEED", [0] + n_car[:-1], k))
        A((C, "A4", f"{nm}: scout induction and refresher training", f"{P['train_induction_days']} days per new field staff + {P['train_refresher_days']} refresher", "TRAIN_PD",
           [n_car[y] * tsize * P["train_induction_days"] + (car[y] - n_car[y]) * tsize * P["train_refresher_days"] for y in Y], k))
    return L


# --------------------------------------------------------------------------
def compute(F, P):
    N = len(P["year_labels"]); Y = range(N)
    rate = {r[0]: r[3] for r in RATES}; unit = {r[0]: r[2] for r in RATES}
    a = {k: v for k, _l, v, _s in ASSUMPTIONS}
    a.update(P.get("assumptions") or {})         # a scenario may override a ratio (docs/plan/scenarios.yaml); plan.yaml as it stands sets none
    rows = []
    for cat, act, item, basis, code, qty, drv in lines(F, P):
        tot = [q * rate[code] for q in qty]
        rows.append(dict(cat=cat, act=act, item=item, basis=basis, code=code, unit=unit[code],
                         rate=rate[code], qty=qty, tot=tot, drv=drv))
    def stack(direct, goods):
        freight = goods * a["FREIGHT_PCT"]; bank = (direct + freight) * a["BANK_PCT"]
        sub = direct + freight + bank; supp = sub * a["SUPPORT_PCT"]; cont = (sub + supp) * a["CONTING_PCT"]
        return dict(direct=direct, freight=freight, bank=bank, sub=sub, supp=supp, cont=cont, total=sub + supp + cont)
    per_year = [stack(sum(r["tot"][y] for r in rows), sum(r["tot"][y] for r in rows if is_goods(r))) for y in Y]
    t = {k: [p[k] for p in per_year] for k in per_year[0]}
    t["a"] = a; t["N"] = N
    t["load"] = t["total"][0] / t["direct"][0] if t["direct"][0] else 1.0
    t["h1"] = per_year[0]          # 2026-10: the "first six months" ARE Phase 1 - no separate tranche
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
    t["oneoffs"] = [sum(r["tot"][y] for r in rows if any(w in r["item"].lower() for w in ("survey", "solar pump", "water point", "savannah", "helicopter", "veterinary"))) for y in Y]
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
    WD = P["wave_dates"]
    N = len(P["year_labels"]); mp = P["months_paid"]; mf = P.get("months_first_phase") or mp
    sme = P.get("strand_months_paid") or mp["echo"]
    def months(kind, join, y):   # months a unit deployed in phase `join` (1-based) works in phase y; 1 in the set-up phase if it is
        if join - 1 > y: return 1 if y == 0 else 0        # being recruited and equipped there without yet being in the field
        m = mf[kind][y] if join - 1 == y else mp[kind][y]
        return m or (1 if y == 0 else 0)
    out = []
    for tid, t in sorted(F["deploy"]["teams"].items()):
        kind, strand = t["kind"], t.get("strand", "SSD")
        if kind == "FP": drv, w = "FP", ([0] * N if t.get("pm") else [months("fp", t["year"], y) for y in range(N)])   # the PM-as-FP is costed in the PM lines
        elif strand != "SSD": drv, w = strand, [sme[y] if t["year"] <= y + 1 else 0 for y in range(N)]
        else: drv, w = "TEAM", [months(kind.lower(), t["year"], y) for y in range(N)]
        out.append(dict(id=tid, place=t["place"], kind=kind, drv=drv, country=t.get("country") or {"CAR": "CAF", "COD": "COD", "SDN": "SDN"}.get(strand, "SSD"), year=t["year"], w=w))
    out.append(dict(id="HQ", place="Chinko HQ", kind="HQ", drv="HQ", country="CAF", year=1, w=[1] * N))
    # The FP delivery unit is "focal points AND the Wau/Juba backbone": coordinator, finance officer, the national
    # partner's grant, the Wau compound, Juba flights. Only the focal points' own salaries, desks and laptops belong to
    # a town, so the regional row takes twice the towns' combined weight (2/3 of every FP line) - editable on the sheet.
    fp_w = [sum(o["w"][y] for o in out if o["drv"] == "FP") for y in range(N)]
    out.append(dict(id="REG", place="Regional overhead (Wau/Juba backbone)", kind="REGION", drv="FP", country="SSD", year=1, w=[2 * x for x in fp_w]))
    # Programme-wide lines (survey, flights, counsel, audit, constitution facilitation) carry no delivery unit and land here too.
    out.append(dict(id="REG", place="Regional overhead (Wau/Juba backbone)", kind="REGION", drv="NONE", country="SSD", year=1, w=[1] * N))
    return out


def build_xlsx(path, F, P):
    from openpyxl.worksheet.datavalidation import DataValidation
    rows, t = compute(F, P); N = t["N"]; a = t["a"]; WD = P["wave_dates"]
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
    # ---- Budget: THE line table. Editable: rate code (dropdown), quantities, delivery unit (dropdown).
    PH = P.get("phase_names") or [f"Year {y+1}" for y in range(N)]; HM = P.get("horizon_months") or 12 * N
    wb_ = wb.create_sheet("Budget")
    wb_["A1"] = f"EASY / AP-RCA WESTERN SOUTH SUDAN - {HM}-MONTH BUDGET ({P['year_labels'][0].split(' - ')[0]} - {P['year_labels'][-1].split(' - ')[-1]}), two phases"
    wb_["A1"].font = Font(bold=True, size=13)
    wb_["A2"] = ("THE SHEET TO EDIT (with Rates and Assumptions). Yellow cells: pick a rate code from the dropdown, type a quantity, "
                 "pick a delivery unit. Unit cost, totals, the Allocation sheet and every pivot follow by formula; pivots refresh on opening "
                 "(or Data > Refresh All). Quantities are derived from the deployment (teams per phase) and docs/plan/plan.yaml. "
                 "Reviewed 18-month phasing adopted 2026-10 (ET input, 16 Sep 2026): Phase 1 sets up, Phase 2 is the only field season.")
    wb_["A2"].font = Font(italic=True, size=9)
    hdr = ["Category", "Plan action", "Line item", "Basis / quantity logic", "Rate code", "Unit", "Unit cost USD"]
    for y in range(N): hdr += [f"Qty {PH[y]} {P['year_labels'][y]}", f"Total {PH[y]}"]
    hdr += [f"Total {HM} months", "Delivery unit", "helper: goods flag (freight base)"]
    wb_.append([]); wb_.append(hdr); style_header(wb_, 4, len(hdr))
    qcol = [get_column_letter(8 + 2 * y) for y in range(N)]; tcol = [get_column_letter(9 + 2 * y) for y in range(N)]
    TOT = get_column_letter(8 + 2 * N); DRV = get_column_letter(9 + 2 * N)
    GOODS = get_column_letter(10 + 2 * N)          # helper column, last and hidden: the user never edits it
    wb_.column_dimensions[GOODS].hidden = True
    first = 5
    goods_cats = ",".join(f'"{g}"' for g in GOODS_CATS); goods_codes = ",".join(f'"{g}"' for g in GOODS_CODES)
    nofreight = ",".join(f'"{g}"' for g in NO_FREIGHT_CODES)
    for cat, act, item, basis, code, qty, drv in lines(F, P):
        row = [cat, ACTIONS[act], item, basis, code, "", ""]
        for y in range(N): row += [qty[y], ""]
        row += ["", drv or "NONE", ""]
        wb_.append(row); r = wb_.max_row
        wb_.cell(row=r, column=5).fill = EDIT
        wb_.cell(row=r, column=6).value = f'=VLOOKUP($E{r},Rates!$A${rate_first}:$E${rate_last},3,FALSE)'
        wb_.cell(row=r, column=7).value = f'=VLOOKUP($E{r},Rates!$A${rate_first}:$D${rate_last},4,FALSE)'; wb_.cell(row=r, column=7).number_format = MONEY
        # goods flag as the sheet's own formula: equipment/transport categories, or an imported-goods code inside a strand category,
        # except the codes that are not imports (delivered-price motorbike, insurance)
        strand_cats = ",".join(f'"{c}"' for c in STRAND_CAT.values())
        wb_[f"{GOODS}{r}"] = (f'=IF(AND(NOT(ISNUMBER(MATCH($E{r},{{{nofreight}}},0))),OR(ISNUMBER(MATCH($A{r},{{{goods_cats}}},0)),AND(ISNUMBER(MATCH($A{r},{{{strand_cats}}},0)),'
                              f'ISNUMBER(MATCH($E{r},{{{goods_codes}}},0))))),1,0)')
        for y in range(N):
            wb_[f"{qcol[y]}{r}"].fill = EDIT
            wb_[f"{tcol[y]}{r}"] = f"=$G{r}*{qcol[y]}{r}"; wb_[f"{tcol[y]}{r}"].number_format = MONEY
        wb_[f"{TOT}{r}"] = "=" + "+".join(f"{c}{r}" for c in tcol); wb_[f"{TOT}{r}"].number_format = MONEY
        wb_[f"{DRV}{r}"].fill = EDIT
        for c in (3, 4): wb_.cell(row=r, column=c).alignment = Alignment(wrap_text=True, vertical="top")
    last = wb_.max_row
    dv_rate = DataValidation(type="list", formula1=f"=Rates!$A${rate_first}:$A${rate_last}", allow_blank=False); wb_.add_data_validation(dv_rate)
    dv_rate.add(f"E{first}:E{last}")
    dv_drv = DataValidation(type="list", formula1='"TEAM,FP,HQ,' + ",".join(STRAND_KEYS) + ',NONE"', allow_blank=False); wb_.add_data_validation(dv_drv)
    dv_drv.add(f"{DRV}{first}:{DRV}{last}")
    cols = tcol + [TOT]
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
    for c in qcol + tcol + [TOT]: wb_.column_dimensions[c].width = 12
    wb_.column_dimensions[DRV].width = 13
    for r in range(first, last + 1): wb_.row_dimensions[r].height = 30
    wb_.freeze_panes = "C5"
    # ---- Locations: where the plan puts people (from facts), with months active per year as the allocation weight
    locs = _locations(F, P)
    wl = wb.create_sheet("Locations"); wl["A1"] = "LOCATIONS - every site the plan staffs, from the deployment; weights split each budget line across its delivery unit's sites"
    wl["A1"].font = Font(bold=True, size=12)
    wl["A2"] = ("Weight = months active in the phase (a team being recruited and equipped in Phase 1 counts 1; Chinko HQ 1); the regional row under FP is twice the towns' combined weight, so "
                "two thirds of the backbone lines (coordinator, partner grant, Wau compound, Juba travel) stay regional. Edit a weight to move money "
                "between sites; a site with 0 in a phase gets nothing that phase."); wl["A2"].font = Font(italic=True, size=9)
    wl.append([]); wl.append(["Team", "Place", "Kind", "Delivery unit", "Country", f"Wave (1 = {WD[0]}, 2 = {WD[1]})"] + [f"Weight {PH[y]}" for y in range(N)]); style_header(wl, 4, 6 + N)
    loc_first = 5
    for L_ in locs:
        wl.append([L_["id"], L_["place"], L_["kind"], L_["drv"], L_["country"], L_["year"]] + L_["w"]); r = wl.max_row
        for y in range(N): wl.cell(row=r, column=7 + y).fill = EDIT
    loc_last = wl.max_row
    wl.append([]); wl.append(["Weight sums by delivery unit"]); wl.cell(row=wl.max_row, column=1).font = Font(bold=True)
    wsum_row = {}
    for key in ("TEAM", "FP", "HQ", "NONE") + STRAND_KEYS:
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
    ahdr = ["Category", "Plan action", "Line item", "Delivery unit", "Strand", "Team", "Location", "Country", "Phase", "Direct USD", "Loaded USD"]
    wal.append([]); wal.append(ahdr); style_header(wal, 3, len(ahdr)); a_hdr_row = 3
    strand_name = {"CAR": "CAR conservancies (separate budget)", "COD": "DRC conservancies (separate budget)", "SDN": "Sudan conservancies (separate budget)"}
    line_rows = list(range(first, last + 1)); line_vals = rows
    def load_formula(r_b):
        return (f"(1+Budget!${GOODS}${r_b}*{keycell['FREIGHT_PCT']})*(1+{keycell['BANK_PCT']})"
                f"*(1+{keycell['SUPPORT_PCT']})*(1+{keycell['CONTING_PCT']})")
    arows = []
    for r_b, rv in zip(line_rows, line_vals):
        drv = rv["drv"] or "NONE"
        targets = [(i + loc_first, L_) for i, L_ in enumerate(locs) if L_["drv"] == drv] or [(None, dict(id="-", place="Unallocated", country="SSD"))]
        strand = strand_name.get(drv, "South Sudan request")
        for y in range(N):
            for r_l, L_ in targets:
                wal.append([f"=Budget!$A${r_b}", f"=Budget!$B${r_b}", f"=Budget!$C${r_b}", f"=Budget!${DRV}${r_b}", strand,
                            L_["id"], L_["place"], L_["country"], PH[y], "", ""]); r = wal.max_row
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
                arows.append(dict(zip(ahdr, [rv["cat"], ACTIONS[rv["act"]], rv["item"], drv, strand, L_["id"], L_["place"], L_["country"], PH[y], direct, direct * load])))
    for col, w in zip("ABCDEFGHIJK", (26, 34, 44, 12, 34, 8, 26, 8, 6, 14, 14)): wal.column_dimensions[col].width = w
    wal.sheet_properties.tabColor = "BBBBBB"
    wal.freeze_panes = "A4"
    # ---- Pivots (real pivot tables over Allocation; refresh on load)
    cache, phdr, pshared = _pivot_cache(wal, a_hdr_row, len(ahdr), arows)
    def pivot_sheet(title, caption, row_field, name, data_fields=("Direct USD", "Loaded USD"), col_field="Phase"):
        p = wb.create_sheet(title); p["A1"] = caption; p["A1"].font = Font(bold=True, size=12)
        p["A2"] = "Pivot table over the Allocation sheet - refreshes when the file is opened (Excel: Data > Refresh All if not)."; p["A2"].font = Font(italic=True, size=9)
        _pivot(cache, phdr, pshared, p, ("A", 4), row_field, list(data_fields), name, col_field=col_field)
        p.column_dimensions["A"].width = 44
        for c in "BCDEFGHIJ": p.column_dimensions[c].width = 16
        return p
    pivot_sheet("Summary", "SUMMARY BY COST CATEGORY, direct and loaded, by phase", "Category", "PivotCategory")
    pivot_sheet("By action", "COST BY PLAN ACTION", "Plan action", "PivotAction")
    pivot_sheet("By location", "COST PER LOCATION (as per the plan's team sites; lines for 'all teams' split by months active)", "Location", "PivotLocation")
    pivot_sheet("By strand", "COST BY BUDGET STRAND (South Sudan request vs the separate CAR / DRC / Sudan conservancy budgets)", "Strand", "PivotStrand")
    pivot_sheet("By delivery unit", "LOADED COST OF EACH DELIVERY UNIT (teams, focal points and backbone, Chinko HQ, strands)", "Delivery unit", "PivotUnit")
    # ---- AP format (review C9, EM C6 / Eric Tamko): the static summary layout of reports/review_v2_sources/AP_budget_template_ET_input.xlsx
    #      (Row Labels | Phase 1 direct, loaded | Phase 2 direct, loaded | Grand Total direct, loaded), SUMIFS over Allocation - no pivot to refresh,
    #      so it reads in any viewer. Four blocks on one sheet: category, strand, delivery unit, plan action. Loaded = TOTAL REQUESTED.
    AP_SHEET = "AP format (ET)"
    ap = wb.create_sheet(AP_SHEET); a_last = wal.max_row
    ap["A1"] = f"AP BUDGET FORMAT (ET input, Oct 2026) - direct and loaded USD by phase; static SUMIFS over the Allocation sheet, {HM} months"; ap["A1"].font = Font(bold=True, size=12)
    ap["A2"] = "Loaded = direct + freight on goods + bank + Chinko HQ/Juba desk + contingency; the loaded grand total equals TOTAL REQUESTED on the Budget sheet. Edit nothing here."; ap["A2"].font = Font(italic=True, size=9)
    def ap_block(title, field_col, labels):
        ap.append([]); r0 = ap.max_row + 1
        ap.append([title] + [x for y in range(N) for x in (PH[y], "")] + ["Grand Total", "Grand Total"]); ap.cell(row=r0, column=1).font = Font(bold=True)
        ap.append(["Row Labels"] + [x for _ in range(N) for x in ("Sum of Direct USD", "Sum of Loaded USD")] + ["Grand Total (Sum of Direct USD)", "Grand Total (Sum of Loaded USD)"])
        style_header(ap, r0 + 1, 2 * N + 3); first = ap.max_row + 1
        for lab in labels:
            r = ap.max_row + 1; cells = [lab]
            for y in range(N):
                crit = f'Allocation!${field_col}$4:${field_col}${a_last},A{r},Allocation!$I$4:$I${a_last},"{PH[y]}"'
                cells += [f"=SUMIFS(Allocation!$J$4:$J${a_last},{crit})", f"=SUMIFS(Allocation!$K$4:$K${a_last},{crit})"]
            dcols = [get_column_letter(2 + 2 * y) for y in range(N)]; lcols = [get_column_letter(3 + 2 * y) for y in range(N)]
            cells += ["=" + "+".join(f"{c}{r}" for c in dcols), "=" + "+".join(f"{c}{r}" for c in lcols)]
            ap.append(cells)
        last = ap.max_row; r = last + 1
        ap.append(["Total Result"] + [f"=SUM({get_column_letter(c)}{first}:{get_column_letter(c)}{last})" for c in range(2, 2 * N + 4)])
        for c in range(1, 2 * N + 4): ap.cell(row=r, column=c).font = Font(bold=True)
        for rr in range(first, r + 1):
            for c in range(2, 2 * N + 4): ap.cell(row=rr, column=c).number_format = MONEY
    cats = list(dict.fromkeys(rv["cat"] for rv in rows))
    ap_block("By cost category", "A", cats)
    ap_block("By budget strand", "E", ["South Sudan request"] + [strand_name[k] for k in STRAND_KEYS])
    ap_block("By delivery unit", "D", ["HQ", "FP", "TEAM", "NONE"] + list(STRAND_KEYS))
    ap_block("By plan action", "B", list(ACTIONS.values()))
    ap.column_dimensions["A"].width = 44
    for c in "BCDEFGH": ap.column_dimensions[c].width = 18
    ap.sheet_properties.tabColor = "1F4E79"
    # ---- Phasing (plain formulae: Phase 1 = set-up in the wet season, Phase 2 = the field season; Phase 1 is also the first six months)
    ws7 = wb.create_sheet("Phasing"); ws7["A1"] = f"PHASING ON THE {HM}-MONTH CALENDAR (Phase 1 = set-up, no field teams; Phase 2 = the one field season)"
    ws7["A1"].font = Font(bold=True, size=12); ws7.append([]); ws7.append(["Period", "Direct", "Loaded"]); style_header(ws7, 3, 3)
    for y in range(N):
        ws7.append([f"{PH[y]} ({P['year_labels'][y]})", f"=Budget!${tcol[y]}${r_direct}", f"=Budget!${tcol[y]}${r_tot}"])
    ws7.append([f"Total {HM} months", f"=Budget!${TOT}${r_direct}", f"=Budget!${TOT}${r_tot}"])
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
    rows, t = compute(F, P); N = t["N"]; Y = range(N); a = t["a"]; by = F["deploy"]["by_year"]; d = derive(F, P)
    tot = sum(t["total"]); o = []; Pp = o.append
    yl = P["year_labels"]; PH = P.get("phase_names") or [f"Year {y+1}" for y in range(N)]; HM = P.get("horizon_months") or 12 * N
    pm_ = d["pm_"]; PHU = [f"PHASE {y+1}" for y in Y]; PHM = [f"({pm_[y]} mo)" for y in Y]
    PHL = [f"Phase {y+1} ({yl[y]}, {pm_[y]} months)" for y in Y]; WD, WDL = P["wave_dates"], P["wave_dates_long"]
    def row(label, vals, total=True):
        Pp(f"    {label:38s}" + "".join(f" {m(v):>12s}" for v in vals) + (f" {m(sum(vals)):>12s}" if total else ""))
    def hdr_row(last):
        Pp(f"    {'':38s}" + "".join(f" {h:>12s}" for h in PHU) + f" {last:>12s}")
        Pp(f"    {'':38s}" + "".join(f" {h:>12s}" for h in PHM) + f" {f'({HM} mo)':>12s}")
    def q(x): return f"{x:g}" if float(x).is_integer() else f"{x:.2f}"
    Pp("=" * W); Pp("")
    Pp("        WHAT IT COSTS")
    Pp(f"        An {HM}-month budget for the EASY priority intervention, western South Sudan")
    Pp(f"        - lean, partner-delivered, seasonal. Phase 1 ({yl[0]}) sets up;")
    Pp(f"        Phase 2 ({yl[1]}) is the field season. Reviewed phasing (ET input,")
    Pp("        16 Sep 2026) adopted. Companion to the PIP summary.")
    Pp(f"        {datetime.date.today():%B %Y} - INTERNAL. Live workbook: {xlsx_name}")
    Pp(""); Pp("=" * W); Pp(""); Pp("")
    Pp("THE BUDGET IN TEN LINES"); Pp("-" * W)
    natl = sum(sum(t["by_cat"][c]) for c in ("2 National staff", "3 Local partners and SSWS"))
    staff = " -> ".join(str(b["staff"]) for b in by)
    Pp(f"  1  {HM}-month total        USD {m(tot)}  ({' / '.join(m(v) for v in t['total'])} by phase)")
    if any(any(t["strand"][k]["total"]) for k in STRAND_KEYS):
        for k in STRAND_KEYS:
            if any(t["strand"][k]["total"]):
                Pp(f"     of which {k} strand   USD {m(sum(t['strand'][k]['total']))}  ({' / '.join(m(v) for v in t['strand'][k]['total'])}) - a SEPARATE budget")
        Pp(f"     South Sudan request   USD {m(sum(t['ssd_total']))}  ({' / '.join(m(v) for v in t['ssd_total'])})")
    Pp("  2  Shape                 No new organisation. A national partner implements,")
    Pp("                           SSWS leads visibly, Chinko HQ oversees, AP South")
    Pp("                           Sudan carries Juba.")
    WK = P.get("wave_dates_by_kind") or {k: WD for k in ("echo", "tango", "fp")}
    Pp(f"  3  People on the ground  {staff} field staff recruited by wave; none in the field in Phase 1 (wet season).")
    fp0 = f"{by[0]['fp']} FP" if by[0]['fp'] else ("the PM as Wau FP" if d["pm_fp"][0] else "0 FP")
    Pp(f"                           All {by[0]['tango']} TANGO + {fp0} from {WK['tango'][0]} (before the herds), {by[0]['echo']} ECHO from {WK['echo'][0]};")
    Pp(f"                           all {by[-1]['echo']} ECHO + {by[-1]['tango']} TANGO + {by[-1]['fp']} paid FP from {WK['echo'][1]}: {d['team_months'][-1]:g} team-months, all in Phase 2.")
    for k in STRAND_KEYS:
        if any(b.get(f"echo_{k.lower()}") for b in by):
            Pp(f"                           {k} strand, separately: " + "; ".join(f"P{y+1} {by[y].get(f'echo_{k.lower()}', 0)} ECHO ({by[y].get(f'staff_{k.lower()}', 0)} staff)" for y in Y) + ".")
    Pp("                           A team is 4 scouts + 1 leader; a focal point one person.")
    Pp(f"  4  Money on the ground   {natl/tot:.0%} of the total is national staff, partner")
    Pp("                           grants and SSWS support.")
    Pp(f"  5  Seasonal payroll      Teams are paid from deployment to Nov 2028 ({P['months_paid']['tango'][1]} months TANGO, {P['months_paid']['echo'][1]} for")
    Pp(f"                           first-wave ECHO, {P['months_first_phase']['echo'][1]} for the second), not from the start of the grant.")
    _a6 = [r for r in rows if r["act"] == "A6" and r["drv"] not in STRAND_KEYS and sum(r["tot"])]
    _a6_kinds = ", ".join(sorted({"water" if "pump" in r["item"].lower() or "water" in r["item"].lower() else "veterinary" if "veterinar" in r["item"].lower() else "meetings" for r in _a6}))
    Pp(f"  6  The corridor's price  {_a6_kinds.capitalize()}: USD {m(t['corridor_price'])} -")
    if _a6_kinds == "meetings":
        Pp("                           the herders named water and veterinary care too; those are deferred")
        Pp("                           with the scope (review C6/C7) and costed again in the Jul 2028 business plan.")
    else:
        Pp("                           the herders named it, so it is a line, not an intention.")
    Pp(f"  7  One expatriate        A full-time Project Manager in South Sudan from {d['pm_start']} ({sum(d['pm_pm'])} months,")
    Pp(f"                           also the Wau focal point), reporting to Chinko; {sum(t['by_cat']['1 International staff'])/tot:.0%} of the budget.")
    Pp(f"  8  Freight is {a['FREIGHT_PCT']:.0%}        Of everything imported. A line, not a rounding error.")
    Pp(f"  9  Support is a rate     {a['SUPPORT_PCT']:.0%} for Chinko HQ and the Juba desk - services")
    Pp("                           consumed, not a second structure.")
    Pp(" 10  Actions 3, 5 and 11   Cost nothing but staff time, already paid for in the")
    Pp("                           project manager's salary and the legal retainer.")
    Pp(""); Pp("")
    Pp("THE OPERATION THIS BUDGET PAYS FOR"); Pp("-" * W)
    for para in [
        "Four organisations already exist on or beside this ground, and the budget's first decision is to use them. "
        "A registered national NGO with offices in Wau and Raja implements: premises, mobilisers, and it is already inside "
        "the NGO Act's 80%-national staffing rule. The South Sudan Wildlife Service leads in public and on the ground; we "
        "equip its existing but unequipped posts and pay allowances for joint missions, never a government salary. African "
        "Parks leads the feasibility phase itself: one full-time Project Manager / National Coordinator based in Wau runs it and reports to the "
        "Landscape Coordinator at Chinko. African Parks South Sudan carries Juba: banking, approvals, representation and the "
        "half-time finance officer.",
        f"What that leaves us to pay for directly is small and deliberate: the Project Manager / National Coordinator, "
        f"{by[-1]['fp'] + sum(1 for x in F['deploy']['fp_places'] if 'PM' in x)} focal points ({', '.join(F['deploy']['fp_places'])}" + ("; the PM holds the Wau desk, so one is paid" if any(d["pm_fp"]) and by[-1]['fp'] == 1 else (f"; the PM holds the Wau desk, so {by[-1]['fp']} are paid" if any(d["pm_fp"]) else "")) + "), "
        f"{by[-1]['echo']+by[-1]['tango']} seasonal scout teams, and the activities themselves. "
        "There is no vehicle fleet - 4x4s are hired by the day. No Juba office. No headquarters. The heaviest single asset is a motorbike.",
        f"The calendar does the rest. The project starts on 1 June 2027, in the wet season, and {PHL[0]} is set-up only: "
        f"recruit, equip, train, position the avgas, run the paper track from Juba. The herds arrive in November, so "
        f"{PHL[1]} is the one field season: the TANGO corridor teams deploy in {WDL[0]}, before the herds, the ECHO conservancy teams in "
        f"{P.get('wave_dates_by_kind', {}).get('echo', WD)[0]} and {WDL[1]}, and all are paid to the end of November 2028.",
    ]:
        Pp(wrap(para)); Pp("")
    Pp(""); Pp("THE HEADLINE"); Pp("-" * W); Pp("")
    hdr_row("TOTAL")
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
    _oneoff_names = []
    for r in rows:
        il = r["item"].lower()
        if sum(r["tot"]) and any(w in il for w in ("survey", "solar pump", "water point", "savannah", "helicopter", "veterinary")):
            _oneoff_names.append("Savannah hours" if "savannah" in il else "helicopter hours" if "helicopter" in il or "r44" in il
                                 else "the survey package" if "survey" in il else "the solar pump" if "pump" in il else "the veterinary campaign")
    _oneoff_names = list(dict.fromkeys(_oneoff_names))
    Pp(wrap(f"{PHL[1]} carries the field season and the one-offs ({', '.join(_oneoff_names)}: USD {m(t['oneoffs'][1])} direct) "
            f"and the full deployment of {by[-1]['staff']} field staff; {PH[0]} buys the kit, "
            "the recruitment and the training before anyone deploys."))
    Pp(""); Pp("")
    Pp("PHASING ON THE PLAN'S OWN CLOCK"); Pp("-" * W)
    Pp(wrap(f"The FIELD clock opens in {WDL[0]}; the PAPER clock runs from the first day, from Juba. Phase 1 is the first {pm_[0]} months "
            "of the project, so there is no separate set-up tranche: the two rows below add up to the total."))
    Pp(""); Pp(f"    {'PERIOD':38s} {'DIRECT':>12s} {'LOADED':>12s} {'CUMULATIVE':>12s}"); Pp("    " + "-" * (W - 8))
    cum = 0
    for y in Y:
        cum += t["total"][y]
        Pp(f"    {f'{PH[y]}  ' + yl[y]:38s} {m(t['direct'][y]):>12s} {m(t['total'][y]):>12s} {m(cum):>12s}")
    Pp("")
    h = t["h1"]
    Pp(wrap(f"{PHL[0]} is USD {m(h['total'])} loaded - {h['total']/tot:.0%} of the total - and is committed before anyone knows "
            "whether the field season deploys: the Project Manager / National Coordinator in post, the paper track in Juba (legal retainer, "
            "registration, the regulation text and the s.24 gazette request), the partner grant's first third, SSWS posts equipped, "
            "every team recruited, trained and kitted, the avgas positioned, the security assessment done. "
            f"The gate sits before it: no written SSWS endorsement by {P['gate_date']} and the project does not launch on 1 June 2027."))
    Pp(""); Pp("")
    Pp("WHAT A TEAM AND A FOCAL POINT ACTUALLY COST - LOADED"); Pp("-" * W)
    Pp(wrap(f"LOADED cost adds each unit's share of freight, bank charges, HQ/Juba support and contingency - a factor of "
            f"{t['load']:.2f} on Phase 1. These are the numbers to quote when somebody asks what one team costs to have."))
    Pp(""); d = t["drv"]
    hdr_row("TOTAL"); Pp("    " + "-" * (W - 8))
    for key, label in (("TEAM", "ECHO/TANGO teams, all (South Sudan)"), ("FP", "Focal points + Wau/Juba backbone"), ("HQ", "Chinko HQ oversight"),
                       *[(k, f"{ {'CAR': 'CAR', 'COD': 'DRC', 'SDN': 'Sudan'}[k] } conservancy teams (separate budget)") for k in STRAND_KEYS]):
        if key in STRAND_KEYS and not any(d[key]["loaded"]): continue
        row(label, d[key]["loaded"])
    Pp("")
    yf = N - 1; nt = by[yf]["echo"] + by[yf]["tango"]; tm = sum(d["TEAM"]["loaded"]); fp = sum(d["FP"]["loaded"])
    Pp(f"    PER UNIT, WHOLE PROJECT ({nt} teams, {by[yf]['fp']} focal points; Phase 1 kit and training included)")
    Pp(f"      One ECHO/TANGO team, {P['team']['scouts']} scouts + {P['team']['leader']} leader, one season   {m(tm/nt):>10s}")
    _bb = f"Wau/Juba backbone incl. {by[yf]['fp']} paid focal point{'s' if by[yf]['fp'] != 1 else ''}, whole project"
    Pp(f"      {_bb:<62s}{m(fp):>10s}")   # whole, not per FP: with one paid FP a per-unit figure misreads as the FP's own cost (5MP, 16 Oct 2026)
    Pp(f"      PM and Chinko HQ oversight, {HM} months                         {m(sum(d['HQ']['loaded'])):>10s}")
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
        Pp(""); Pp(f"  {cat.upper()}   ({HM} mo {m(sum(t['by_cat'][cat]))})"); Pp("  " + "-" * (W - 4))
        for r in sel:
            Pp(f"  {r['item']}"); Pp(wrap(r["basis"], indent="      "))
            Pp(f"      {m(r['rate']) if r['rate'] >= 1 else f'{r['rate']:.0%}'}/{r['unit']}   "
               + "   ".join(f"P{y+1} {q(r['qty'][y])} = {m(r['tot'][y]):>9s}" for y in Y))
    Pp(""); Pp("")
    Pp("WHAT EACH PLAN ACTION COSTS"); Pp("-" * W); Pp("")
    for key in sorted(ACTIONS, key=lambda k: int(k[1:])):
        Pp(f"    {ACTIONS[key]:44s} {m(t['by_action'][key]):>12s}")
    Pp("")
    Pp(wrap("Actions 3, 5 and 11 carry no line of their own: the s.24 gazette request, the road finding and the funding "
            "conversations are signatures, letters and phone calls, paid for inside the project manager's salary and the legal retainer."))
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
        ("An aircraft", f"{sum(P['savannah_hours']):g} Savannah hours and {sum(P['heli_hours']):g} helicopter hours in one dry season do not justify an airframe. Hours are flown on African Parks' own aircraft at dry rates, fuel is positioned; no aerial-survey package is bought."),
        ("A Juba office", "There is a functioning one already, belonging to a partner in the same group."),
        ("A resident expatriate structure", "One Project Manager in country, reporting to Chinko; no second expatriate post. This landscape's bottleneck has never been technical knowledge."),
        ("Road rebuilding", "Reopening the 1932 alignment is a separate capital project with its own EIA."),
        ("Enforcement hardware", "No weapons, no interception vehicles, no detention infrastructure. The posture is awareness first, seizure last."),
        ("Twelve-month scout contracts", "The audience is seasonal. Paying a full year for a two-month window is how money disappears here."),
    ]:
        Pp(f"  * {item.upper()}"); Pp(wrap(why, indent="    "))
    Pp(""); Pp("")
    Pp("WHAT WOULD MAKE THIS BUDGET WRONG"); Pp("-" * W)
    for item, why in [
        (f"No SSWS endorsement by {P['gate_date']}", "Then the project does not launch on 1 June 2027 and nothing in Phase 1 is spent."),
        ("A lost season", f"This project has ONE field season ({yl[1]}) in a state whose conflict record is quiet with bad days. A cancelled season cannot be pushed right inside 18 months; that is what the contingency is for, and why the hand-over decision in Nov 2028 matters."),
        ("A mineral title issued on the rim", "Then the second wave stops expanding and spends itself on that single problem."),
        ("The aviation rates", "150/h for the Savannah and 700/h for the R44 sit far below this catalogue's own benchmark (about 465 and 1,860). If the operator's real rate applies, the aviation lines roughly triple."),
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
    """The numbers other documents quote (into facts.json). per_year / ssd_per_year etc. are PER PHASE since 2026-10."""
    rows, t = compute(F, P); d = derive(F, P); N = t["N"]; Y = range(N); WD = P["wave_dates"]
    ssd = [r for r in rows if r["drv"] not in STRAND_KEYS]
    def phase_of(qty): return next((y + 1 for y in Y if qty[y]), None)
    equipment = [dict(item=r["item"], code=r["code"], qty=round(sum(r["qty"])), unit_cost=r["rate"], total=round(sum(r["tot"])), phase=phase_of(r["qty"]),
                      freight=is_goods(r)) for r in ssd if r["code"] in EQUIPMENT_CODES and sum(r["qty"])]
    T = P["team"]; HM = P.get("horizon_months") or 12 * N
    headcount = []
    def hc(role, n, months, phase, basis): headcount.append(dict(role=role, n=n, months=months, phase=phase, basis=basis))
    hc("Project Manager / National Coordinator (international, based in Wau)", 1, d["pm_pm"], "both", f"one post; full-time from {d['pm_start']}: " + (f"{d['pm_pre']} pre-launch months (booked to Phase 1) + {sum(P['pm_months'])} = " if d['pm_pre'] else "") + f"{sum(d['pm_pm'])} months at USD {RATE['SAL_INTL']:,} all-in; also the Wau focal point; reports to the Chinko Landscape Coordinator")
    if any(d["coord_pm"]): hc("National project coordinator, Juba/Wau", 1, d["coord_pm"], "both", f"all {sum(d['coord_pm'])} months")
    hc("Finance/admin officer (0.5 FTE, AP South Sudan, Juba)", 1, d["fin_pm"], "both", f"{sum(d['fin_pm'])} FTE-months")
    hc("Focal points (one person each)", d["fp"][-1], d["fp_pm"], "both", f"{d['fp'][0]} from the start, {d['fp'][-1]} from {WD[1]}: {sum(d['fp_pm'])} person-months" + ("; the Project Manager is the Wau focal point (costed above)" if any(d["pm_fp"]) else ""))
    hc("Community wildlife scouts", d["teams"][-1] * T["scouts"], d["scout_pm"], 2, f"{T['scouts']} per team x {d['team_months'][-1]:g} team-months, Phase 2 only")
    hc("Scout team leaders", d["teams"][-1] * T["leader"], d["lead_pm"], 2, f"one per team, {d['team_months'][-1]:g} team-months, Phase 2 only")
    AV = {"SAV_HR": ("Savannah STOL (light aircraft)", "surveillance, livestock counts and rim reconnaissance, several flights over the 2027-28 dry season"),
          "HELI_HR": ("R44 helicopter", "operational support"), "AVGAS_BBL": (None, "fuel for the above: the aircraft rates are dry (EM C4)"),
          "SURVEY_TECH": ("Survey technical package", "design, observers, analysis; flown within the Savannah hours"), "FLY_HR": ("Light aircraft, wet", "reconnaissance / survey")}
    def _craft(r):   # AVGAS: the label names the barrels per phase (emergency reserve in Phase 1, operational fuel for the flying in Phase 2), never typed
        if AV[r["code"]][0] is not None: return AV[r["code"]][0]
        q = [round(v) for v in r["qty"]]
        return f"Avgas ({q[0]} emergency barrels positioned in Phase 1" + (f" + {q[1]} barrels of operational fuel for the flying hours in Phase 2" if len(q) > 1 and q[1] else "") + ")"
    av_all = [dict(craft=_craft(r), code=r["code"], hours=round(sum(r["qty"])) if r["unit"] == "hour" else None, qty=round(sum(r["qty"])), unit=r["unit"],
                   rate=r["rate"], total=round(sum(r["tot"])), phase=phase_of(r["qty"]), purpose=AV[r["code"]][1]) for r in ssd if r["code"] in AV and sum(r["qty"])]
    aviation = [a for a in av_all if a["hours"] is not None]            # the aircraft: every entry has hours
    aviation_support = [a for a in av_all if a["hours"] is None]        # survey package, avgas: no hours
    return dict(years=t["N"], horizon_months=HM, phase_months=list(d["pm_"]), phase_names=P.get("phase_names"),
                team_months=[round(v) for v in d["team_months"]], fp_months=[round(v) for v in d["fp_pm"]], national_person_months=[round(v) for v in d["natl_pm"]],
                equipment=equipment, headcount=headcount, aviation=aviation, aviation_support=aviation_support,
                aviation_total=round(sum(a["total"] for a in av_all)), pm_prelaunch_months=d["pm_pre"], pm_months=list(d["pm_pm"]),
                gap_fill_total=round(sum(sum(r["tot"]) for r in ssd if r["code"].startswith("GAP_") or r["code"] == "AVGAS_BBL")),
                total=round(sum(t["total"])), per_year=[round(v) for v in t["total"]],
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
    ap.add_argument("--xlsx", default="reports/BUDGET_EASY_2026-10.xlsx")
    ap.add_argument("--txt", default="reports/BUDGET_EASY_2026-10.txt")
    a = ap.parse_args()
    F = json.load(open(a.facts)); P = yaml.safe_load(open(a.plan))
    build_xlsx(a.xlsx, F, P); build_txt(a.txt, a.xlsx.rsplit("/", 1)[-1], F, P)
    print(json.dumps(summary(F, P), indent=1))


if __name__ == "__main__":
    main()
