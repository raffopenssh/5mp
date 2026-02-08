# Quick Context for Shelley

## What This Is

5MP Conservation Monitoring - Go web app with 3D globe showing 162 African protected areas.
Features: Fire detection, deforestation tracking, settlement analysis, legal frameworks.

---

## ⚠️ DATABASE PROTECTION - READ FIRST

### DO NOT:
- Run `DELETE` or `DROP` without explicit confirmation
- Run `UPDATE` on large tables without `WHERE` clause
- Truncate any tables
- Overwrite db.sqlite3

### ALWAYS:
- Use `LIMIT` when exploring data
- Back up before schema changes: `cp db.sqlite3 db.sqlite3.bak`
- Test queries with `SELECT` first
