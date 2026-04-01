# Database Backups

## Latest Backup — April 1, 2026 (Zenodo)

| Field | Value |
|-------|-------|
| **Location** | Zenodo (restricted access) |
| **Deposit ID** | `19363593` |
| **DOI** | `10.5281/zenodo.19363593` |
| **Record URL** | https://zenodo.org/records/19363593 |
| **Download URL** | https://zenodo.org/api/records/19363593/draft/files/5mp_db_backup_20260401.sqlite3/content |
| **File** | `5mp_db_backup_20260401.sqlite3` |
| **Size** | 1,262,694,400 bytes (~1.2 GB) |
| **MD5** | `d17ef446b03f58b5fdd1cb527dcd3088` |
| **Created** | April 1, 2026 04:02 UTC |
| **Method** | SQLite `.backup` command (atomic copy) |
| **Access** | Restricted |

### Verification

- ✅ PRAGMA integrity_check: **ok**
- ✅ MD5: `d17ef446b03f58b5fdd1cb527dcd3088`
- ✅ Record counts verified:
  - fire_detections: 565,789
  - feature_geometries: 436,841
  - fire_narrative_cache: 162
  - park_settlements: 9,933
  - park_species: 39,489

### Download from Zenodo

```bash
# Requires Zenodo API token with access to this restricted deposit
curl -H "Authorization: Bearer $ZENODO_TOKEN" \
  "https://zenodo.org/api/records/19363593/draft/files/5mp_db_backup_20260401.sqlite3/content" \
  -o 5mp_db_backup_20260401.sqlite3
```

### Restore Instructions

```bash
# Stop the server
sudo systemctl stop 5mp

# Back up current DB
cp db.sqlite3 db.sqlite3.old

# Restore from backup
cp 5mp_db_backup_20260401.sqlite3 db.sqlite3

# Restart server
sudo systemctl start 5mp
```

---

## Previous Backup — March 2, 2026 (exe-dev-monitor-peer01)

| Field | Value |
|-------|-------|
| **Location** | exe-dev-monitor-peer01.exe.xyz |
| **File ID** | `c8de734b-ad0e-4c25-b5bb-6e4ddef3f847` |
| **Token** | `REDACTED_TOKEN` |
| **File** | `5mp_db_backup_20260302.sqlite3` |
| **Size** | 1.87 GB (2,008,952,832 bytes) |
| **MD5** | `c4f7fff51e59277566d3d03e9eaf31a1` |
| **Created** | March 2, 2026 |

### Download from peer01

```bash
curl -H "Authorization: Bearer REDACTED_TOKEN" \
  https://exe-dev-monitor-peer01.exe.xyz:8000/api/download/c8de734b-ad0e-4c25-b5bb-6e4ddef3f847 \
  -o 5mp_db_backup_20260302.sqlite3
```

### Verify peer01 backup

```bash
curl -X POST -H "Authorization: Bearer REDACTED_TOKEN" \
  https://exe-dev-monitor-peer01.exe.xyz:8000/api/verify/c8de734b-ad0e-4c25-b5bb-6e4ddef3f847
```
