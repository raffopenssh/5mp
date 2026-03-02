# Database Backup - March 2, 2026

## Backup File
- **Location:** `/home/exedev/5mp/db_backup_20260302.sqlite3`
- **Size:** 1.9GB
- **Created:** March 2, 2026 21:00 UTC
- **Method:** SQLite `.backup` command (atomic copy)

## Verification Results
✓ Integrity check: **PASSED**
✓ Record counts match original:
  - Fire detections: 154,633
  - Feature geometries: 384,572
  - Fire narrative cache: 162

## Restore Instructions
To restore from this backup:
```bash
# Stop the server first
pkill server

# Restore database
cd /home/exedev/5mp
cp db.sqlite3 db.sqlite3.old  # Keep current as fallback
cp db_backup_20260302.sqlite3 db.sqlite3

# Restart server
./server &
```

## Disk Space Impact
- Before backup: 77% used, 4.0GB available
- After backup: 88% used, 2.2GB available
- **Backup consumed:** 1.9GB

## Important Notes
1. This is a **one-time manual backup** - no automatic backups are configured
2. The cron job does NOT create backups
3. Consider moving this backup off-server for safety
4. Monitor disk space: `df -h /home`

## Download Backup (if needed)
```bash
# From local machine
scp exedev@five-megapixel-conservation.exe.xyz:/home/exedev/5mp/db_backup_20260302.sqlite3 .
```
