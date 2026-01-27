-- Add infraction tracking to fire analysis

ALTER TABLE park_fire_analysis ADD COLUMN total_infractions INTEGER DEFAULT 0;
ALTER TABLE park_fire_analysis ADD COLUMN infraction_rate REAL DEFAULT 0;
ALTER TABLE park_fire_analysis ADD COLUMN peak_infraction_day TEXT;
ALTER TABLE park_fire_analysis ADD COLUMN peak_infraction_count INTEGER DEFAULT 0;
ALTER TABLE park_fire_analysis ADD COLUMN net_south_km REAL DEFAULT 0;
ALTER TABLE park_fire_analysis ADD COLUMN avg_daily_movement_km REAL DEFAULT 0;
ALTER TABLE park_fire_analysis ADD COLUMN monthly_stats_json TEXT;
