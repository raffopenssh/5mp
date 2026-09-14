# fire_vanguard — prototype (2026-09-14), NOT wired into the app

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
