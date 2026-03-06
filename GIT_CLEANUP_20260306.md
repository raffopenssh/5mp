# Git Repository Cleanup - March 6, 2026

## Summary

Surgically removed large binary and regenerable files from git history using `git-filter-repo`.

## Removed from History

**Binary artifacts** (should never be in git):
- `server` - Go binary (~18-23MB, multiple versions)
- `sync-tool` - Go binary (~18MB)
- `test-faolex` - Go binary (~16MB)

**Python dependencies** (belongs in .gitignore):
- `.venv/` - NumPy/SciPy libraries (~24MB each)

**Regenerable data** (already in .gitignore):
- `data/export/fire_narratives.json` - 3 versions (68MB + 52MB + 51MB = ~171MB)
- `data/fire_trajectories/` - Old v2/v3 trajectories (~20-27MB per park)
- `data/export/fire_narratives/` - Per-park narrative JSON files
- `data/fire/viirs-jpss1_2023_Central_African_Republic.csv` - Raw CSV (42MB)

## Results

| Metric | Before | After | Savings |
|--------|--------|-------|---------|
| Working tree | 7.8GB | 7.0GB | 800MB |
| `.git/` size | 1.9GB | 1.9GB | Minimal* |
| Disk free | 3.5GB | 3.5GB | - |

\* Git history still contains 162 parks × ~20-35MB JSON files (fire_groups_v2, feature_geometries)
which are legitimate project data, updated twice (initial + v5 rebuild).

## Backup

Full git backup before cleanup:
```
/home/exedev/5mp-backup-20260306-071332.tar.gz (732MB)
```

## Commands Run

```bash
# Install tool
sudo apt-get install -y git-filter-repo

# Backup
tar -czf /home/exedev/5mp-backup-$(date +%Y%m%d-%H%M%S).tar.gz 5mp/.git

# Remove files from history
git-filter-repo \
  --invert-paths \
  --path server \
  --path sync-tool \
  --path test-faolex \
  --path .venv/ \
  --path 'data/fire/viirs-jpss1_2023_Central_African_Republic.csv' \
  --path 'data/export/fire_narratives.json' \
  --path 'data/fire_trajectories/' \
  --path 'data/export/fire_narratives/' \
  --force

# Cleanup
git reflog expire --expire=now --all
git gc --prune=all --aggressive
```

## Remaining Large Files

Current largest files in git history (>20MB):
- `data/fire_groups_v2/*.json` - 162 parks, 2 versions each (v2 + v5)
- `data/feature_geometries/deforestation/*.json` - Deforestation polygons

These are legitimate project data that should remain in git.

## Recommendations

To prevent future bloat:
1. ✅ Keep binaries out of git (already in .gitignore)
2. ✅ Keep .venv out of git (already in .gitignore)
3. ✅ Keep regenerable data out of git (already in .gitignore)
4. Consider: Move large data files to Git LFS if repository grows >5GB
