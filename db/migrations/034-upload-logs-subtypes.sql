-- Add movement subtype columns to gpx_upload_logs
-- These break down vehicle into ground-vehicle + boat,
-- and aircraft into fixed-wing + rotor-wing (helicopter).
ALTER TABLE gpx_upload_logs ADD COLUMN boat_segments INTEGER DEFAULT 0;
ALTER TABLE gpx_upload_logs ADD COLUMN boat_km REAL DEFAULT 0;
ALTER TABLE gpx_upload_logs ADD COLUMN boat_minutes REAL DEFAULT 0;
ALTER TABLE gpx_upload_logs ADD COLUMN fixed_wing_segments INTEGER DEFAULT 0;
ALTER TABLE gpx_upload_logs ADD COLUMN fixed_wing_km REAL DEFAULT 0;
ALTER TABLE gpx_upload_logs ADD COLUMN fixed_wing_minutes REAL DEFAULT 0;
ALTER TABLE gpx_upload_logs ADD COLUMN rotor_wing_segments INTEGER DEFAULT 0;
ALTER TABLE gpx_upload_logs ADD COLUMN rotor_wing_km REAL DEFAULT 0;
ALTER TABLE gpx_upload_logs ADD COLUMN rotor_wing_minutes REAL DEFAULT 0;
