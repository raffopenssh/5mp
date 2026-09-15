# fire_vanguard — prototype (2026-09-14)

**Shipped 2026-09-15:** the round-2 winner (`van3.py SEED`, "KF_seed_gap3") is
now production code in `scripts/fire_vanguard_kf.py` — the vanguard layer's
population wherever it has run (`docs/agents/fire.md` § Kalman seed-ahead
chains; harness `eval_fire_vanguard.py --tracker kf`). Everything below is the
measurement record; the scripts here still read `/tmp/fx/` and are not wired.


Question: can the day order of fires along a line be recovered from VIIRS in the
XSA, and if so where? Answer, measured against the day-shuffled null
(`eval_fire_null.shuffle_days`): **only ahead of the season front.**

All scripts read/write `/tmp/fx/` (scratch; `load.py`, `load_hist.py`,
`load_lines.py`, `load_ctx.py` fill it from `db.sqlite3`). Run in that order,
then whichever analysis you need. Noisy NumPy-1.x import warnings are harmless.

| script | what it measures | result on XSA 2024/25 |
|---|---|---|
| `onset.py`, `resid.py` | first-burn day per 2.5 km cell; regional **season front** (20th pct in 60 km); anomaly = cell − front | front sweeps E→W Nov→Jan, same both seasons; thin early streaks visible |
| `recur.py` | cells ≥15 d early in ≥40 % of seasons 2018–2025 (history = park catchments only) | 322 cells early in ≥4 of ≥6 seasons vs ~13 by chance |
| `synth.py` | astronomy shift-and-stack on fresh-ignition frames (1,214 velocity hypotheses) | **fails**: 13.8k movers real vs 13.2k shuffled — multiple-comparison floor |
| `pair.py` | space-time two-point function ξ(r, Δt) of fresh ignitions | real signal only at 0–15 km / 1–2 days; none at 20+ km/day |
| `drift.py`, `coh.py` | next-day ignition drift field, real − null bias; neighbour coherence | coherence 0.03–0.18 (noise-level except Oct–Nov) |
| `streaks.py` | Spearman(date, position) along early-burn streaks; block-shuffled nulls | real ≈ null at 3/7/30-day blocks → no sub-month order in streaks |
| **`van_null.py L`** | production tracker on detections ≥L days ahead of the front, real vs shuffled | **skill 0.42–0.51 on links for L=5…25** (whole field: ~0). L=10: 611 chains, 2,859 vs 1,558 links, 29 vs 19 km |
| `van_tier.py`, `van_dir.py` | per-chain FDR by (days, km); regional mean direction vs null | FDR ≈0.5 at every cut (no per-chain arrows yet); 3 cells z>3 |
| `render.py` | the figure: front isochrones + grey app lines + vanguard chains + recurring ground | `/tmp/fx/xsa_render*.png` |

## Round 2 (2026-09-14 pm): clouds, scars, and the Kalman follow-through

| script | what it measures | result on XSA 2024/25 |
|---|---|---|
| `vis.py`, `vis2.py` | **self-derived visibility map** V(day, 0.1° cell): share of cells that burned in d−3..d−1 *and* d+1..d+3 but reported nothing on d (astronomy's exposure map, particle physics' dead layer). No external cloud data. | Sep–Oct: 54–79 % of expected-active cells dark on a median day (rainy-season tail = the vanguard months); Dec: 34 %. Whole-sensor outages visible (SNPP off 3–6 Nov 2024, N21 off 31 Oct). |
| `ends.py` | why chains end: V in the 3 days after the end; already-burned land within 10 km; restart candidate ahead | vanguard chains: **28 % end into a dark spell** (own days 13 %); **43 % end because the front caught up** (lead ≤ 13 at the end = cut by the lead≥10 filter); scar around the end only 15 % → scar is *not* what cuts vanguard chains. In-season lines: 80 % burned around both start and end (saturated). |
| `van2.py` | production tracker + gap budget counted in *seen* days (coast through cloud), gate grows 8 km/day | **rejected**: real links 2,859→4,010 but null 1,558→2,870; skill 0.46→0.26, long-front skill negative. Opening the gate is not the fix. |
| `van3.py KF` | **Kalman-filter tracker** (state x,y,vx,vy; walker noise σv0 5 km/d, q 1.5; Mahalanobis gate χ²<9.21; measurement var from cluster spread/n) on the lead≥10 field, gap ≤3 as production | links 3,164 vs 1,670 (**skill 0.47**), long chains 40 vs 14, fires in long chains 1,287 vs 326 (**0.75**; production 384 vs 101). Same link skill as production, 5× the long chains at the same null ratio. |
| `van3.py KFV` | + cloud days are dead layers (holes only where V≥0.35), coast ≤10 d | **rejected**: skill 0.23; links by gap 1/2/3/4/5+ real 2,226/1,336/1,078/821/801 vs null 1,109/942/1,020/997/1,550 — beyond 2 missed days the next sighting is somebody else's fire. A daily 375 m sensor cannot bridge a cloud spell at this density, whatever the covariance. |
| `van3.py SEED` | **auto-vanguard**: tracks may be *born* only at lead≥10, then followed into the arriving season (field lead≥−10) | `KF_seed_gap3`: links 11,822 vs 8,388 (0.29), km/chain 76 vs 48, fires in ≥150 km chains 24,567 vs 9,714 (**0.60**), consecutive-day links 8,153 vs 4,728. Fixes the 43 % "front caught up" truncation. Regime rule "no missed day once in season" **backfires** (0.05): a bridge keeps a real track alive for more consecutive-day hits, so per-gap link counts are not a clean LR — judge on the aggregate null only. |
| `render3.py` | app-style figure: red lines as today + auto-vanguard chains coloured by lead, clipped where the season arrives, dashed = unseen day, blue ring = lost under cloud | `/tmp/fx/www/xsa_autovanguard_2024*.png` |

Bottom line: clouds **do** cut trajectories (measured), but the honest response is to *say so* (blue ring, dashed day) — not to extrapolate; scars do not cut vanguard chains, the season front does, and seeding-ahead + KF follow-through repairs that with measured skill.
