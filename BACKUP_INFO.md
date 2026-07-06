# Database Backups

## Latest Backup — July 6, 2026 (Zenodo Draft, gzip-compressed)

| Field | Value |
|-------|-------|
| **Location** | Zenodo (draft, not published) |
| **Deposition ID** | `19363779` |
| **State** | Draft (unsubmitted) — no public DOI |
| **Draft URL** | https://zenodo.org/deposit/19363779 |
| **Bucket URL** | `https://zenodo.org/api/files/4bd66ea4-80b9-45f9-af7b-4237c268844a` |
| **File** | `5mp_db_backup_20260706.sqlite3.gz` (gzip -6) |
| **Size** | 2,608,679,381 bytes (~2.6 GB compressed, from ~8.8 GB db.sqlite3) |
| **MD5** | `05d8a420e6486a4bb0932f2bc87e6f88` |
| **Created** | July 6, 2026 17:03 UTC |
| **Method** | SQLite `.backup` (atomic copy) + gzip compression |
| **Manifest** | `data/db_backup_zenodo_manifest.json` |

### Verification

- ✅ PRAGMA integrity_check: **ok**
- ✅ MD5: `05d8a420e6486a4bb0932f2bc87e6f88`
- ✅ Zenodo HEAD check: HTTP 200
- ✅ State: unsubmitted (draft, not public)

### Download from Zenodo

```bash
# Requires ZENODO_TOKEN (draft deposits are not publicly accessible)
curl -H "Authorization: Bearer $ZENODO_TOKEN" \
  "https://zenodo.org/api/files/4bd66ea4-80b9-45f9-af7b-4237c268844a/5mp_db_backup_20260706.sqlite3.gz" \
  -o 5mp_db_backup_20260706.sqlite3.gz
gunzip 5mp_db_backup_20260706.sqlite3.gz
```

> Note: `cmd/backup-zenodo/` now gzip-compresses the backup before upload
> (`.sqlite3.gz`). Zenodo's PUT endpoint intermittently returned 502s on
> this large upload; the client's built-in retry/backoff eventually
> succeeded — this is expected occasional flakiness for multi-GB uploads,
> not a client bug.

---

## Previous Backup — April 1, 2026 (Zenodo Draft)

| Field | Value |
|-------|-------|
| **Location** | Zenodo (draft, not published) |
| **Deposition ID** | `19363779` |
| **State** | Draft (unsubmitted) — no public DOI |
| **Draft URL** | https://zenodo.org/deposit/19363779 |
| **Bucket URL** | `https://zenodo.org/api/files/4bd66ea4-80b9-45f9-af7b-4237c268844a` |
| **File** | `5mp_db_backup_20260401.sqlite3` |
| **Size** | 1,262,694,400 bytes (~1.2 GB) |
| **MD5** | `d17ef446b03f58b5fdd1cb527dcd3088` |
| **Created** | April 1, 2026 04:42 UTC |
| **Method** | SQLite `.backup` command (atomic copy) |
| **Manifest** | `data/db_backup_zenodo_manifest.json` |

### Verification

- ✅ PRAGMA integrity_check: **ok**
- ✅ MD5: `d17ef446b03f58b5fdd1cb527dcd3088`
- ✅ Zenodo HEAD check: HTTP 200
- ✅ State: unsubmitted (draft, not public)

### Download from Zenodo

```bash
# Requires ZENODO_TOKEN (draft deposits are not publicly accessible)
curl -H "Authorization: Bearer $ZENODO_TOKEN" \
  "https://zenodo.org/api/files/4bd66ea4-80b9-45f9-af7b-4237c268844a/5mp_db_backup_20260401.sqlite3" \
  -o 5mp_db_backup_20260401.sqlite3
```

### Using the backup tool

```bash
# Create a new backup and upload as Zenodo draft
ZENODO_TOKEN=... go run ./cmd/backup-zenodo/

# This will:
# 1. Create SQLite backup via .backup command
# 2. Verify integrity
# 3. Upload to Zenodo as draft (NOT published)
# 4. Verify upload via HEAD request
# 5. Remove local backup file
# 6. Update manifest at data/db_backup_zenodo_manifest.json
```

### Restore Instructions

```bash
# Download backup
curl -H "Authorization: Bearer $ZENODO_TOKEN" \
  "https://zenodo.org/api/files/4bd66ea4-80b9-45f9-af7b-4237c268844a/5mp_db_backup_20260401.sqlite3" \
  -o 5mp_db_backup_20260401.sqlite3

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

---

## Deprecated: Published Zenodo Deposit 19363593

> ⚠️ This deposit was published accidentally and should not be used.
> It was superseded by the draft deposit 19363779 above.

| Field | Value |
|-------|-------|
| **Deposit ID** | `19363593` |
| **DOI** | `10.5281/zenodo.19363593` |
| **Status** | Published (cannot be deleted) |
