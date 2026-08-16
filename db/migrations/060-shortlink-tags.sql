-- A tag groups links that were issued for one purpose (e.g. every link a
-- report cites gets tag 'report'), so their lives can be extended together
-- instead of slug by slug. Free text, but sanitised on write to [a-z0-9_-].
ALTER TABLE short_links ADD COLUMN tag TEXT NOT NULL DEFAULT '';
