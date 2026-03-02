# Disk Space Cleanup - Mar 2, 2026

## Actions Taken

### 1. Removed Backup Files (1.2GB freed)
- Deleted `/home/exedev/5mp/backups/` directory containing:
  - `db_backup_20260302_181545.sqlite3` (230MB)
  - `db_backup_20260302_182400.sqlite3` (927MB)
  - Associated journal files

### 2. Removed Backup Scripts
- Deleted `scripts/backup_before_cron.sh` (not used by any cron jobs)

### 3. Removed Template Backups
- Deleted `srv/templates/globe.html.bak` (583KB)

## Current Status
- Disk usage: 77% (was ~83%)
- 4.0GB available (was ~2.8GB)

## Backup Policy
**No automatic backups are configured.** The cron job at 3am UTC runs `daily_fire_update.py` which does NOT create backups.

To manually backup the database if needed:
```bash
sqlite3 db.sqlite3 ".backup db_backup_$(date +%Y%m%d).sqlite3"
```

## Recommendations
1. Monitor disk space with `df -h /home`
2. Consider cleaning old log files periodically
3. Prune old fire data if database grows too large
