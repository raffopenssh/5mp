#!/usr/bin/env python3
"""Render reports/ToR_Project_Manager_South_Sudan.txt from facts.json (numbers never typed; PIP v3, Oct 2026 — EM A1–A7, C1, A9).
  python3 scripts/easyplan_tor.py
"""
import json, re
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
F = json.load(open(ROOT / "data/plan_zones/solver/facts.json"))
D, B, P = F["deploy"], F["budget"], F["plan"]
y1, y2 = D["by_year"]
W = {r["when"]: r["what"] for r in P["workplan"]}
start = next(k for k in W if k.startswith("1 Jun"))
assert any("Options assessment" in v for v in W.values()), "workplan lacks the Jul 2028 options assessment row (A6)"
opt_when = next(k for k, v in W.items() if "Options assessment" in v)
way_when = next((k for k, v in W.items() if "way forward" in v.lower() and "agree" in v.lower()), None)
pm = B["headcount"][0]; assert "Project Manager" in pm["role"]
sal = int(re.search(r"USD ([\d,]+) all-in", pm["basis"]).group(1).replace(",", ""))
months = sum(pm["months"]); assert months == B["horizon_months"] == 18, (months, B["horizon_months"])
usd = lambda x: f"USD {x:,.0f}"
musd = lambda x: f"USD {x/1e6:.2f} million"
n_states = len(D["states"]); n_counties = D["n_counties"]
teams = f"{y2['echo']} ECHO teams, {y2['tango']} TANGO teams, {y2['fp']} paid county focal point{'s' if y2['fp'] != 1 else ''}"
ssws = P["gate_date"]
wk = P["wave_dates_by_kind"]
sav = P["savannah_hours"][1]; heli = P["heli_hours"][1]; fcm = sum(P["field_check_missions"])
nsif = 0.17; gross = round(sal / (1 + nsif) / 100) * 100
txt = f"""TERMS OF REFERENCE
Project Manager / National Coordinator, South Sudan
Radom-Numatina-Southern-Chinko Priority Intervention Plan (PIP v3, Oct 2026)

Employer        African Parks (Chinko). One post, full-time, international, based in Wau.
Reports to      Chinko Landscape Coordinator.
Start           {start}. {B['horizon_months']} months, the whole plan (Phase 1, {P['year_labels'][0]}: set-up;
                Phase 2, {P['year_labels'][1]}: one field season and the options assessment). Preparation before the
                start (key staff, donors, partner-grant scope) is Chinko's, so the CAR team keeps its 2026-27 dry season.
Budget held     {musd(B['ssd_total'])} (South Sudan request, {usd(B['ssd_total'])}){"; the CAR conservancy strand (" + usd(B['strands']['CAR']['total']) + ") is Chinko's own line beside it" if B.get('strands', {}).get('CAR') else "; no cross-border strand"}.
Line management {y2['staff']} field staff at full strength ({teams}); the post-holder is
                also the Wau focal point in year one. A 0.5 FTE finance/admin officer in Juba (dotted line, AP South
                Sudan); one national NGO grantee.

1. PURPOSE
Run the {B['horizon_months']}-month assessment and engagement phase on the ground: get the written government backing, build and
field the teams on time, understand the old-growth areas and the communities, herders and mining interests that use
them, prepare the legal track, and bring AP management a business plan and way forward by {opt_when}. The post
combines project management and the national, government-facing coordination in one person.

2. DELIVERABLES AND DEADLINES
D1  Written endorsement of the plan by the South Sudan Wildlife Service - by {ssws}.
    This is the gate: without it Phase 2 does not start. Plan presented to SSWS first, then Fauna & Flora
    (Southern NP) and the national NGOs; plan refined with them.
D2  Wau base open, kit procured, partner grant signed, {y2['staff']} staff recruited from the areas they will work in
    and trained - by the end of Phase 1 ({P['year_labels'][0].split(' - ')[1]}).
D3  Field deployment to calendar: {y1['tango']} TANGO teams in {wk['tango'][0]} (before the herds); {y1['echo']} ECHO teams in
    {wk['echo'][0]}; the paid focal point in {wk['fp'][1]}.
D4  Landscape assessment of the conservancy and corridor zones - actors, herd routes, gold activity - ground-truthed
    in the 2027-28 dry season, every patrol's checklist entered into the project database, and the ranking re-run
    with ground data after the season.
D5  A working focal-point network and working relations with SSWS, the {n_counties} counties, herder leadership and the
    communities of the staffed zones.
D6  Legal track prepared, not concluded: community-conservancy applications (ss. 14-16, Wildlife Act 2026), the
    Numatina boundary description and mining clause (s. 80 regulations) and the s. 24 closure request drafted and
    discussed where opportunities arise. Their approval is not a target of this grant.
D7  Flight programme delivered: {sav} h STOL (surveillance and livestock counts), {heli} h R44; {fcm} ground field-check
    missions.
D8  Options assessment and business plan to GoSS and AP head office by {opt_when}: incubator partner, local
    implementing partner, or AP taking on Numatina directly; {('way forward agreed ' + way_when) if way_when else 'way forward agreed with AP management'}.
D9  Monthly finance and operations report to the Landscape Coordinator; quarterly report to SSWS; one external
    audit at close; handover note.

3. RESPONSIBILITIES
Government and partners. Own the relationship with SSWS (Director of Protected Areas), the Ministries of Mining and
of Environment and Forestry, {n_states} governors, {n_counties} county commissioners, chiefs and the Fallata/Mbororo herder
leadership; work with Fauna & Flora on the Southern NP connection. Keep RRC/NGO Act registration and permits
current (80 % national staff, annual renewal). SSWS leads visibly beside the project; it does not report into it.
People. Recruit, contract, train, pay and line-manage all teams and the focal point. Enforce the recruitment rule:
every scout from the area the team works in. Mobile-money pay where it works.
Money. Hold the budget and approve spend against the workbook line table. Cash-in-transit discipline, no
unbudgeted assets: no new organisation, fleet, aircraft or enforcement equipment (vehicle hire for the Wau base,
charter for wings as needed). Manage the partner grant and suspend it on fraud.
Operations and security. Commission the external security assessment in Phase 1 and run the risk register. Medevac
and insurance in place before deployment. Charter and flight scheduling from Wau; Starlink at the base and the team
bases, trackers for the TANGO teams.
Data. Make sure every patrol's ground-truth forms reach the database that produced the plan, and feed corrections
back into the zoning and the gold model.

4. AUTHORITY
Signs contracts and spend within the approved budget and AP delegation limits; hires and dismisses national staff;
stops any team deployment on security grounds. Changes to team sites, phase dates or the budget envelope require
the Landscape Coordinator's approval; the team placement itself is AP-RCA's management decision, honed with SSWS.

5. KEY PERFORMANCE INDICATORS
- SSWS endorsement signed on or before {ssws}.
- Deployment dates met ({wk['tango'][0]} / {wk['echo'][0]} / {wk['fp'][1]}); {y1['staff']} then {y2['staff']} staff in post.
- Landscape assessment and ground-truth database complete after the 2027-28 season; ranking re-run with ground data.
- Options assessment and business plan delivered by {opt_when}.
- Spend within 5 % of budget by phase; clean audit; zero unresolved fraud cases.
- Zero serious security incidents; 100 % of flights and field-check missions delivered.
- 100 % of ground-truth forms entered within 30 days of patrol.

6. PROFILE
- 8+ years running field operations or multi-site projects in fragile states; South Sudan or the wider region
  strongly preferred.
- Track record negotiating with ministries, state authorities and traditional leaders; comfortable with legal
  process (gazettement, licensing, community registration).
- Proven budget ownership of USD 1 million, with donor-grade reporting and audit experience.
- Has recruited and managed teams of 20+ national staff; security management in remote areas.
- Protected-area, land-use or natural-resource experience an advantage; pastoralism and artisanal mining literacy
  a plus.
- English fluent; Arabic (Juba) an advantage. Fit to live in Wau and travel by light aircraft, motorbike and on foot.

7. TERMS AND SOUTH SUDAN TAX POSITION
Budget line         {usd(sal)} per month all-in (SAL_INTL), {months} months from {start} = {usd(sal*months)} (EM C11).
                    Whether this is gross-plus-employer-NSIF or an expatriate package sits on top is for AP HR to
                    confirm (open item, review v2 C1); the figures below assume all-in.
Gross salary        About {usd(gross)} per month (salary and allowances): gross plus the employer's NSIF 17 % equals
                    the budget line.
Tax residence       Resident from day one: present more than 183 days in the tax year (Taxation Act 2009, as
                    amended). Tax year = calendar year. Taxed on South Sudan-sourced income; home-country position is
                    the employee's own affair (no double-tax treaty with South Sudan).
PAYE                Progressive 0-20 % (top bracket above SSP 10,000/month; some advisers apply 25 % under the latest
                    Financial Act - confirm with the NRA before the first payroll). At the 2026 exchange rate the SSP
                    thresholds are negligible, so the top rate applies to practically the whole salary. Employer
                    withholds monthly and remits to the National Revenue Authority by the 15th of the following
                    month; annual return and employee certificate.
NSIF                National Social Insurance Fund: employee 8 % of gross (deductible for PAYE), employer 17 % on top
                    of gross. Applies to expatriates unless an exemption is agreed.
Indicative payslip  Gross {gross:,} - NSIF 8 % ({gross*0.08:,.0f}) - PAYE 20 % on {gross*0.92:,.0f} ({gross*0.92*0.2:,.0f}) = net approx. {usd(gross*0.92*0.8)}
                    (approx. {usd(gross*0.92*0.75)} if the 25 % bracket applies).
Employer cost       Gross {gross:,} + NSIF 17 % ({gross*nsif:,.0f}) = about {usd(gross*(1+nsif))} per month = the budget line. The work
                    permit (professional category, approx. USD 4,000 per year, employer-paid, renewed annually) and
                    any AP regional expatriate package (medevac, group life, R&R, flights) are HR's to confirm.
Allowances in kind  Housing in Wau, project vehicle use and field per diems are taxable employment income under the
                    Act unless structured as reimbursed business expenses - keep receipts.
Compliance          The post-holder is also responsible for the project's own tax hygiene: PAYE and NSIF for all
                    {y2['staff']} national staff, withholding tax on the partner grant and on suppliers, annual tax clearance
                    certificate (needed for RRC renewal and the work permit).
Location            Based in Wau with travel to Juba, the county towns and Chinko HQ.
"""
out = ROOT / "reports/ToR_Project_Manager_South_Sudan.txt"
out.write_text(txt); print("wrote", out, f"({len(txt.splitlines())} lines)")
