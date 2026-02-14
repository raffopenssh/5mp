-- Run these queries on production database to diagnose data issues

-- Check park_settlements table structure
.schema park_settlements

-- Check if classification column exists and has data
SELECT 
    'park_settlements' as table_name,
    COUNT(*) as total_rows,
    COUNT(classification) as with_classification,
    COUNT(narrative) as with_narrative
FROM park_settlements;

-- Sample data for Chinko
SELECT park_id, classification, narrative 
FROM park_settlements 
WHERE park_id = 'CAF_Chinko' 
LIMIT 3;

-- Check deforestation_events table
.schema deforestation_events

SELECT 
    'deforestation_events' as table_name,
    COUNT(*) as total_rows,
    COUNT(classification) as with_classification,
    COUNT(narrative) as with_narrative
FROM deforestation_events;

-- Sample deforestation for Chinko
SELECT park_id, year, classification, narrative 
FROM deforestation_events 
WHERE park_id = 'CAF_Chinko' 
LIMIT 3;

-- Check fire_narrative_cache
SELECT park_id, computed_at,
       json_extract(narrative_json, '$.trend.seasonality') as seasonality,
       length(json_extract(narrative_json, '$.trend.months')) as months_length
FROM fire_narrative_cache
WHERE park_id IN ('CAF_Chinko', 'TCD_Zakouma')
LIMIT 5;
