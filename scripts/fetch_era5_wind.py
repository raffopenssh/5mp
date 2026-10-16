#!/usr/bin/env python3
"""Compatibility shim: `fetch_era5_wind.py ARGS` == `fetch_era5.py --product wind ARGS`. See fetch_era5.py."""
import os, subprocess, sys
sys.exit(subprocess.call([sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)), "fetch_era5.py"), "--product", "wind"] + sys.argv[1:]))
