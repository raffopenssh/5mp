-- 056: fire_sensor_epochs — how many instruments were watching, per month.
--
-- WHY THIS EXISTS (F11, docs/AOI_STRUCTURAL_FIXES.md). Every raw fire chart in
-- this app has a step at 2024-01-01 that is the SATELLITE FLEET, not the
-- landscape. One VIIRS sensor (Suomi-NPP, `N`) flies before 2024; three fly
-- after (`N`, `N20`, `N21`). CAF_Chinko's tagged detections go 61,509 in 2023
-- to 203,223 in 2024 — a 3.3x "increase in burning" of which the fleet
-- explains almost all. A reader shown that line concludes fire pressure
-- tripled, and nothing on the chart contradicts them. Same family as F8: two
-- adjacent points measuring different quantities must not be joined by a line
-- (AGENTS.md invariant 7).
--
-- WHY A TABLE AND NOT A CONSTANT. "Three sensors since 2024" is exactly the
-- hardcoded count invariant 2 forbids: it describes an ingest history that
-- grows every night, and the next sensor (or a decommissioned one, or a
-- country CSV imported from a different product) moves it without touching any
-- code. So it is MEASURED from the archive by
-- scripts/build_sensor_epochs.py and stored per month, with the detection
-- count that backs it.
--
-- WHY MONTHLY, AND WHY GLOBAL. Per-park-per-week distinct-satellite counts are
-- a sampling artefact: a quiet week in a small park shows one sensor because
-- only one fire burned, not because two stopped flying. The fleet is a
-- property of the ARCHIVE, so it is counted over all detections and at a
-- coarse enough grain that a thin month cannot fake a fleet change.
--
-- `detections` is what makes a row falsifiable: a month with 40 detections
-- naming one sensor is weak evidence and the reader can see that it is.
CREATE TABLE IF NOT EXISTS fire_sensor_epochs (
    month        TEXT PRIMARY KEY,   -- 'YYYY-MM'
    sensors      TEXT NOT NULL,      -- comma-separated, sorted: 'N,N20,N21'
    sensor_count INTEGER NOT NULL,
    detections   INTEGER NOT NULL,
    computed_at  TEXT NOT NULL
);
