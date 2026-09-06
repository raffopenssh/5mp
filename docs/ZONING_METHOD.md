# Zoning method — how 5MP proposes parks, wilderness, conservancies and corridors

*Companion to `reports/ZONING_PLANNER_MAP_2026-09.png`. Agent detail in `docs/agents/zoning.md`.*

## 1. Why a method, not a drawing

The EASY plan zones (park, wilderness, grazing corridor, conservancies) were
drawn by hand. Hand-drawn lines have two weaknesses a donor or a ministry will
find: nobody can say *why the line is here and not 5 km east*, and nobody can
say *how sure we are*. This method answers both with the data 5MP already
holds for the study area, and it applies the same yardstick to what it
proposes and to what was drawn — the same numbers, side by side.

## 2. Four classes, the ones the plan already uses

| Class | Meaning | Legal hook (South Sudan) |
|---|---|---|
| **core** | nobody lives there, no fields, no recent clearing, no known workings | national park / extension |
| **wilderness** | sparse people, negligible fields | Wildlife Act 2026 s.9 buffer / reserve |
| **community** | settled, farmed, or exposed to mining licences | s.14 community conservancy (s.14(4) veto); Land Act 2009 s.66–67; Mining Act s.24 consent |
| **corridor** | dense long-range herd movements along one axis, few people | s.9 corridor |

## 3. Legible units

Land is cut *only* along features a villager, herder or ranger can name:
rivers (HydroRIVERS; unnamed reaches take their 1930s Sudan Survey name),
swamp and lake edges (the toich edge is the grazing boundary herders already
keep), ridge chains of the sheets' hill marks, named khors, roads, borders,
1930s *district* boundaries, and — faintly — geological contacts. **1930s
tribal boundaries are not used**: they are the Condominium's assignment, not
a line today's communities should be asked to accept, and a boundary must be
described in words people use now. Where no river or ridge runs, the
description names the point landmarks within 3 km — 1930s village sites and
wells from the sheets' symbol layer, today's villages, lone hills — so a
stretch reads "from the Bo River past old Tidi to Kuru pool", not "unnamed".
A watershed algorithm floods from every village and from a lattice
in empty land, so each unit ends on the strongest such feature between two
seeds. Units under 2,000 ha are absorbed. Every unit's perimeter is then
described by feature and compass side ("Nahr al Jur on the E/NE 34 km; K.
Ngoko (1930s sheet) on the W 10 km") and the share of its edge that lies on
nothing nameable is printed.

## 4. Evidence and one rule

Each unit is measured with the same rasters the 5MP park report uses: GHSL
people (a lower bound), clusters new since 2015, fire 2024–25 (three-satellite
fleet), fire fronts with type, length and direction, reviewed clearing,
GLAD cropland 2003→2019, mining targets (XSA model: top‑5 % cells capture 16 %
of known workings, lift 3.25 — targets, not mines), rock type and its
commodity prior.

One rule classifies, in order **corridor → core → wilderness → community**,
and stores every test with its number: e.g. *long fronts per 10 km width
256.9 ≥ 154.4: yes | transhumance 99 % ≥ 50: yes | axis coherence 0.43 ≥ 0.4:
yes | people 1.13/km² ≤ 2: yes → corridor*. Thresholds on quantities a sensor
fleet changes (fire) are quantiles of the area's own units; thresholds with
physical meaning (people per km², hectares of field) are absolute.

## 5. Proposals by growth, with support

For a wanted class the planner grows the best area from the units already of
that class, adding neighbours only while the *whole* area still classifies as
wanted and the objective (size or people served × fenced, compact boundary ×
class term) rises. It repeats this dozens of times with every threshold
perturbed ±25 % and the path jittered; the share of runs in which a unit ends
inside the area is its **support**. A proposal is the land with support ≥ 0.5;
0.25–0.5 is *contested* land to walk with the community; the spread of sizes
across runs is the uncertainty. A corridor is treated as a *network of paths*:
the long transhumance fronts are grouped by where they start and end (about
ten bundles, several beginning and ending inside the study area), each bundle
is routed as its own least-cost path over long-front density weighted by how
much the fronts there agree on one axis, and each is bootstrapped the same
way. Every branch reports where from, where to, how many fronts, which months,
and whether the band holds more herd movement than the general burning
would predict.

## 6. Reading the map

* dark green solid — **core proposals** with ha, people, support; the largest
  re-finds the drawn Pongo-Wau-Numatinna park and extends it south-west
* red band — the **corridor the herds actually walk** (support ≥ 0.5),
  Radom → Raga → Deim Zubeir → north of Wau → east of Southern NP → Garamba
* violet hatch — **community conservancy proposals** on the park rim
* grey hairlines — the legible mesh; thin outlines and labels — the hand-drawn
  zones and gazetted PAs for comparison; the pink haze is fire fronts

## 7. Validation, honestly

Fed only rivers/ridges/borders and measurements, the planner re-finds
Zemongo (IoU 0.93), Bili-Uere 0.85, Chinko 0.81, Wau-Wilderness 0.77,
Southern-NP-Wilderness 0.76, Boro 0.73, Chelkou 0.69, Southern NP 0.65. It
finds the drawn **Pongo-Wau park at 0.54** — its perimeter is only 44 %
legible, i.e. much of that line runs through open bush. It does *not* find the
drawn grazing zones (IoU ≤ 0.47): the fronts walk elsewhere. Every reference
polygon is also measured whole with the same rule, so the report can say what
the planner would call Southern NP as drawn (wilderness, 0.08 people/km²) and
why.

## 8. Limits

Populations are GHSL lower bounds; boundary names come from HydroRIVERS and
1930s sheets and must be verified on the ground; the mining model exists for
XSA only (elsewhere *unmeasured*, never zero); geology is a prior with skill
measured only for CAR junctions; an area without 1930s sheet coverage
(Chinko) yields few nameable edges and the method degrades to rivers and
roads. The LLM reading of sheet names is advisory and never classifies.
