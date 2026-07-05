-- 035: test environment tenant isolation.
-- Adds env column ('prod'|'test') to upload/effort tables so the test2026
-- password gets an isolated pixel/upload tenant.

ALTER TABLE gpx_uploads ADD COLUMN env TEXT NOT NULL DEFAULT 'prod';
ALTER TABLE upload_queue ADD COLUMN env TEXT NOT NULL DEFAULT 'prod';
ALTER TABLE notifications ADD COLUMN env TEXT NOT NULL DEFAULT 'prod';
ALTER TABLE track_points ADD COLUMN env TEXT NOT NULL DEFAULT 'prod';

-- effort_data: rebuild to include env in the UNIQUE constraint (needed for upserts)
CREATE TABLE effort_data_new (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    grid_cell_id TEXT NOT NULL,
    year INTEGER NOT NULL,
    month INTEGER NOT NULL, -- 1-12
    day INTEGER, -- NULL for monthly/annual aggregation
    movement_type TEXT NOT NULL, -- 'foot', 'vehicle', 'aircraft', 'all'
    total_distance_km REAL NOT NULL DEFAULT 0,
    total_points INTEGER NOT NULL DEFAULT 0,
    unique_uploads INTEGER NOT NULL DEFAULT 0,
    protected_area_ids TEXT,
    coverage_percent REAL DEFAULT 0,
    avg_speed_kmh REAL,
    avg_altitude_m REAL,
    env TEXT NOT NULL DEFAULT 'prod',
    FOREIGN KEY (grid_cell_id) REFERENCES grid_cells(id),
    UNIQUE(grid_cell_id, year, month, day, movement_type, env)
);
INSERT INTO effort_data_new (id, grid_cell_id, year, month, day, movement_type, total_distance_km, total_points, unique_uploads, protected_area_ids, coverage_percent, avg_speed_kmh, avg_altitude_m, env)
SELECT id, grid_cell_id, year, month, day, movement_type, total_distance_km, total_points, unique_uploads, protected_area_ids, coverage_percent, avg_speed_kmh, avg_altitude_m, 'prod' FROM effort_data;
DROP TABLE effort_data;
ALTER TABLE effort_data_new RENAME TO effort_data;
CREATE INDEX idx_effort_data_grid ON effort_data(grid_cell_id);
CREATE INDEX idx_effort_data_time ON effort_data(year, month, day);

-- subcell_visits: rebuild to include env in the UNIQUE constraint
CREATE TABLE subcell_visits_new (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    grid_cell_id TEXT NOT NULL,
    subcell_id TEXT NOT NULL, -- format: "row_col" within the 10x10 grid (0-9, 0-9)
    visit_date DATE NOT NULL, -- specific day of visit
    visit_count INTEGER NOT NULL DEFAULT 1,
    env TEXT NOT NULL DEFAULT 'prod',
    FOREIGN KEY (grid_cell_id) REFERENCES grid_cells(id),
    UNIQUE(grid_cell_id, subcell_id, visit_date, env)
);
INSERT INTO subcell_visits_new (id, grid_cell_id, subcell_id, visit_date, visit_count, env)
SELECT id, grid_cell_id, subcell_id, visit_date, visit_count, 'prod' FROM subcell_visits;
DROP TABLE subcell_visits;
ALTER TABLE subcell_visits_new RENAME TO subcell_visits;
CREATE INDEX idx_subcell_visits_grid ON subcell_visits(grid_cell_id);
CREATE INDEX idx_subcell_visits_date ON subcell_visits(visit_date);

CREATE INDEX idx_effort_data_env ON effort_data(env);
CREATE INDEX idx_gpx_uploads_env ON gpx_uploads(env);
