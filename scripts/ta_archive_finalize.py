#!/usr/bin/env python3
"""Assemble + zip the TA archive once the long jobs are done (feedback §G, S5).

Preconditions (the script refuses, never no-ops, when one is missing):
  * reports/TA_ARCHIVE_XSA_2026-10/_gfw_cells_stats.json  (scripts/ta_archive_rasters.py finished)
  * both app-export jobs 'ready' (POST .../export.gpkg raw=1 and raw=0)

    set -a; source secrets.env; set +a
    python3 scripts/ta_archive_finalize.py --full-job c39b72ef7adeb9eb --light-job bf235b58b0e35d48

Moves the rasters into rasters/, copies the two exports under self-describing
names, fills {{GFW_STATS}} in SOURCES.md, writes MANIFEST.txt (size + sha256),
zips to reports/TA_ARCHIVE_XSA_2026-10.zip and prints every file's size.
"""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
AOI = "XSA_Study_Area"


def job(jid, pwd):
    u = f"http://localhost:8000/api/geopackage/{jid}?pwd={pwd}"
    return json.load(urllib.request.urlopen(u))


def sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="reports/TA_ARCHIVE_XSA_2026-10")
    ap.add_argument("--full-job", required=True)
    ap.add_argument("--light-job", required=True)
    ap.add_argument("--no-zip", action="store_true")
    a = ap.parse_args()
    out = BASE / a.out
    pwd = os.environ.get("AOI_OWNER_PWD") or sys.exit("AOI_OWNER_PWD not set (source secrets.env)")

    stats_p = out / "_gfw_cells_stats.json"
    if not stats_p.exists():
        sys.exit(f"{stats_p} missing: ta_archive_rasters.py has not finished (tail /tmp/ta_rasters.log)")
    stats = json.load(open(stats_p))

    # --- app exports -------------------------------------------------------
    exp = out / "app_export"
    exp.mkdir(exist_ok=True)
    for jid, name in ((a.full_job, f"{AOI}_app_export_2026-10.gpkg"),
                      (a.light_job, f"{AOI}_app_export_2026-10_no_raw_fire.gpkg")):
        j = job(jid, pwd)
        if j.get("state") != "ready":
            sys.exit(f"job {jid} is {j.get('state')} ({j.get('step')}, {j.get('progress')}) -- not ready")
        src = BASE / "data" / "gpkg_output" / jid
        files = list(src.glob("*.gpkg"))
        if len(files) != 1 or files[0].stat().st_size != j["size_bytes"]:
            sys.exit(f"job {jid}: expected one gpkg of {j['size_bytes']} B in {src}, found {files}")
        dst = exp / name
        if not dst.exists() or dst.stat().st_size != files[0].stat().st_size:
            shutil.copyfile(files[0], dst)
        # the embedded QGIS project references ./<basename>.gpkg -- the basename
        # changed, so the project inside must be re-pointed (exports.md).
        rc = subprocess.run(["python3", "-c", f"""
import sqlite3, zlib, zipfile, io, sys
p = {str(dst)!r}; old = {files[0].name!r}; new = {name!r}
c = sqlite3.connect(p)
rows = c.execute("select name, xml from qgis_projects").fetchall()
for n, xml in rows:
    raw = bytes.fromhex(xml.decode() if isinstance(xml, bytes) else xml)
    z = zipfile.ZipFile(io.BytesIO(raw)); buf = io.BytesIO(); zo = zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED)
    hit = 0
    for zi in z.infolist():
        b = z.read(zi)
        if zi.filename.endswith('.qgs'):
            s = b.decode(); hit += s.count(old); b = s.replace(old, new).encode()
        zo.writestr(zi, b)
    zo.close()
    if hit == 0: sys.exit('embedded project %s does not reference %s' % (n, old))
    c.execute("update qgis_projects set xml=? where name=?", (buf.getvalue().hex(), n))
    print('  re-pointed embedded project', n, hit, 'refs')
c.commit()
"""])
        if rc.returncode:
            sys.exit("embedded project rewrite failed")

    # --- rasters -----------------------------------------------------------
    ras = out / "rasters"
    ras.mkdir(exist_ok=True)
    for p in list(out.glob(f"{AOI}_*.tif")) + list(out.glob(f"{AOI}_*.gpkg")):
        shutil.move(str(p), ras / p.name)
    for p in list(out.glob("*.aux.xml")) + list(ras.glob("*.aux.xml")):
        p.unlink()
    shutil.copyfile(BASE / "data" / "study_areas" / f"{AOI}.geojson", ras / f"{AOI}_aoi.geojson")
    expect = [f"{AOI}_glad_cropland_2003_clip.tif", f"{AOI}_glad_cropland_2019_clip.tif",
              f"{AOI}_ghsl_built_s_E2000_clip.tif", f"{AOI}_ghsl_built_s_E2015_clip.tif",
              f"{AOI}_ghsl_built_s_E2030_clip.tif", f"{AOI}_hansen_lossyear_2001-2024_clip.tif",
              f"{AOI}_gfw_alert_cells_2024-26_0p5deg.tif", f"{AOI}_gfw_alert_cells_2024-26_0p5deg.gpkg"]
    missing = [e for e in expect if not (ras / e).exists()]
    if missing:
        sys.exit(f"rasters missing: {missing}")

    # --- SOURCES.md numbers ------------------------------------------------
    sp = out / "SOURCES.md"
    s = sp.read_text()
    line = (f"Measured on this file: {stats['cells']} cells touch the AOI, {stats['unmeasured']} are unmeasured "
            f"(= {stats['unmeasured_area_pct']} % of the AOI area); {stats['unmeasured_with_hansen_loss']} of the "
            f"unmeasured cells hold > 0.5 km² of Hansen loss 2015–23. (The 71.9 % in feedback §G1 was counted on the 400-day tile cache, 2025-08→2026-10, over whole tiles; this file counts 2024-01→2026-08 alerts and weights cells by their share inside the AOI.)")
    if "{{GFW_STATS}}" in s:
        s = s.replace("{{GFW_STATS}}", line)
    elif "Measured on this file:" not in s:
        sys.exit("SOURCES.md has neither the placeholder nor the filled line")
    sp.write_text(s)
    stats_p.unlink()

    # --- manifest ------------------------------------------------------------
    rows, total = [], 0
    for p in sorted(out.rglob("*")):
        if p.is_file() and p.name != "MANIFEST.txt":
            sz = p.stat().st_size; total += sz
            rows.append((p.relative_to(out).as_posix(), sz, sha(p)))
    man = out / "MANIFEST.txt"
    with open(man, "w") as f:
        f.write(f"# {out.name} — {len(rows)} files, {total/1e9:.2f} GB\n# path\tbytes\tsha256\n")
        for r in rows:
            f.write(f"{r[0]}\t{r[1]}\t{r[2]}\n")
    for r in rows:
        print(f"{r[1]/1e6:10.1f} MB  {r[0]}")
    print(f"{total/1e9:.2f} GB total, {len(rows)} files -> {man}")

    if a.no_zip:
        return
    zp = out.with_suffix(".zip")
    if zp.exists():
        zp.unlink()
    subprocess.run(["zip", "-q", "-r", "-9", str(zp), out.name], cwd=out.parent, check=True)
    print(f"zip: {zp} {zp.stat().st_size/1e9:.2f} GB")


if __name__ == "__main__":
    main()
