-- 041: which protected areas an AOI actually overlaps, and by how much.
--
-- This is the AOI's most-used fact — the popup's Overview links to these
-- parks, aoi_clip.py clips from exactly them, and the report folds them in —
-- and it is a real polygon intersection that must not be re-derived per
-- request. Doing it client-side would need all 163 boundaries in JS; doing it
-- in Go would need a geometry library the server does not have; approximating
-- it by bbox is wrong precisely where it matters (a park can share an AOI's
-- bbox and touch none of it).
--
-- So it is computed once by shapely in scripts/aoi_clip.py and stored here.
--
-- Two different fractions, because both questions get asked:
--   frac_of_aoi  — how much of the AOI this park covers (the coverage story:
--                  10.5% of XSA is inside any park at all)
--   frac_of_park — how much of the park is inside the AOI (the containment
--                  story: CAF_Chinko 100% = contained, COD_Garamba 13% =
--                  clipped at the edge)
CREATE TABLE IF NOT EXISTS aoi_parks (
  aoi_id       TEXT NOT NULL,
  park_id      TEXT NOT NULL,
  frac_of_aoi  REAL NOT NULL DEFAULT 0,
  frac_of_park REAL NOT NULL DEFAULT 0,
  PRIMARY KEY (aoi_id, park_id)
) WITHOUT ROWID;
