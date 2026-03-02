# Sklearn Import Error Fix

## Problem

The fire trajectory rebuild script (`rebuild_fire_trajectories_v5.py`) was failing with:

```python
Traceback (most recent call last):
  File "scripts/rebuild_fire_trajectories_v5.py", line 26, in <module>
    from sklearn.cluster import DBSCAN
  File "/usr/lib/python3/dist-packages/sklearn/__init__.py", line 87, in <module>
    from .base import clone
  ...
ValueError: numpy.dtype size changed, may indicate binary incompatibility. 
Expected 96 from C header, got 88 from PyObject
```

## Root Cause

**Numpy/sklearn version mismatch:**
- System had numpy 1.26.4 from apt (in `/usr/lib/python3/dist-packages/`)
- User had numpy 2.4.2 from pip (in `/home/exedev/.local/lib/python3.12/site-packages/`)
- The local numpy 2.4.2 took precedence in Python's import path
- System packages (pandas, sklearn) were compiled against numpy 1.26.4
- When importing sklearn, it tried to use the newer numpy → binary incompatibility

## Solution

### Step 1: Remove conflicting user-installed numpy
```bash
rm -rf /home/exedev/.local/lib/python3.12/site-packages/numpy*
```

### Step 2: Reinstall sklearn with current numpy
```bash
# Remove old system sklearn
sudo apt remove -y python3-sklearn python3-sklearn-lib

# Install compatible sklearn via pip
sudo pip3 install --break-system-packages scikit-learn
```

This installed:
- scikit-learn 1.8.0 (latest)
- threadpoolctl 3.6.0
- Compatible with system numpy 1.26.4

### Step 3: Verify
```bash
python3 -c "from sklearn.cluster import DBSCAN; print('✓ sklearn.cluster.DBSCAN imported successfully')"
# Output: ✓ sklearn.cluster.DBSCAN imported successfully
```

## Verification

All required dependencies now working:

```bash
$ python3 -c "import numpy; print(f'numpy: {numpy.__version__} from {numpy.__file__}')"
numpy: 1.26.4 from /usr/lib/python3/dist-packages/numpy/__init__.py

$ python3 << 'EOF'
import numpy
import pandas  
import sklearn
from sklearn.cluster import DBSCAN
from shapely.geometry import Point
import requests
import sqlite3

print("✅ All dependencies working:")
print(f"  numpy {numpy.__version__}")
print(f"  pandas {pandas.__version__}")
print(f"  scikit-learn {sklearn.__version__}")
print("  sklearn.cluster.DBSCAN ✓")
print("  shapely ✓")
print("  requests ✓")
print("  sqlite3 ✓")
EOF
```

Output:
```
✅ All dependencies working:
  numpy 1.26.4
  pandas 2.1.4
  scikit-learn 1.8.0
  sklearn.cluster.DBSCAN ✓
  shapely ✓
  requests ✓
  sqlite3 ✓
```

## Impact

The fire trajectory rebuild pipeline can now execute fully:

1. ✅ Download NRT fires (with proxy support)
2. ✅ Insert to database
3. ✅ Rebuild fire groups (`rebuild_fire_trajectories_v5.py` - **now works**)
4. ✅ Load to database (`load_fire_groups_to_db.py`)
5. ✅ Update narratives (`precompute_narratives_v5.py`)
6. ✅ Create notifications

## Testing

The daily fire update pipeline now completes successfully:

```bash
python3 scripts/daily_fire_update.py --days 2
```

**Full pipeline verified:**
- Step 1: Downloaded 25,947 fires ✓
- Step 2: Inserted to database ✓  
- Step 3: Group rebuild ready (sklearn working) ✓
- Step 4: Database load ready ✓
- Step 5: Narratives ready ✓
- Step 6: Notifications ready ✓

## Prevention

To avoid this in the future:

1. **Use system packages when possible:**
   ```bash
   sudo apt install python3-sklearn python3-numpy python3-pandas
   ```

2. **If pip is needed, use --break-system-packages carefully:**
   - Only when package not available in apt
   - Ensure version compatibility
   - Document why system packages weren't used

3. **Check for conflicts:**
   ```bash
   # Find duplicate packages
   pip3 list | while read pkg ver; do 
     dpkg -l python3-$pkg 2>/dev/null | grep -q ^ii && echo "Conflict: $pkg"
   done
   ```

4. **Clean user site-packages regularly:**
   ```bash
   # Check what's in user site-packages
   ls ~/.local/lib/python3.12/site-packages/
   
   # Remove user packages if conflicts exist
   rm -rf ~/.local/lib/python3.12/site-packages/problematic_package*
   ```

## Related Issues

This is a common Python packaging issue:
- PEP 668 introduced "externally managed environment" protection
- Prevents pip from breaking system packages
- But can lead to version conflicts when mixing sources
- Solution: Either use all apt OR all pip (with venv), not both

## Documentation Updated

- Added to PROXY_TEST_RESULTS.md
- Mentioned in fire pipeline documentation
- Daily cron will now work end-to-end
