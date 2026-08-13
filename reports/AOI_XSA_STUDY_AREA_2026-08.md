# Study Area (XSA) — Fire, Transhumance, Human Presence, Forest Loss
**485,150 km² across CAR / South Sudan / DRC · window 2024-01-01 → 2026-08-06**
Prepared 2026-08-13 · source: 5MP conservation monitoring DB · share link `/s/cf4ftqj`

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
| Settlements | GHSL built-up R2023A, 100 m | **Low-medium** | Modelled *2030 projection*; our area and population figures are inflated ~20× and ~20×, see §4.1 |
| Mining sites | OSM + Crisis Tracker reports | **Anecdotal** | 38 points in 485,000 km²; a report list, not a survey. §5 |
| Place names | OSM + Sudan Survey 1:250k (1915–68) | Mixed | OSM is thin here; the 1930s sheets are richer. §6 |

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

### 4.1 ⚠ The settlement figures must be corrected before use

GHSL R2023A 100 m built-up surface, epoch **E2030 (a projection, not an
observation)**, gives 74,904 built-up polygons → **1,552 settlement clusters**.
Two errors compound in the stored figures:

1. **Area is the mask, not the surface.** We keep every 100 m cell with >50 m²
   of built-up surface and then count the *whole cell* as built. Recomputing
   from the raster: mask 6,798 km² vs actual built-up surface **181 km²** — the
   stored "4,268 km² built-up" is **~24× too high**.
2. **Population is 200 people/ha applied to that inflated area**, giving
   85.4 million people in the AOI. The true population is on the order of
   3–5 million. **Every `population_est` in this AOI is unusable.** The largest
   "town" is recorded as 61.7 million people across a 3,086 km² blob spanning
   7.2–9.7 °N and 26.6–29.3 °E — that is 52,454 polygons chained together by
   2 km single-linkage across the whole Bahr el Ghazal, i.e. one artefact, not
   one town.

**What survives the correction and is usable:**

```
zone           clusters   per 1,000 km²
unprotected       1,486        3.43
Bili-Uere            18        1.59
Southern NP          28        1.45
Chinko               20        1.01
Garamba (part)        0        0.00
```
**Settlement density inside parks is 2–3× lower than outside** — the parks are
demonstrably less settled. Chinko is the least settled of the three with any
settlement at all; Garamba's slice has none.

Cluster size distribution (as counted, before the 24× area correction):
```
1-5 ha    ############################################  62,408 polygons
5-100 ha  ########                                       12,070
1-10 km²                                                    347
>10 km²                                                      34   <- these are chaining artefacts
```

### 4.2 Settlements and fire are *not* co-located

Fire detections within 5 km of a settlement cluster vs. 1,552 random points in
the AOI:

```
                  median   mean    p10    p90   zero
settlements        1,594   1,502    661  2,239     0
random points      1,504   1,477    449  2,391    23
```
Statistically indistinguishable. **28 % of all detections fall within 5 km of a
settlement — but so would 28 % of random ground.** Burning in this landscape is
not a village-perimeter phenomenon; it is a landscape-wide pastoral practice.
This is the finding that most changes what a manager should do: **you cannot
find the burners by watching the settlements.**

### 4.3 Infrastructure

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

What we do hold, as *reports*, not survey: **38 occurrence points inside the
AOI** — 32 from Crisis Tracker community incident reports, 6 from OSM tags.
Commodity is recorded for 4 of them (3 gold, 1 diamond). By prefecture:
Haute-Kotto 22, Mbomou 14, Haut-Mbomou 1, South Darfur 1. All 38 sit in the
western third of the AOI (22.8–24.9 °E), i.e. **west of Chinko, in the Kotto
basin** — the same direction Chinko's transhumance incursions come from.

Every Crisis Tracker site exists because an incident was reported there; the
list maps *reporting reach*, not geology. The blank eastern two-thirds of the
AOI is not evidence of no mining.

**Geology affinity, measured, for the CAR sheet:** gold *junction* lines (rock-
type contacts) concentrate known workings **2.2–3.9×** over random ground. Gold
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

## 6. Historical maps: the names the modern data lost

The Sudan Survey 1:250,000 sheets (surveyed 1915–1968, 49 cells cover this AOI)
are the only source we hold that describes the **northern grazing belt as a
grazing belt**. Georeferencing is good to a few hundred metres — read them for
names and land use, not for coordinates. Verbatim from the sheets:

* **10.5 °N, 23 °E** — the corridor between Birao and Am Dafok is labelled
  `Qoz Salsilgo`, `Qoz Binat`, `Kirkira el Binat`, with `Uninhabited forest`,
  `Open forest, Sandy soil`, `Scattered bamboos`, and a chain of named *rahads*
  (seasonal pools): `Rahad Gabr Hamza`, `Sahabaia — water till end of Jan`,
  `Bahr — water plentiful Jan 1923`, `Rahad Sherukh`, `Rahad Masrur`,
  `Rahad Gamalla`. The tribal name printed across it is **TAAISHA**, with
  `Umm Dafug (Watering place)` and `Barasmas Wells`.
* **10.6 °N, 24.5 °E** — `Qoz Dango`, annotated `Uninhabited forest Traversable
  in rains or just after`, with the surveyor's own note: *"Wells and Ruhud on
  Qoz Dango entered from hearsay. They mark hunters' routes across the Qoz north
  to south and east to west."* Also **FALLATA** (a Fulani/West African
  pastoralist name) with `Good grazing`, `Numerous Wells`, `Open country
  (Unsurveyed)`, and villages recorded in groups — `(4 Villages)`, `(3 Villages)`.
* **10.8 °N, 25.4 °E** — **BENIYA** ( Rizeigat/Beni Halba country), `Mostly black
  cotton soil and bad going`, `Cultivation` beside `Buram (Police Post)`,
  `Doleiba Wells`, `El Buheir (Wells)`, `Telahun (Market)`, `Broad track`
  running SE, and `(Unsurveyed)` across the southern third.
* **8.5 °N, 28 °E (Gogrial / Jur)** — `Open Toich` (seasonal grazing floodplain)
  in two places, `Fly Forest` and `Tsetse Fly Forest` on the east,
  `Single Gemmeiza Well`, `Scattered wells`, `Deserted village(s)`, and roughly
  **400 named settlements** in one 1:250k crop — against **23 OSM places** in
  the whole 8 °N band today.
* **7–8 °N, 24.7 °E (upper Chinko)** — `Bamboo forest`, `Open forest`, named
  jebels (`Dj. Ngoug 3,342'`, `Dj. Am-Bayaba`), `Very hilly` and a French-CAR
  boundary note; the Chinko headwaters are mapped in detail with no settlement
  at all, which matches the modern zero-settlement reading for the park core.

**Operational value:** the 1930s sheets give you a named, dated inventory of
**dry-season water points and tribal grazing territories** for exactly the
northern belt where the modern data is a blank. Any transhumance dialogue in
the north should start from these names — they are still the names in use.

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
   indistinguishable from fire near random ground. Water points, not
   settlements, are the leverage — and the 1930s sheets name them.
6. **Engage by direction, not by park.** Chinko's incursions come from the west,
   Garamba's from the east, Bili-Uere's from the west and south. Those are
   different communities.
7. **Do not quote the population or built-up-area numbers** until §4.1 is fixed
   in the pipeline (see `docs/AOI_STRUCTURAL_FIXES.md`). Everything else here
   is safe to publish with its stated caveat.

---

### Reproduce
`/s/cf4ftqj` · AOI `XSA_Study_Area` · 11/11 datasets `done`
(3.18 M detections → 38,725 trajectories · 76,903 Hansen polygons → 7,079
events · 2.23 M GFW alerts → 696 events · 74,904 built-up polygons → 1,552
clusters · 12,956 roads · 971 places · 46 watersheds)
