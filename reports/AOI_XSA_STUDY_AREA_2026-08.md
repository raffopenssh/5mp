# Study Area (XSA) — Fire, Transhumance, Human Presence, Forest Loss
**485,150 km² across CAR / South Sudan / DRC · window 2024-01-01 → 2026-08-06**
Prepared 2026-08-13 · revised 2026-08-14 (v2: settlement rebuild, persistence epochs, complete histmap OCR, nightlights null) · source: 5MP conservation monitoring DB · share link `/s/cf4ftqj`

The AOI spans the eastern CAR savanna, the South Sudan Bahr el Ghazal / Western
Equatoria plains, and the northern edge of the Uele forest. It contains four
protected areas — **Chinko (19,764 km²), Southern NP (19,270 km²), Bili-Uere
(11,343 km²), Garamba (641 km² of its area)** — together **10.5 %** of the AOI.
**89.5 % of the study area is unprotected ground**, and that is where most of
everything below happens.

---

## 0. How much to trust each number

| Layer | Source | Trust | The caveat that matters |
|---|---|---|---|
| Fire detections | VIIRS 375 m, 3 sensors | **High** | Sensor count changed: see §1.1 — never compare 2023 to 2024 raw |
| Fire trajectories / groups | our v5 clustering | **High for shape, medium for count** | A "group" is a burning front tracked day-to-day, not a herd |
| Forest loss 2001–2023 | Hansen GFC v1.12 | **High** | Ends 2023. Northern 2023 spike is a *map change*, see §3.2 |
| Forest loss 2024–2026 | GFW integrated alerts | **Medium** | Different unit and threshold than Hansen — do not splice the series |
| Settlements | GHSL built-up + POP, 100 m | **Medium-high** | Rebuilt since v1: surface is measured (not mask), population is the GHSL raster (not a density constant), and every cluster now carries a **persistence epoch** (E2000/E2015). §4.1 |
| Settlement persistence | GHSL back-epochs E2000/E2015 | **Medium** | "Recent" = no built surface before 2015 *at 100 m*; a small camp below GHSL's detection floor in 2015 would also read "recent" |
| Mining sites | OSM + Crisis Tracker reports | **Anecdotal** | 96 points in the AOI bbox; a report list, not a survey. §5 |
| Nightlights (mines) | VIIRS VNP46A3 monthly | **High as a null** | Measured and dark: median 0.00 nW at 71 sites. §5.2 |
| Place names | OSM + Sudan Survey 1:250k (1908–76) | Mixed | OSM is thin here; the historic sheets are richer — OCR of all 187 sheets is now **complete** (98,055 labels). §6 |

**One rule for the whole report:** every count below is *detected*, not
*present*. Absence of detection in the wet season is cloud and green fuel, not
absence of people.

---

## 1. Fire — the strongest signal we have

**4.08 million detections** inside the AOI in 31 months, grouped into
**38,725 fire trajectories**, of which **34,250 (88 %) are classified
transhumance** — moving fronts averaging **160 km over 23 days**.

### 1.1 ⚠ Read this before any year-on-year comparison

We ingest three VIIRS sensors from 2024 onward, one before. Raw totals triple
at the 2024 boundary for that reason alone. Single-sensor (NOAA-20) totals for
the four parks, whole fire seasons (Jul–Jun), are comparable:

```
season      Chinko   Bili-Uere  Garamba  Southern NP
2018/19     72,270      86,521   50,790      108,208
2019/20     78,497      95,100   49,341      113,635
2020/21     81,825      93,989   55,507      109,815
2021/22     52,890      76,843   45,962      103,479
2022/23     76,737      86,824   50,319      112,126
2023/24     62,065      86,343   54,393      133,227
2024/25     80,089     101,817   68,397      157,535
2025/26     80,800      75,455   41,194      137,482
```
Trend, honestly stated, against each park's own 2018/19–2023/24 mean:
**2024/25 was the heaviest season on record in all four parks — Chinko +13 %,
Bili-Uere +16 %, Garamba +34 %, Southern NP +39 %.** 2025/26 then split: the
**DRC parks fell back hard** (Bili-Uere −26 %, Garamba −40 % against 2024/25,
ending *below* their long-run mean), while **CAR and South Sudan stayed high**
(Chinko +1 %, Southern NP −13 % but still +21 % above its mean). Six seasons of
single-sensor history show no secular rise before 2024; two seasons of elevated
burning is a change, not yet a trend.

### 1.2 The season is one pulse, and it is short

Detections per month, all sensors, AOI-wide:

```
2024-01  ###############################              462,496
2024-02  #################                            256,482
2024-03  #####                                         80,607
2024-04  #                                             20,156
2024-05  #                                              9,420
2024-06                                                 1,103
2024-07                                                   208
2024-08                                                   445
2024-09                                                 1,028
2024-10  #                                             14,269
2024-11  #########                                    137,810
2024-12  #####################################        547,077
2025-01  ############################################ 649,540
2025-02  ###############                              216,027
2025-03  ########                                     124,975
2025-04  ##                                            24,104
2025-05  #                                             12,107
2025-06                                                 3,604
2025-07                                                   326
2025-08                                                   395
2025-09                                                   997
2025-10  ##                                            25,857
2025-11  ################                             230,947
2025-12  ###########################                  398,907
2026-01  ###################################          519,052
2026-02  #############                                187,834
2026-03  #########                                    127,078
2026-04  #                                             19,229
2026-05                                                 5,993
2026-06                                                 1,340
2026-07                                                   527
```

**Nov–Feb carries 87 % of the annual fire load. July–September is effectively
zero (<0.1 %).** Any patrol or engagement calendar that is not built around
Oct→Mar is built around the wrong months.

### 1.3 Fire *moves north* through the season

Mean latitude of detections, by month (all years pooled) — the front tracks the
retreating rains:

```
month  <6N      6-8N     8-10N   >10N     what it means
Oct     2,462   11,433   21,567   4,664   burning starts in the north
Nov    37,901  102,180  190,900  37,776   northern peak
Dec   213,679  389,062  307,194  36,049   front sweeps south
Jan   464,850  810,703  301,852  53,683   peak, centred 6-8N
Feb   220,850  286,043  114,206  39,244
Mar    95,466  152,539   64,808  19,847   south only
Apr     7,777   19,268   24,487  11,957   northern tail (late qoz burning)
```
This is the classic dry-season sweep. **The northern belt burns first (Oct–Nov)
and again late (Apr); the park belt at 5–8 N burns Dec–Feb.**

### 1.4 Fire pressure is not evenly distributed — and parks are *not* the worst

Detections per km² per season, AOI:

```
zone              km²      2023/24    2024/25    2025/26   det/km²/season
Chinko         19,764        6,871     22,317     25,672        0.93  ▁▃▃
Bili-Uere      11,343       16,948     24,637     20,357        1.82  ▂▃▃
Garamba (part)    641        1,436      1,453        925        1.98  ▃▃▂
Southern NP    19,270       45,519     95,947     74,122        3.73  ▂▄▃
unprotected   433,009      759,490  1,586,840  1,396,879        2.88  ▂▄▃
```
**Chinko burns at one third the density of the surrounding landscape** and one
quarter of Southern NP. Whatever Chinko is doing is working, or its fuel is
different — either way it is the outlier worth explaining. **Southern NP burns
30 % hotter than the unprotected landscape around it**; it is a park on paper
in a landscape that treats it as pasture.

Note also that Chinko's density *rose* over the three seasons (0.35 → 1.13 →
1.30 per km²) while the region's plateaued. That is the one adverse park trend
in this dataset and is worth a targeted look.

### 1.5 Transhumance in numbers

```
                       2023/24   2024/25   2025/26
groups (all types)       9,500    14,727    14,491
  of which transhumance  8,300    13,077    12,870   (87-89% every season)
mean days per group       19.4      21.9      20.7
mean track length (km)   129.2     151.7     143.8
```

Track length distribution (all groups):
```
  <25 km  #######################          5,188   avg  5 days
 25-50    ########################         5,548   avg 10 days
 50-100   #######################################  8,899   avg 14 days
100-200   ###########################################  9,736   avg 22 days
200-400   #############################    6,970   avg 36 days
 >=400    ##########                       2,384   avg 56 days
```
**2,384 groups travelled 400 km or more; the longest ran 1,577 km over 119
days (Oct 2024 → Feb 2025, heading SE).** These are not local farm burns. They
are corridor-scale movements crossing the whole AOI.

Direction of travel is almost perfectly isotropic AOI-wide (E 4,552 / SE 4,542 /
S 4,262 / NW 4,251 / W 4,226 / N 4,173 / NE 4,142 / SW 4,102) — **there is no
single migration axis to police**; there is a diffuse, everywhere-at-once
seasonal flood. But per park the geometry differs (§2).

Monthly onset of transhumance groups:
```
Oct  ######                               1,691
Nov  ##################                   5,153
Dec  #####################                5,954
Jan  ################################### 10,044
Feb  ########################             6,579
Mar  ###########                          2,946
Apr  #####                                1,417
May  #                                      380
Jun-Sep (all four months)                     86
```

---

## 2. What reaches each protected area

Transhumance groups whose track intersects the park boundary:

```
park            2023/24  2024/25  2025/26   arrival peak   dominant headings
Southern NP         782    1,311    1,236   Jan (1,137)    SE 527 / S 467 / NW 462
Bili-Uere           579      807      858   Jan (881)      W 445 / S 366 / SW 332
CAF Chinko          104      161      142   Jan (148)      W 85 / S 58 / E 58
Garamba (part)       82      119       99   Jan (132)      E 78 / SW 51
```

Read across, not down:
* **Bili-Uere is the only park with a rising three-season trend (+48 %)** while
  its raw fire count fell — more incursions, smaller fires. That combination
  usually means *more, smaller herds*, not less pressure.
* **Southern NP takes 8× Chinko's incursions on a similar area.**
* **January is the arrival month for every park without exception.** A single
  region-wide engagement window exists, and it is Dec 15 – Feb 15.
* Chinko's incursions come **from the west** (Kotto/Ouaka side), Bili-Uere's
  from the **west and south**, Garamba's from the **east**. These are different
  herder populations and need different interlocutors.

---

## 3. Forest loss

### 3.1 The long series (Hansen, 2001–2023, ends 2023)

```
2001  ##                    20.3 km²
2002  ###                   24.1
2003  #                     10.8
2004  ###                   31.4
2005  ##                    25.1
2006  ##                    21.7
2007  ###                   36.1
2008  ##                    21.3
2009  ###                   33.5
2010  ##                    22.5
2011  ####                  39.5
2012  ###                   35.0
2013  ###                   28.3
2014  #####                 51.9
2015  ##                    25.4
2016  #####                 49.0
2017  ##########            99.6
2018  ####                  39.7
2019  #####                 48.9
2020  ####                  37.8
2021  ######                60.1
2022  ######                58.7
2023  ################################  313.6
```
Total 2001–2023: **1,134 km², i.e. 0.23 % of the AOI in 23 years.** By regional
standards this is a *low-deforestation landscape*. The underlying trend is a
slow doubling: ~25 km²/yr in the 2000s, ~50 km²/yr in the 2020s.

### 3.2 ⚠ The 2023 spike is mostly not deforestation

Of 2023's 313.6 km², **203.0 km² (65 %) lies north of 9.5 °N** — the Darfur
border qoz, where the Hansen series had recorded ~0.2 km²/yr for twenty years.
That is a step change of three orders of magnitude in one year in a savanna
woodland belt. It is far more consistent with **a change in the Hansen tree-
cover baseline / a large qoz fire scar being scored as loss** than with 200 km²
of new clearing. Treat the northern 2023 figure as **unverified**. The southern
half (110.7 km², vs 58.5 km² in 2022) is a genuine, and notable, doubling.

### 3.3 Where the loss is, and what type

```
class          events  km²    where (mean)     what it is
logging         1,086  839.7  6.2N 26.4E       linear/road-associated clearing
slash_burn      1,807  227.9  5.5N 26.4E       fire-correlated agricultural clearing
natural         4,807   84.6  6.4N 27.0E       scattered, no fire, small
encroachment      115   15.1  6.5N 26.9E       clustered patches
```
**Logging accounts for 74 % of all area lost while being only 14 % of events.**
It concentrates in two bands: 4–5 °N (267 + 203 km² — the Uele forest edge) and
a 214 km² anomaly at 10 °N which is the suspect 2023 northern block above.

### 3.4 Loss rate by zone (2015–2023, the reliable recent window)

```
zone           km²      loss 15-23   %/yr
Bili-Uere    11,343       49.3 km²   0.048   <- 3x the landscape rate
Garamba(part)   641        2.3 km²   0.039
unprotected 433,009      677.8 km²   0.017
Chinko       19,764        3.1 km²   0.002   <- 10x better than landscape
Southern NP  19,270        0.4 km²   0.000
```
**Bili-Uere is losing forest three times faster than the unprotected
landscape.** It is also the park with rising transhumance incursions. Those two
facts pointing the same way is the strongest single finding in this report.
Chinko and Southern NP have essentially no measurable canopy loss — but note
Southern NP is savanna, where Hansen sees little to lose; its zero is a
*biome* result, not a management result. Chinko's is both.

### 3.5 2024–2026 (GFW alerts — different unit, do not splice)

696 events from 2.23 M alerts (3,505 quality cells inside the polygon), and the
area figures (0.7 / 2.4 / 29.7 km² for 2024/25/26) are **not comparable to
Hansen** — they are an alert-count proxy at 0.0001 km²/alert, not a mapped
area. Use them for *where and when*, never for *how much*. What they do show:
99 % of 2024–26 alert-derived events are classed `slash_burn`, and the alert
volume is rising steeply (15.6 k → 79.0 k → 2.13 M alerts by last-alert year),
which for integrated alerts largely reflects sensor/product changes plus the
2026 season being fully covered.

---

## 4. Human presence

### 4.1 The settlement layer was rebuilt — v1's warnings are resolved

v1 of this report flagged the settlement figures as unusable (mask area counted
as surface, 200 people/ha applied to it, one "town" of 61.7 M people spanning
the Bahr el Ghazal). The pipeline has since been fixed: surface is now measured
from the raster, population comes from the GHSL POP grid, cluster chaining is
bounded. The current numbers, all measured:

**2,121 settlement clusters · 110.2 km² built-up surface · 2.63 M people
(population measured for all 2,121).** That is ~5.4 people per km² of AOI
— thinly but genuinely peopled ground.

```
classification    n      persistence          n
temporary_camp  1,374    permanent (pre-2000) 1,665   (79 %)
town              333    established (00–15)    208   (10 %)
village           311    recent (post-2015)     248   (12 %)
settlement        102
agricultural        1
```

### 4.2 The "temporary camps" are not temporary

The classifier calls 1,374 clusters `temporary_camp` on morphology (small,
low-density). GHSL back-epochs say **1,332 of them (97 %) already had built
surface in 2000**; only 21 appeared after 2015. Whatever these are — and the
historic sheets suggest an answer, §6.3 — they are a **persistent lattice of
small settlements**, most of it standing for 25+ years and much of it far
older. The word "camp" in the classification should not be read as "mobile" or
"new". This reverses v1's framing of a camp-dominated, transient landscape.

Median fire detections within 5 km, by persistence class: permanent 1,264 ·
established 979 · recent 909. The *oldest* settlements sit in the heaviest
burning — consistent with the fire being the long-standing pastoral economy of
the old lattice, not something new arriving around new camps.

### 4.3 Where growth actually is: a post-2015 town belt in Western Bahr el Ghazal

The 248 recent clusters are not scattered — they are a **belt at 8.5–9.4 °N /
26.8–28.2 °E** (Busseri River / Koko corridor, Western Bahr el Ghazal, SSD),
and they are disproportionately **towns**: 150 of the 333 towns in the AOI are
post-2015, and 62 of those sit in this one belt, housing ~168,000 people.
All recent clusters together hold **~569,000 people — 22 % of the AOI's
population lives in settlements that did not exist in 2015.** This is the
northern-boundary pressure v1 gestured at, now with coordinates and a count.
It coincides with the SSD civil-war displacement and return years, and it is
the single fastest-changing human fact in the AOI.

### 4.4 The "new" towns are mostly old places (historic-map cross-check)

Matching every cluster against named places on the 100-year-old Sudan Survey
sheets (§6): **54 % of recent clusters sit within 3 km of a named historic
place, vs 24 % for random ground across the covered area** — 2.2× the random rate,
and *higher* than for permanent clusters (31 %). Of the 62 recent towns in the
WBeG belt, 36 sit on a named historic village (Rian Kuj, Karyat, Agok, Mayong,
Fan Dung…). **The post-2015 growth is largely re-occupation of villages the
colonial surveyors mapped, not greenfield expansion.** For dialogue and land-
claims purposes that distinction matters: these populations are returning,
not arriving.

### 4.5 Parks are still 2–3× less settled than the landscape

```
zone           clusters   per 1,000 km²
unprotected      ~2,055        4.7
Bili-Uere            18        1.6
Southern NP          28        1.5
Chinko               20        1.0
Garamba (part)        0        0.0
```
(The park-side counts are unchanged from v1; the unprotected count rose with
the rebuild.) Chinko remains the least settled park with any settlement at
all; Garamba's slice has none.

### 4.6 Settlements and fire are *not* co-located

Fire detections within 5 km of a settlement cluster vs. random points in the
AOI remain statistically indistinguishable (medians ~1,500 vs ~1,500).
Burning here is a landscape-wide pastoral practice, not a village-perimeter
phenomenon. **You cannot find the burners by watching the settlements** — and
§4.2 explains why: the settlements and the burning are the same old economy,
not cause and effect.

### 4.7 Infrastructure

12,956 road segments, 15,050 km:
```
track           3,642 segs   5,104 km
path            6,458        4,973
unclassified      795        2,204
secondary          81        1,174
primary           113          992
tertiary           42          235
residential     1,783          351
trunk               1            9
```
**Only 2,410 km of the 15,050 km network is tertiary-or-better.** 67 % of
length is track and footpath — i.e. the access network is the herding network.
Interdiction on roads is not a viable control; presence at water is.

971 OSM places, but distributed 4 °N: 230, 5 °N: 469, 6 °N: 152, 7 °N: 38,
8 °N: 23, 9 °N: 10, 10 °N: 49. **The 7–9 °N belt — where the heaviest burning
happens — is a near-blank on modern maps.** See §6.

---

## 5. Mining — what we can and cannot say

**We do not hold a mining dataset for this AOI, and we do not detect mining.**
Optical detection of artisanal workings was tested and retired (AUC 0.45–0.56
against confusers; the one model with real signal, AUC 0.78, still yields
precision ~0.001 at this base rate). Nothing in this report should be read as
a mine detection.

What we do hold, as *reports*, not survey: **96 occurrence points inside the
AOI bbox** — 40 from Crisis Tracker community incident reports, 56 from OSM
tags (the OSM points cluster around Juba-side quarries in the far east; the
Crisis Tracker points sit in the western third, 22.8–24.9 °E, **west of
Chinko in the Kotto basin** — the same direction Chinko's transhumance
incursions come from).

Every Crisis Tracker site exists because an incident was reported there; the
list maps *reporting reach*, not geology. The blank middle of the AOI is not
evidence of no mining.

### 5.1 A century of extraction at the same pits (historic-map cross-check)

The 100-year-old Sudan Survey sheets record mining directly, and the overlap
with the modern incident list is exact where it can be checked:

* **`Hofrat elNahas`** ("the copper pit", 9.76 °N 24.32 °E) is printed on the
  sheets; **8 km away** sits Crisis Tracker site ct_015 — an unnamed mine where
  an **LRA attack was reported in 2016**. The same pit, worked and fought over,
  a century apart.
* **`Old copper workings`** at 9.11 °N 23.86 °E — 66 km from the nearest
  modern reported site, on the same Kotto-basin trend.
* **`(Iron mines)`** at 8.66 °N 25.80 °E and **`Old iron forge`** at 7.38 °N
  26.34 °E — no modern report within 200 km of either: either genuinely dead,
  or outside anyone's reporting reach (see above; we cannot distinguish).
* **54 `Ironstone` labels** (flats, ridges, outcrops) trace the laterite
  plateau across 6–8 °N — geology the surveyors walked, not inferred.

The operational point: **the historic sheets are an independent, pre-conflict
mineral-occurrence layer** for exactly the ground where modern reporting is
thinnest, and where they can be checked against modern reports they agree.

### 5.2 The mines are dark (nightlights null result)

VIIRS monthly nightlights (VNP46A3) were measured at 71 reference mine sites
region-wide: **median radiance 0.00 nW·cm⁻²·sr⁻¹** — indistinguishable from
dark ground and ~50× below the lit-settlement threshold. A mean-statistic
"glow" lift of 1.44× (p≈0.03) exists but is driven by a handful of sites near
towns. **Artisanal mining in this region cannot be monitored by satellite
nightlights**; the pipeline was measured, found null, and retired. Reported
here because a measured null closes a door that would otherwise keep being
proposed.

### 5.3 Geology affinity (unchanged from v1, still the measured view)

**For the CAR sheet:** gold *junction* lines (rock-type contacts) concentrate known workings **2.2–3.9×** over random ground. Gold
*map units* score **0.63×, i.e. worse than random**. Diamond units are flat
(0.98–1.41×) and diamond junctions are unmeasured. Two operational readings:
(1) contact zones are worth prospecting for *incursion risk*, map units are not;
(2) the effect is **stronger where surveyors recorded an armed actor** at the
pit (2.04 vs 1.31 for units, p = 0.002 stratified) — armed presence concentrates
on the better ground. Conflict context for the prefectures overlapping the AOI
(ACLED-derived totals, ours not theirs): Haute-Kotto 818 events / 1,318
fatalities since 1997, Mbomou 434 / 1,022, Vakaga 612 / 1,748 with a 2025 spike
(195 events), Western Equatoria 1,168 / 1,823 also spiking in 2025 (239).

---

## 6. The historic map layer — a 100-year-old baseline, now machine-readable

OCR of the Sudan Survey 1:250,000 series (surveyed 1908–1976, mostly
1920s–40s) is **complete**: all 187 sheets, 5,093 tiles, **98,055 label
readings → 94,300 after dedup**, of which **17,553 fall in the AOI bbox**
(9,476 named places, 3,355 water features, 1,890 terrain, 735 vegetation, 468
route annotations) across 46 sheets. Georeferencing is good to a few hundred
metres; the OCR is machine transcription — verify against the sheet image
before citing a specific name. These maps are over a century old in their
oldest survey work: they are a **historic reference**, a snapshot of the
landscape before the modern conflict era, not a current gazetteer.

**Coverage caveat:** the series is Anglo-Egyptian Sudan. Coverage of the CAR
and DRC parks is thin border-sheet overlap only — Chinko 57 labels, Bili-Uere
26, Garamba 190 — vs Southern NP 751. Findings below apply to the Sudanese
two-thirds of the AOI; the park cores of Chinko/Bili-Uere are effectively
uncovered, which itself matches: the surveyors mapped the Chinko headwaters'
rivers and jebels in detail and recorded almost no settlement, agreeing with
the modern zero.

### 6.1 What the sheets say about the northern grazing belt (verbatim)

* **10.5 °N, 23 °E** — the corridor between Birao and Am Dafok is labelled
  `Qoz Salsilgo`, `Qoz Binat`, `Kirkira el Binat`, with `Uninhabited forest`,
  `Open forest, Sandy soil`, `Scattered bamboos`, and a chain of named *rahads*
  (seasonal pools): `Rahad Gabr Hamza`, `Sahabaia — water till end of Jan`,
  `Bahr — water plentiful Jan 1923`, `Rahad Sherukh`, `Rahad Masrur`,
  `Rahad Gamalla`. The tribal name printed across it is **TAAISHA**, with
  `Umm Dafug (Watering place)` and `Barasmas Wells`.
* **10.6 °N, 24.5 °E** — `Qoz Dango`, annotated `Uninhabited forest
  Traversable in rains or just after`, with the surveyor's own note: *"Wells
  and Ruhud on Qoz Dango entered from hearsay. They mark hunters' routes across
  the Qoz north to south and east to west."* Also **FALLATA** (a Fulani/West
  African pastoralist name) with `Good grazing`, `Numerous Wells`, and villages
  recorded in groups — `(4 Villages)`, `(3 Villages)`.
* **10.8 °N, 25.4 °E** — **BENIYA** (Rizeigat/Beni Halba country), `Mostly
  black cotton soil and bad going`, `Cultivation` beside `Buram (Police Post)`,
  `Doleiba Wells`, `Telahun (Market)`, `Broad track` running SE, and
  `(Unsurveyed)` across the southern third.
* **The 2023 Hansen anomaly block (10.1–10.8 °N, 23–25.2 °E, §3.2)** is, on
  the sheets, qoz dune country and hunters'/caravan routes — `DARB EL FIL`
  ("the elephant road"), `To Birkat Khadra`, `To Buram`, `(Rough track)` —
  historic pastoral and forest ground with no settlement. Nothing in the
  historic record suggests a 200 km² clearing frontier here; it supports
  reading the 2023 northern spike as a map artefact.

### 6.2 The vanished density of the Bahr el Ghazal

At **8.5 °N, 28 °E (Gogrial/Jur)** one 1:250k crop carries roughly **400 named
settlements** — against **23 OSM places in the whole 8 °N band today**. The
recent-town belt of §4.3 sits exactly here, and 36 of its 62 new towns are on
named historic villages (§4.4). The sheets also carry the surveyors' Dinka
glossary in situ: `Toich = Swamp formed by river flood`, `Wat = Cattle
enclosure`, `Wot: site of cattle camp usually raised, with water adjacent`,
`Luaks:- Large grass Dinka cattle byres`, `Dugdug:- Dinka cattle camp` — and
**40 place names beginning `Wun …`** (Dinka *wun*, cattle-camp), 20 of them in
the single degree square 8–9 °N / 28–29 °E. The named `Open Toich` polygons at
7.9–8.5 °N, 27.5–28.7 °E map the seasonal grazing floodplain — exactly the
belt where §1.3 shows the fire front peaking in November–December. **The
pastoral geography of the 1930s is the fire geography of the 2020s.** The v1
reading that "temporary camps" dominate the settlement layer (§4.2) resolves
here: the lattice of small, old, persistent clusters *is* the cattle-camp
system the surveyors named, still standing where they mapped it.

### 6.3 Historic routes and terrain vs today's fire — measured, weak-to-real

Two quantitative cross-checks of the historic layer against 2024–26 VIIRS
detections (10 km sampling boxes, random-point controls, permutation test):

```
historic feature (5–10°N, 24–31°E)   n     fire det/km²   control   lift    p
route annotations (tracks, darbs)   274        8.91         8.32    1.07×  0.03
ironstone labels (flats, ridges)     54        9.89         8.25    1.20×  0.001
```

The century-old route network still carries a *measurable but weak* excess of
burning — real (p = 0.03) but only +7 %; the corridors persist more in the
toponymy than in the flame map. The **ironstone plateau association is
stronger (+20 %, p = 0.001)**: laterite flats are the classic open
grass-savanna surfaces of the Zande plateau, and they burn hardest. Neither is
a targeting tool; both confirm the fire is structured by terrain and old
geography, not by recent settlement (§4.6).

**Operational value:** the sheets give a named, dated inventory of dry-season
water points, tribal grazing territories, cattle-camp sites and market/police
posts for exactly the northern belt where modern data is a blank — and §4.4
shows the names still bind: people are re-occupying the mapped villages. Any
transhumance dialogue in the north should start from these names.

---

## 7. Species and climate context (park scale — deliberately not averaged over the AOI)

Threatened species recorded across the four parks: **Chimpanzee (EN)** in all
four; **Okapi (EN)** in Bili-Uere and Garamba; **VU** in all four: African
elephant, lion (Chinko / Garamba / Southern), leopard, cheetah, giraffe,
hippopotamus, African golden cat, and four pangolin species. 272–312 species
recorded per park (Chinko 272, Southern NP 304, Bili-Uere 306, Garamba 312).

```
park           T °C  rain mm  zone                 rains     dry
Chinko         24.3   1,498   Tropical Savanna     Jun-Sep   Dec-Feb
Bili-Uere      24.5   1,739   Tropical Rainforest  Oct       Dec-Jan
Garamba        24.6   1,547   Tropical Rainforest  Aug       Dec-Feb
Southern NP    26.6   1,064   Tropical Savanna     May-Aug   Dec-Feb
```
Southern NP is the hottest and driest of the four and burns hardest — consistent
with fuel and rainfall, not only with governance. **Bili-Uere is the wettest and
the only one classed rainforest that is losing canopy fast**; a rainforest park
under a rising transhumance load is the combination that ends in permanent
conversion.

---

## 8. What this says to a manager

1. **The calendar is fixed and narrow.** Nov–Feb is 87 % of fire; January is the
   arrival month at every park. Everything else is preparation or debrief.
2. **Bili-Uere is the priority.** Only park with rising incursions (+48 % over
   three seasons), losing canopy at 3× the landscape rate, and a rainforest
   biome that does not recover. It is not the loudest in raw fire counts, which
   is exactly why it has been overlooked.
3. **Southern NP is functionally unprotected.** It burns 30 % hotter than the
   unprotected landscape around it and takes 1,200+ transhumance incursions a
   season.
4. **Chinko is the control case.** One third of the landscape fire density, one
   tenth its canopy-loss rate. But its fire density rose in each of the three
   seasons — worth explaining now, not after it converges.
5. **Do not look for burners near villages.** Fire near settlements is
   indistinguishable from fire near random ground — and the settlement lattice
   is a century-old pastoral system, not new encroachment (§4.2, §6.2). Water
   points, not settlements, are the leverage — and the historic sheets name
   them.
6. **Engage by direction, not by park.** Chinko's incursions come from the west,
   Garamba's from the east, Bili-Uere's from the west and south. Those are
   different communities.
7. **The real demographic change is the WBeG town belt.** 248 post-2015
   settlements, ~569,000 people (22 % of the AOI's population), concentrated at
   8.5–9.4 °N — and mostly *returns* to villages mapped a century ago, not new
   colonisation (§4.3–4.4). This is where land-use conflict with the northern
   grazing corridors will surface first.
8. **The settlement numbers are now safe to quote.** v1's warning is retired:
   surface is measured, population is the GHSL grid, persistence is dated.
   Quote them with their epoch caveats (§0).
9. **The mines are dark and old.** Nightlights cannot monitor them (§5.2), and
   the ground being fought over is in places the same pits the colonial sheets
   label — Hofrat el Nahas has been "the copper pit" for a century (§5.1).

---

### Reproduce
`/s/cf4ftqj` · AOI `XSA_Study_Area` · 11/11 datasets `done`
(3.18 M detections → 38,725 trajectories · 76,903 Hansen polygons → 7,079
events · 2.23 M GFW alerts → 696 events · 74,904 built-up polygons → 2,121
clusters · 12,956 roads · 971 places · 46 watersheds · 187 historic sheets →
94,300 deduped labels, 17,553 in AOI). Histmap labels:
`GET /api/histmap/sudan250k/labels?bbox=…` — machine OCR, verify against the
sheet before citing. Route/ironstone fire lifts: 10 km boxes, VIIRS 2024–26,
400 random controls, 2,000-permutation test.
