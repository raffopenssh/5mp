-- 061: short_link_tags — a link may carry MORE THAN ONE purpose tag.
--
-- 060 put one `tag` column on short_links, which forced a link that a report
-- cites AND a workshop hands out to pick a side: tagging it `workshop` took it
-- out of the next "renew #report", silently, which is the failure mode tags
-- exist to prevent. Tags are a set, so they live in their own table.
--
-- The old column is DROPPED rather than kept in sync. A duplicated fact with
-- two writers drifts (AGENTS.md invariant 5: "a stored answer nothing
-- re-checks drifts from the question"), and a reader that happened to select
-- short_links.tag would keep seeing the first tag as if it were the only one.
CREATE TABLE IF NOT EXISTS short_link_tags (
    slug       TEXT NOT NULL,            -- → short_links.slug
    tag        TEXT NOT NULL,            -- sanitised [a-z0-9_-]{1,32}
    created_at TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (slug, tag)
);

-- By-tag lookups ("every live key tagged report") are the point of the table.
CREATE INDEX IF NOT EXISTS idx_short_link_tags_tag ON short_link_tags(tag);

INSERT OR IGNORE INTO short_link_tags (slug, tag, created_at)
    SELECT slug, tag, COALESCE(created_at, '') FROM short_links
    WHERE tag IS NOT NULL AND tag != '';

ALTER TABLE short_links DROP COLUMN tag;
