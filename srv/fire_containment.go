package srv

// Fire containment: `protected_area_id` is a catchment, not a park.
//
// `park_assigner.ASSIGN_MAX_DIST_KM = 100`, so `protected_area_id = X` selects
// every detection whose *nearest* park boundary is X — including detections up
// to 100 km outside it. Measured over all 163 parks (2026-08-13,
// `scripts/audit_fire_containment.py`): 42,092,853 rows tagged vs 7,585,655
// actually inside a boundary, a per-park median overstatement of 9.8x, and
// seven rainforest parks (CMR_Nki among them) whose entire "fire count" is
// somebody else's savanna.
//
// `in_protected_area` is the ingest-time point-in-polygon answer
// (`dist_km == 0.0`). It is ~94% accurate: 5.83% of flagged rows are not
// inside today's polygon, 92% of that error being one identifiable batch
// (2026-02-26 -> 2026-07-03, written by the bbox+0.5deg `_find_park` that
// `ParkAssigner` replaced in 858eb69). Re-deriving the flag is a separate
// change; `scripts/audit_fire_containment.py` is the before/after instrument.
//
// Every user-facing "fires in park X" count must append fireInsideSQL to a
// `protected_area_id = ?` predicate. Counts that deliberately want the
// catchment (there are none in srv/ today) must say so in a comment.
//
// The `+` is load-bearing (invariant 3's family): without it SQLite prefers
// `idx_fire_infraction (in_protected_area, acq_date)` and scans 8M rows —
// 0.2 s becomes 18 s (Kafue: 14.2 s). The unary `+` makes the term
// non-indexable, so the planner keeps `idx_fire_pa_date`.
const fireInsideSQL = " AND +in_protected_area = 1"

// fireInsideOnlySQL is the same restriction for queries that have no
// `protected_area_id = ?` equality to protect — a global GROUP BY, where the
// planner has nothing better to pick anyway.
const fireInsideOnlySQL = " AND in_protected_area = 1"
