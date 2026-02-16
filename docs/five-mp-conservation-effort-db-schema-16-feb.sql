CREATE TABLE migrations (
    migration_number INTEGER PRIMARY KEY,
    migration_name TEXT NOT NULL,
    executed_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE users (
    id TEXT PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL DEFAULT '',
    organization TEXT NOT NULL DEFAULT '',
    organization_type TEXT NOT NULL DEFAULT '', -- 'government', 'nonprofit', 'protected_area_manager'
    role TEXT NOT NULL DEFAULT 'pending', -- 'pending', 'approved', 'admin'
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    approved_at TIMESTAMP,
    approved_by TEXT
, password_hash TEXT NOT NULL DEFAULT '');
CREATE TABLE gpx_uploads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    filename TEXT NOT NULL,
    movement_type TEXT NOT NULL, -- 'foot', 'vehicle', 'aircraft'
    protected_area_id TEXT, -- WDPA ID if associated with a protected area
    upload_date TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    start_time TIMESTAMP,
    end_time TIMESTAMP,
    total_distance_km REAL NOT NULL DEFAULT 0,
    total_points INTEGER NOT NULL DEFAULT 0, file_hash TEXT, processing_status TEXT DEFAULT 'pending', error_message TEXT,
    FOREIGN KEY (user_id) REFERENCES users(id)
);
CREATE TABLE sqlite_sequence(name,seq);
CREATE TABLE grid_cells (
    id TEXT PRIMARY KEY, -- format: "lat_lon" e.g. "12.3_45.6"
    lat_center REAL NOT NULL,
    lon_center REAL NOT NULL,
    lat_min REAL NOT NULL,
    lat_max REAL NOT NULL,
    lon_min REAL NOT NULL,
    lon_max REAL NOT NULL
);
CREATE TABLE effort_data (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    grid_cell_id TEXT NOT NULL,
    year INTEGER NOT NULL,
    month INTEGER NOT NULL, -- 1-12
    day INTEGER, -- NULL for monthly/annual aggregation
    movement_type TEXT NOT NULL, -- 'foot', 'vehicle', 'aircraft', 'all'
    total_distance_km REAL NOT NULL DEFAULT 0,
    total_points INTEGER NOT NULL DEFAULT 0,
    unique_uploads INTEGER NOT NULL DEFAULT 0,
    protected_area_ids TEXT, coverage_percent REAL DEFAULT 0, -- JSON array of WDPA IDs
    FOREIGN KEY (grid_cell_id) REFERENCES grid_cells(id),
    UNIQUE(grid_cell_id, year, month, day, movement_type)
);
CREATE TABLE track_points (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    upload_id INTEGER NOT NULL,
    lat REAL NOT NULL,
    lon REAL NOT NULL,
    elevation REAL,
    timestamp TIMESTAMP,
    grid_cell_id TEXT,
    FOREIGN KEY (upload_id) REFERENCES gpx_uploads(id),
    FOREIGN KEY (grid_cell_id) REFERENCES grid_cells(id)
);
CREATE TABLE visitors (
    id TEXT PRIMARY KEY,
    view_count INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_seen TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE sessions (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);
CREATE TABLE pa_publications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pa_id TEXT NOT NULL,          -- WDPA ID or internal PA ID
    openalex_id TEXT NOT NULL,    -- OpenAlex work ID for deduplication
    title TEXT NOT NULL,
    authors TEXT,                 -- JSON array of author names
    year INTEGER,
    doi TEXT,
    url TEXT,
    abstract TEXT,
    cited_by_count INTEGER DEFAULT 0,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(pa_id, openalex_id)
);
CREATE TABLE pa_publication_sync (
    pa_id TEXT PRIMARY KEY,
    last_sync TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    result_count INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE park_checklist (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pa_id TEXT NOT NULL,
    item_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending', -- pending, in_progress, complete, not_applicable
    notes TEXT,
    document_url TEXT,
    updated_by TEXT,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(pa_id, item_id)
);
CREATE TABLE park_documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pa_id TEXT NOT NULL,
    category TEXT NOT NULL,
    item_id TEXT,
    title TEXT NOT NULL,
    description TEXT,
    file_url TEXT,
    file_type TEXT,
    uploaded_by TEXT,
    uploaded_at DATETIME DEFAULT CURRENT_TIMESTAMP
, year INTEGER, summary TEXT);
CREATE TABLE subcell_visits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    grid_cell_id TEXT NOT NULL,
    subcell_id TEXT NOT NULL, -- format: "row_col" within the 10x10 grid (0-9, 0-9)
    visit_date DATE NOT NULL, -- specific day of visit
    visit_count INTEGER NOT NULL DEFAULT 1,
    FOREIGN KEY (grid_cell_id) REFERENCES grid_cells(id),
    UNIQUE(grid_cell_id, subcell_id, visit_date)
);
CREATE TABLE fire_detections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    latitude REAL NOT NULL,
    longitude REAL NOT NULL,
    brightness REAL,           -- Brightness temperature (Kelvin)
    scan REAL,                 -- Scan pixel size
    track REAL,                -- Track pixel size
    acq_date TEXT NOT NULL,    -- Acquisition date (YYYY-MM-DD)
    acq_time TEXT,             -- Acquisition time (HHMM)
    satellite TEXT,            -- VIIRS sensor: N (Suomi NPP), 1 (NOAA-20), 2 (NOAA-21)
    instrument TEXT,           -- VIIRS
    confidence TEXT,           -- low, nominal, high
    version TEXT,              -- Processing version
    bright_t31 REAL,           -- Brightness temp channel 31
    frp REAL,                  -- Fire Radiative Power (MW)
    daynight TEXT,             -- D=day, N=night
    
    -- Computed fields for efficient queries
    grid_cell_id TEXT,         -- References grid_cells table
    in_protected_area INTEGER DEFAULT 0,  -- 1 if inside any keystone PA
    protected_area_id TEXT,    -- Keystone PA ID if inside
    
    UNIQUE(latitude, longitude, acq_date, acq_time, satellite)
);
CREATE TABLE fire_data_sync (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    park_id TEXT NOT NULL,          -- Keystone PA ID
    bbox_west REAL NOT NULL,
    bbox_south REAL NOT NULL,
    bbox_east REAL NOT NULL,
    bbox_north REAL NOT NULL,
    buffer_km REAL NOT NULL DEFAULT 50,
    first_date TEXT,                -- Earliest date we have data for
    last_date TEXT,                 -- Latest date we have data for
    last_sync_at TEXT,
    total_detections INTEGER DEFAULT 0,
    UNIQUE(park_id)
);
CREATE TABLE fire_daily_grid (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    grid_cell_id TEXT NOT NULL,
    date TEXT NOT NULL,             -- YYYY-MM-DD
    fire_count INTEGER NOT NULL DEFAULT 0,
    total_frp REAL DEFAULT 0,       -- Sum of Fire Radiative Power
    avg_confidence REAL,
    in_protected_area INTEGER DEFAULT 0,
    protected_area_id TEXT,
    UNIQUE(grid_cell_id, date),
    FOREIGN KEY (grid_cell_id) REFERENCES grid_cells(id)
);
CREATE TABLE park_fire_analysis (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            park_id TEXT NOT NULL,
            year INTEGER NOT NULL,
            total_fires INTEGER,
            dry_season_fires INTEGER,
            transhumance_groups INTEGER,
            transhumance_fires INTEGER,
            avg_transhumance_speed REAL,
            herder_groups INTEGER,
            management_groups INTEGER,
            village_groups INTEGER,
            peak_month INTEGER,
            analysis_json TEXT,
            analyzed_at DATETIME DEFAULT CURRENT_TIMESTAMP, total_infractions INTEGER DEFAULT 0, infraction_rate REAL DEFAULT 0, peak_infraction_day TEXT, peak_infraction_count INTEGER DEFAULT 0, net_south_km REAL DEFAULT 0, avg_daily_movement_km REAL DEFAULT 0, monthly_stats_json TEXT,
            UNIQUE(park_id, year)
        );
CREATE TABLE park_group_infractions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            park_id TEXT NOT NULL,
            year INTEGER NOT NULL,
            total_groups INTEGER,
            transhumance_groups INTEGER,
            herder_groups INTEGER,
            avg_days_burning REAL,
            median_days_burning REAL,
            max_days_burning INTEGER,
            total_fires_inside INTEGER,
            groups_transited INTEGER,
            groups_stopped_inside INTEGER,
            groups_stopped_after INTEGER,
            avg_days_tracked_before REAL,
            avg_days_tracked_after REAL,
            trajectories_json TEXT,
            analyzed_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(park_id, year)
        );
CREATE TABLE park_ghsl_data (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                park_id TEXT NOT NULL,
                year INTEGER NOT NULL,
                built_up_km2 REAL,
                population_estimate REAL,
                data_source TEXT DEFAULT 'GHSL_R2023A',
                processed_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(park_id, year)
            );
CREATE TABLE ghsl_tiles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tile_id TEXT NOT NULL UNIQUE,
                row INTEGER NOT NULL,
                col INTEGER NOT NULL,
                product TEXT NOT NULL,
                year INTEGER NOT NULL,
                resolution INTEGER NOT NULL,
                filepath TEXT,
                downloaded_at DATETIME
            );
CREATE TABLE osm_roadless_data (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    park_id TEXT NOT NULL UNIQUE,
    total_area_km2 REAL,
    roaded_area_km2 REAL,
    roadless_area_km2 REAL,
    roadless_percentage REAL,
    road_length_km REAL,
    road_density_km_per_km2 REAL,
    buffer_distance_m INTEGER,
    road_types_used TEXT,
    osm_query_timestamp DATETIME,
    processed_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    error_message TEXT
, roads_json TEXT, buffer_roads_json TEXT);
CREATE TABLE ghsl_data (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    park_id TEXT NOT NULL,
    built_up_km2 REAL DEFAULT 0,         -- Total built-up area in km²
    settlement_count INTEGER DEFAULT 0,   -- Number of distinct settlements
    analyzed_at TEXT,
    UNIQUE(park_id)
);
CREATE TABLE osm_places (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    park_id TEXT NOT NULL,
    place_type TEXT NOT NULL,
    name TEXT NOT NULL,
    lat REAL NOT NULL,
    lon REAL NOT NULL,
    geojson TEXT,
    osm_id TEXT,
    osm_tags TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE deforestation_clusters (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    park_id TEXT NOT NULL,
    year INTEGER NOT NULL,
    cluster_id INTEGER NOT NULL,
    area_km2 REAL NOT NULL,
    lat REAL NOT NULL,
    lon REAL NOT NULL,
    pattern_type TEXT,
    geojson TEXT,
    description TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE park_settlements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    park_id TEXT NOT NULL,
    lat REAL NOT NULL,
    lon REAL NOT NULL,
    area_m2 REAL DEFAULT 0,
    population_est INTEGER DEFAULT 0,
    households_est INTEGER DEFAULT 0,
    nearest_place TEXT,
    distance_to_place_km REAL,
    direction_from_place TEXT,
    settlement_type TEXT CHECK(settlement_type IN ('temporary', 'permanent')),
    in_buffer INTEGER DEFAULT 0,
    tile_row INTEGER,
    tile_col INTEGER,
    detected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, classification TEXT, classification_confidence REAL, narrative TEXT, fires_5km INTEGER DEFAULT 0, fire_seasonality TEXT, deforest_nearby_km2 REAL DEFAULT 0, classified_at TIMESTAMP, polygon_ids TEXT,
    UNIQUE(park_id, lat, lon)
);
CREATE TABLE gpx_upload_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    upload_id INTEGER REFERENCES gpx_uploads(id),
    user_id TEXT NOT NULL,
    user_email TEXT,
    filename TEXT NOT NULL,
    upload_time TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    
    -- Validation results
    is_valid BOOLEAN NOT NULL DEFAULT 1,
    total_points INTEGER NOT NULL DEFAULT 0,
    validation_errors TEXT, -- JSON array
    validation_warnings TEXT, -- JSON array
    
    -- Protected area detection
    protected_area_id TEXT,
    protected_area_name TEXT,
    
    -- Classification stats (in km)
    patrol_km REAL NOT NULL DEFAULT 0,
    road_km REAL NOT NULL DEFAULT 0,
    boundary_km REAL NOT NULL DEFAULT 0,
    excluded_km REAL NOT NULL DEFAULT 0,
    
    -- Segment counts
    total_segments INTEGER NOT NULL DEFAULT 0,
    patrol_segments INTEGER NOT NULL DEFAULT 0,
    static_segments INTEGER NOT NULL DEFAULT 0,
    excluded_segments INTEGER NOT NULL DEFAULT 0,
    
    -- Store classified segments as JSON (not original file)
    classified_segments_json TEXT,
    
    -- Processing status
    processing_status TEXT DEFAULT 'completed', -- completed, rejected, partial
    rejection_reason TEXT
, foot_segments INTEGER DEFAULT 0, foot_km REAL DEFAULT 0, foot_minutes REAL DEFAULT 0, vehicle_segments INTEGER DEFAULT 0, vehicle_km REAL DEFAULT 0, vehicle_minutes REAL DEFAULT 0, aircraft_segments INTEGER DEFAULT 0, aircraft_km REAL DEFAULT 0, aircraft_minutes REAL DEFAULT 0, recon_segments INTEGER DEFAULT 0, recon_km REAL DEFAULT 0, recon_minutes REAL DEFAULT 0, fast_vehicle_segments INTEGER DEFAULT 0, fast_vehicle_km REAL DEFAULT 0, fast_vehicle_minutes REAL DEFAULT 0, transit_segments INTEGER DEFAULT 0, transit_km REAL DEFAULT 0, logistics_segments INTEGER DEFAULT 0, logistics_km REAL DEFAULT 0);
CREATE TABLE park_vehicle_stats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    park_id TEXT NOT NULL,
    movement_type TEXT NOT NULL, -- 'vehicle', 'foot', 'aircraft_fixed', 'aircraft_rotor'
    total_distance_km REAL DEFAULT 0,
    total_time_hours REAL DEFAULT 0,
    median_speed_kmh REAL DEFAULT 0,
    max_speed_kmh REAL DEFAULT 0,
    p90_speed_kmh REAL DEFAULT 0,
    sample_count INTEGER DEFAULT 0,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(park_id, movement_type)
);
CREATE TABLE park_patrol_mcp (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    park_id TEXT NOT NULL,
    mcp_90_geojson TEXT, -- 90% MCP as GeoJSON polygon
    mcp_area_km2 REAL DEFAULT 0,
    centroid_lat REAL,
    centroid_lon REAL,
    point_count INTEGER DEFAULT 0,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(park_id)
);
CREATE TABLE learned_roads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    park_id TEXT NOT NULL,
    geojson TEXT NOT NULL, -- Simplified LineString (10m)
    length_m REAL DEFAULT 0,
    match_count INTEGER DEFAULT 1, -- How many times vehicles used this track
    confidence_pct REAL DEFAULT 0, -- Certainty percentage
    status TEXT DEFAULT 'pending', -- 'pending', 'approved', 'rejected'
    approved_by TEXT,
    approved_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE learned_airstrips (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    park_id TEXT NOT NULL,
    lat REAL NOT NULL,
    lon REAL NOT NULL,
    heading_deg REAL, -- Primary runway heading
    length_m REAL DEFAULT 0, -- Estimated runway length
    aircraft_type TEXT DEFAULT 'mixed', -- 'fixed_wing', 'rotor_wing', 'mixed'
    landing_count INTEGER DEFAULT 0,
    takeoff_count INTEGER DEFAULT 0,
    confidence_pct REAL DEFAULT 0,
    status TEXT DEFAULT 'pending',
    approved_by TEXT,
    approved_at TIMESTAMP,
    approach_json TEXT, -- First/last 2000m of approaches
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE learned_places (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    park_id TEXT NOT NULL,
    lat REAL NOT NULL,
    lon REAL NOT NULL,
    place_type TEXT, -- 'headquarters', 'outpost', 'camp', 'gate', 'unknown'
    name TEXT,
    visit_count INTEGER DEFAULT 0,
    avg_duration_minutes REAL DEFAULT 0,
    confidence_pct REAL DEFAULT 0,
    status TEXT DEFAULT 'pending',
    approved_by TEXT,
    approved_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE vehicle_tracks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    park_id TEXT NOT NULL,
    upload_id INTEGER REFERENCES gpx_uploads(id),
    geojson TEXT NOT NULL, -- Simplified LineString
    length_m REAL DEFAULT 0,
    movement_type TEXT DEFAULT 'vehicle',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE aircraft_patterns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    park_id TEXT NOT NULL,
    upload_id INTEGER REFERENCES gpx_uploads(id),
    pattern_type TEXT NOT NULL, -- 'approach', 'departure'
    aircraft_type TEXT, -- 'fixed_wing', 'rotor_wing'
    start_lat REAL,
    start_lon REAL,
    end_lat REAL,
    end_lon REAL,
    geojson TEXT, -- LineString of the pattern
    avg_speed_kmh REAL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE dataset_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    park_id TEXT NOT NULL,
    dataset_type TEXT NOT NULL, -- 'roads', 'places', 'airstrips'
    version INTEGER NOT NULL,
    data_json TEXT NOT NULL, -- Full snapshot of the dataset
    created_by TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    is_active INTEGER DEFAULT 0
);
CREATE TABLE gpx_learning_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    upload_id INTEGER REFERENCES gpx_uploads(id),
    park_id TEXT,
    status TEXT DEFAULT 'pending', -- 'pending', 'processing', 'completed', 'failed'
    error_message TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    started_at TIMESTAMP,
    completed_at TIMESTAMP
);
CREATE TABLE gpx_learning_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    upload_id INTEGER REFERENCES gpx_uploads(id),
    park_id TEXT NOT NULL,
    park_name TEXT,
    
    -- Speed stats discovered
    vehicle_median_speed_kmh REAL,
    vehicle_max_speed_kmh REAL,
    foot_median_speed_kmh REAL,
    foot_max_speed_kmh REAL,
    
    -- Area stats
    foot_mcp_area_km2 REAL,
    
    -- Discoveries
    new_roads_found INTEGER DEFAULT 0,
    new_roads_km REAL DEFAULT 0,
    road_confidence_pct REAL,
    
    new_airstrips_found INTEGER DEFAULT 0,
    airstrip_confidence_pct REAL,
    
    new_places_found INTEGER DEFAULT 0,
    place_types_json TEXT, -- {"headquarters": 1, "outpost": 2, "camp": 3}
    place_confidence_pct REAL,
    
    -- Summary for admin
    summary_text TEXT,
    discoveries_json TEXT, -- Full details of all discoveries
    
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE learned_roads_history (
    history_id INTEGER PRIMARY KEY AUTOINCREMENT,
    original_id INTEGER NOT NULL,
    park_id TEXT NOT NULL,
    geojson TEXT NOT NULL,
    distance_km REAL,
    match_count INTEGER DEFAULT 1,
    confidence REAL DEFAULT 0.25,
    is_approved INTEGER DEFAULT 0,
    is_rejected INTEGER DEFAULT 0,
    version INTEGER NOT NULL,
    action TEXT NOT NULL,  -- 'create', 'update', 'approve', 'reject', 'rollback'
    action_by TEXT,
    action_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE learned_airstrips_history (
    history_id INTEGER PRIMARY KEY AUTOINCREMENT,
    original_id INTEGER NOT NULL,
    park_id TEXT NOT NULL,
    lat REAL NOT NULL,
    lon REAL NOT NULL,
    heading_deg REAL,
    length_m REAL,
    aircraft_type TEXT,
    landing_count INTEGER DEFAULT 1,
    confidence REAL DEFAULT 0.25,
    is_approved INTEGER DEFAULT 0,
    is_rejected INTEGER DEFAULT 0,
    version INTEGER NOT NULL,
    action TEXT NOT NULL,
    action_by TEXT,
    action_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE learned_places_history (
    history_id INTEGER PRIMARY KEY AUTOINCREMENT,
    original_id INTEGER NOT NULL,
    park_id TEXT NOT NULL,
    lat REAL NOT NULL,
    lon REAL NOT NULL,
    place_type TEXT,
    visit_count INTEGER DEFAULT 1,
    avg_duration_minutes REAL,
    confidence REAL DEFAULT 0.25,
    is_approved INTEGER DEFAULT 0,
    is_rejected INTEGER DEFAULT 0,
    version INTEGER NOT NULL,
    action TEXT NOT NULL,
    action_by TEXT,
    action_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE fire_group_alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    park_id TEXT NOT NULL,
    group_name TEXT NOT NULL,  -- NATO phonetic name (Alpha, Bravo, etc.)
    alert_type TEXT NOT NULL,  -- 'entered', 'active_inside', 'left'
    first_detected_at TIMESTAMP NOT NULL,
    last_updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    left_at TIMESTAMP,  -- When group left the park (NULL if still inside)
    fire_count INTEGER DEFAULT 0,
    days_active INTEGER DEFAULT 1,
    centroid_lat REAL,
    centroid_lon REAL,
    latest_lat REAL,
    latest_lon REAL,
    movement_direction TEXT,  -- 'north', 'south', 'east', 'west', 'stationary'
    is_dismissed BOOLEAN DEFAULT FALSE,  -- User can dismiss alerts
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    
    UNIQUE(park_id, group_name, first_detected_at)
);
CREATE TABLE settlement_visits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    settlement_id INTEGER NOT NULL REFERENCES park_settlements(id),
    upload_id INTEGER REFERENCES gpx_uploads(id),
    park_id TEXT NOT NULL,
    
    -- Visit timing
    visit_date DATE NOT NULL,
    visit_start TIMESTAMP,
    visit_end TIMESTAMP,
    duration_minutes REAL NOT NULL DEFAULT 0,
    
    -- Movement context
    movement_type TEXT NOT NULL, -- 'foot', 'vehicle', 'aircraft'
    arriving_from TEXT, -- direction or place name
    departing_to TEXT,
    
    -- Aggregation helpers
    year INTEGER NOT NULL,
    month INTEGER NOT NULL,
    
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(settlement_id, upload_id, visit_start)
);
CREATE TABLE settlement_intensity (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    settlement_id INTEGER NOT NULL REFERENCES park_settlements(id),
    park_id TEXT NOT NULL,
    
    -- Time period
    year INTEGER NOT NULL,
    month INTEGER, -- NULL for annual aggregation
    
    -- Intensity metrics
    total_visits INTEGER NOT NULL DEFAULT 0,
    total_duration_minutes REAL NOT NULL DEFAULT 0,
    unique_uploads INTEGER NOT NULL DEFAULT 0,
    
    -- Movement breakdown
    foot_visits INTEGER DEFAULT 0,
    vehicle_visits INTEGER DEFAULT 0,
    aircraft_visits INTEGER DEFAULT 0,
    
    -- Classification hint
    is_likely_base INTEGER DEFAULT 0, -- 1 if frequently visited (potential park base)
    
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(settlement_id, year, month)
);
CREATE TABLE upload_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL DEFAULT 'anonymous',
    user_email TEXT NOT NULL DEFAULT 'anonymous',
    filename TEXT NOT NULL,
    file_hash TEXT,
    file_content BLOB NOT NULL,  -- Raw GPX file content
    status TEXT NOT NULL DEFAULT 'pending',  -- pending, processing, completed, failed
    error_message TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    started_at TIMESTAMP,
    completed_at TIMESTAMP,
    result_upload_id INTEGER,  -- ID in gpx_uploads table once processed
    result_json TEXT  -- Full response JSON for client
);
CREATE TABLE feature_geometries (
    id INTEGER PRIMARY KEY,
    feature_type TEXT NOT NULL,  -- 'fire_trajectory', 'deforestation', 'settlement', 'road'
    feature_id TEXT NOT NULL,    -- Reference to source (e.g., 'CAF_Chinko_2024_grp_1')
    park_id TEXT NOT NULL,
    geojson TEXT NOT NULL,       -- Full GeoJSON geometry
    bbox_minx REAL,
    bbox_miny REAL,
    bbox_maxx REAL,
    bbox_maxy REAL,
    start_date TEXT,             -- ISO date for time slider
    end_date TEXT,
    properties_json TEXT,        -- Additional properties for popup
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(feature_type, feature_id)
);
CREATE INDEX idx_effort_data_grid ON effort_data(grid_cell_id);
CREATE INDEX idx_effort_data_time ON effort_data(year, month, day);
CREATE INDEX idx_track_points_upload ON track_points(upload_id);
CREATE INDEX idx_track_points_grid ON track_points(grid_cell_id);
CREATE INDEX idx_gpx_uploads_user ON gpx_uploads(user_id);
CREATE INDEX idx_users_role ON users(role);
CREATE INDEX idx_sessions_user ON sessions(user_id);
CREATE INDEX idx_sessions_expires ON sessions(expires_at);
CREATE INDEX idx_publications_pa ON pa_publications(pa_id);
CREATE INDEX idx_publications_year ON pa_publications(year);
CREATE INDEX idx_park_checklist_pa ON park_checklist(pa_id);
CREATE INDEX idx_park_checklist_status ON park_checklist(status);
CREATE INDEX idx_park_documents_pa ON park_documents(pa_id);
CREATE INDEX idx_subcell_visits_grid ON subcell_visits(grid_cell_id);
CREATE INDEX idx_subcell_visits_date ON subcell_visits(visit_date);
CREATE INDEX idx_fire_date ON fire_detections(acq_date);
CREATE INDEX idx_fire_location ON fire_detections(latitude, longitude);
CREATE INDEX idx_fire_grid ON fire_detections(grid_cell_id);
CREATE INDEX idx_fire_pa ON fire_detections(protected_area_id);
CREATE INDEX idx_fire_infraction ON fire_detections(in_protected_area, acq_date);
CREATE INDEX idx_fire_daily_date ON fire_daily_grid(date);
CREATE INDEX idx_fire_daily_pa ON fire_daily_grid(protected_area_id, date);
CREATE INDEX idx_park_fire_analysis_park ON park_fire_analysis(park_id);
CREATE INDEX idx_osm_roadless_park_id ON osm_roadless_data(park_id);
CREATE INDEX idx_park_group_infractions_park ON park_group_infractions(park_id);
CREATE INDEX idx_park_group_infractions_year ON park_group_infractions(year);
CREATE INDEX idx_ghsl_park ON ghsl_data(park_id);
CREATE INDEX idx_roadless_park ON osm_roadless_data(park_id);
CREATE INDEX idx_osm_places_park ON osm_places(park_id);
CREATE INDEX idx_osm_places_type ON osm_places(place_type);
CREATE INDEX idx_osm_places_location ON osm_places(lat, lon);
CREATE INDEX idx_clusters_park_year ON deforestation_clusters(park_id, year);
CREATE INDEX idx_settlements_park ON park_settlements(park_id);
CREATE INDEX idx_settlements_location ON park_settlements(lat, lon);
CREATE INDEX idx_settlements_type ON park_settlements(settlement_type);
CREATE INDEX idx_park_documents_category ON park_documents(category);
CREATE INDEX idx_park_documents_year ON park_documents(year);
CREATE UNIQUE INDEX idx_park_documents_unique_pa_title ON park_documents(pa_id, title);
CREATE INDEX idx_gpx_logs_upload_time ON gpx_upload_logs(upload_time DESC);
CREATE INDEX idx_gpx_logs_park ON gpx_upload_logs(protected_area_id);
CREATE INDEX idx_gpx_logs_user ON gpx_upload_logs(user_id);
CREATE INDEX idx_gpx_logs_status ON gpx_upload_logs(processing_status);
CREATE INDEX idx_learned_roads_park ON learned_roads(park_id);
CREATE INDEX idx_learned_airstrips_park ON learned_airstrips(park_id);
CREATE INDEX idx_learned_places_park ON learned_places(park_id);
CREATE INDEX idx_vehicle_tracks_park ON vehicle_tracks(park_id);
CREATE INDEX idx_aircraft_patterns_park ON aircraft_patterns(park_id);
CREATE INDEX idx_gpx_learning_queue_status ON gpx_learning_queue(status);
CREATE INDEX idx_gpx_learning_results_park ON gpx_learning_results(park_id);
CREATE INDEX idx_roads_history_park ON learned_roads_history(park_id);
CREATE INDEX idx_roads_history_original ON learned_roads_history(original_id);
CREATE INDEX idx_airstrips_history_park ON learned_airstrips_history(park_id);
CREATE INDEX idx_airstrips_history_original ON learned_airstrips_history(original_id);
CREATE INDEX idx_places_history_park ON learned_places_history(park_id);
CREATE INDEX idx_places_history_original ON learned_places_history(original_id);
CREATE INDEX idx_fire_group_alerts_park ON fire_group_alerts(park_id);
CREATE INDEX idx_fire_group_alerts_type ON fire_group_alerts(alert_type);
CREATE INDEX idx_fire_group_alerts_active ON fire_group_alerts(left_at) WHERE left_at IS NULL;
CREATE INDEX idx_gpx_uploads_file_hash ON gpx_uploads(file_hash);
CREATE INDEX idx_settlement_visits_settlement ON settlement_visits(settlement_id);
CREATE INDEX idx_settlement_visits_park ON settlement_visits(park_id);
CREATE INDEX idx_settlement_visits_date ON settlement_visits(visit_date);
CREATE INDEX idx_settlement_visits_year_month ON settlement_visits(year, month);
CREATE INDEX idx_settlement_intensity_park ON settlement_intensity(park_id);
CREATE INDEX idx_settlement_intensity_year ON settlement_intensity(year, month);
CREATE INDEX idx_settlement_intensity_base ON settlement_intensity(is_likely_base) WHERE is_likely_base = 1;
CREATE INDEX idx_upload_queue_status ON upload_queue(status);
CREATE INDEX idx_upload_queue_hash ON upload_queue(file_hash);
CREATE TABLE park_climate (
    park_id TEXT PRIMARY KEY,
    temp_annual_c REAL,
    temp_max_c REAL,
    temp_min_c REAL,
    precip_annual_mm INTEGER,
    precip_wettest_mm INTEGER,
    precip_driest_mm INTEGER,
    climate_zone TEXT,
    rainy_season TEXT,
    dry_season TEXT
);
CREATE TABLE park_waterbodies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    park_id TEXT NOT NULL,
    waterbody_id TEXT,
    name TEXT,
    waterbody_type TEXT,
    lat REAL,
    lon REAL
);
CREATE INDEX idx_pwb_park ON park_waterbodies(park_id);
CREATE TABLE fire_narrative_cache (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    park_id TEXT UNIQUE NOT NULL,
    narrative_json TEXT NOT NULL,
    computed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    from_year INTEGER,
    to_year INTEGER
);
CREATE INDEX idx_fnc_park ON fire_narrative_cache(park_id);
CREATE INDEX idx_fire_lat_lon ON fire_detections(latitude, longitude);
CREATE TABLE rivers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            hyriv_id INTEGER UNIQUE,
            name TEXT,
            length_km REAL,
            discharge_cms REAL,
            stream_order INTEGER,
            geojson TEXT,
            centroid_lat REAL,
            centroid_lon REAL
        );
CREATE INDEX idx_rivers_hyriv ON rivers(hyriv_id);
CREATE INDEX idx_rivers_order ON rivers(stream_order);
CREATE TABLE park_rivers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            park_id TEXT NOT NULL,
            hyriv_id INTEGER NOT NULL,
            distance_km REAL,
            relation TEXT,
            UNIQUE(park_id, hyriv_id)
        );
CREATE INDEX idx_park_rivers_park ON park_rivers(park_id);
CREATE TABLE park_species (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            park_id TEXT NOT NULL,
            binomial TEXT NOT NULL,
            common_name TEXT,
            status TEXT,
            species_order TEXT,
            family TEXT,
            UNIQUE(park_id, binomial)
        );
CREATE INDEX idx_ps_park ON park_species(park_id);
CREATE INDEX idx_ps_status ON park_species(status);
CREATE INDEX idx_fg_defo_year ON feature_geometries(park_id, feature_type, json_extract(properties_json, '$.year'));
CREATE INDEX idx_fg_park_type ON feature_geometries(park_id, feature_type);
CREATE INDEX idx_fg_dates ON feature_geometries(start_date, end_date);
CREATE TABLE notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    park_id TEXT,
    notification_type TEXT NOT NULL,
    title TEXT NOT NULL,
    message TEXT,
    reference_id TEXT,
    reference_url TEXT,
    metadata TEXT,
    is_read INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_notifications_park ON notifications(park_id);
CREATE INDEX idx_notifications_type ON notifications(notification_type);
CREATE INDEX idx_notifications_unread ON notifications(is_read, created_at);
CREATE TABLE IF NOT EXISTS "deforestation_events" (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                park_id TEXT NOT NULL,
                year INTEGER NOT NULL,
                area_km2 REAL NOT NULL,
                event_type TEXT,
                lat REAL NOT NULL,
                lon REAL NOT NULL,
                geojson TEXT,
                description TEXT,
                pattern_type TEXT,
                pixel_count INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                classification TEXT,
                classification_confidence REAL,
                narrative TEXT,
                fires_same_year INTEGER DEFAULT 0,
                fire_ratio REAL DEFAULT 0,
                nearest_settlement_km REAL,
                classified_at TIMESTAMP,
                polygon_ids TEXT
            );
CREATE INDEX idx_deforest_park ON deforestation_events(park_id);
CREATE INDEX idx_deforest_year ON deforestation_events(year);
CREATE INDEX idx_deforest_coords ON deforestation_events(lat, lon);
CREATE TABLE park_rivers_hydro (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            park_id TEXT NOT NULL,
            hyriv_id INTEGER NOT NULL,
            name TEXT,
            stream_order INTEGER,
            ord_flow INTEGER,
            length_km REAL,
            geojson TEXT,
            UNIQUE(park_id, hyriv_id)
        );
CREATE TABLE park_lakes_hydro (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            park_id TEXT NOT NULL,
            hylak_id INTEGER NOT NULL,
            name TEXT,
            lake_type INTEGER,
            elevation INTEGER,
            area_km2 REAL,
            centroid_lon REAL,
            centroid_lat REAL,
            geojson TEXT,
            UNIQUE(park_id, hylak_id)
        );
CREATE TABLE roads_heigit (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            park_id TEXT NOT NULL,
            osm_id TEXT,
            name TEXT,
            highway_type TEXT,
            surface TEXT,
            smoothness TEXT,
            width REAL,
            lanes INTEGER,
            passability TEXT,
            length_km REAL,
            geojson TEXT,
            osm_surface_class TEXT,
            osm_length REAL,
            dl_class_2024 TEXT,
            dl_class_2020 TEXT,
            surface_change TEXT,
            passability_code TEXT,
            passability_desc TEXT,
            passability_risk TEXT,
            rw_class TEXT,
            UNIQUE(park_id, osm_id)
        );
CREATE INDEX idx_rivers_hydro_park ON park_rivers_hydro(park_id);
CREATE INDEX idx_rivers_hydro_order ON park_rivers_hydro(stream_order);
CREATE INDEX idx_lakes_hydro_park ON park_lakes_hydro(park_id);
CREATE INDEX idx_roads_heigit_park ON roads_heigit(park_id);
CREATE TABLE legal_documents (
    id TEXT PRIMARY KEY,
    country_iso TEXT NOT NULL,
    title TEXT,
    title_en TEXT,
    year INTEGER,
    doc_type TEXT,
    subjects TEXT,
    abstract TEXT,
    url TEXT,
    keywords TEXT,
    scraped_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
