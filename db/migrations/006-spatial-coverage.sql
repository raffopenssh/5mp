-- Add spatial coverage tracking to effort_data
-- Coverage is calculated as percentage of 1km x 1km sub-cells visited within the 10km x 10km pixel
ALTER TABLE effort_data ADD COLUMN coverage_percent REAL DEFAULT 0;

-- Table to track which sub-cells have been visited within each grid cell
-- Day granularity for time filtering and intensity calculation
CREATE TABLE IF NOT EXISTS subcell_visits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    grid_cell_id TEXT NOT NULL,
    subcell_id TEXT NOT NULL, -- format: "row_col" within the 10x10 grid (0-9, 0-9)
    visit_date DATE NOT NULL, -- specific day of visit
    visit_count INTEGER NOT NULL DEFAULT 1,
    FOREIGN KEY (grid_cell_id) REFERENCES grid_cells(id),
    UNIQUE(grid_cell_id, subcell_id, visit_date)
);

CREATE INDEX IF NOT EXISTS idx_subcell_visits_grid ON subcell_visits(grid_cell_id);
CREATE INDEX IF NOT EXISTS idx_subcell_visits_date ON subcell_visits(visit_date);
