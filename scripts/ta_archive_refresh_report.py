#!/usr/bin/env python3
"""Refresh report/ inside the TA archive after a PIP rebuild, then MANIFEST + zip (feedback §G, S5).

    python3 scripts/ta_archive_refresh_report.py [--no-zip]

Copies the current deliverables (final PDF + pdftotext, TeX project, piptex builders, plan yaml, maps,
budget, summary, ToR) under the archive's self-describing names, rewrites MANIFEST.txt and re-zips.
Refuses when a source is missing (never a silent no-op).
"""
import argparse
import shutil
import subprocess
import sys
from pathlib import Path

from ta_archive_finalize import sha  # noqa: E402

BASE = Path(__file__).resolve().parent.parent
R = BASE / "reports"
PL = R / "pip_latex"

COPIES = {  # destination (relative to report/) : source
    "PIP_Radom_Numatina_Southern_v3_2026-10-16_final.pdf": PL / "main_final.pdf",   # = the document title (user, 16 Oct)
    "PIP_SUMMARY_EASY_2026-10.txt": R / "PIP_SUMMARY_EASY.txt",
    "ToR_Project_Manager_South_Sudan_2026-10.txt": R / "ToR_Project_Manager_South_Sudan.txt",
    "BUDGET_EASY_2026-10.txt": R / "BUDGET_EASY_2026-10.txt",
    "BUDGET_EASY_2026-10.xlsx": R / "BUDGET_EASY_2026-10.xlsx",
    "DEPLOY_MAP_2026-10.png": R / "DEPLOY_MAP_2026-10.png",
    "DEPLOY_MAP_2026-10.pdf": R / "DEPLOY_MAP_2026-10.pdf",
    "SITUATION_MAP_2026-09.png": R / "SITUATION_MAP_2026-09.png",
    "SITUATION_MAP_2026-09.pdf": R / "SITUATION_MAP_2026-09.pdf",
    # gold-free share copies of Fig 1 / Fig 2 (build_map.py --no-gold; feedback B6): for readers outside AP
    "DEPLOY_MAP_2026-10_nogold.png": R / "DEPLOY_MAP_2026-10_nogold.png",
    "DEPLOY_MAP_2026-10_nogold.pdf": R / "DEPLOY_MAP_2026-10_nogold.pdf",
    "SITUATION_MAP_2026-09_nogold.png": R / "SITUATION_MAP_2026-09_nogold.png",
    "SITUATION_MAP_2026-09_nogold.pdf": R / "SITUATION_MAP_2026-09_nogold.pdf",
    "tex/refs.bib": PL / "refs.bib",
    "tex/plan/plan.yaml": BASE / "docs/plan/plan.yaml",
    "tex/plan/focus.yaml": BASE / "docs/plan/focus.yaml",
}
for n in ("main", "main_share", "main_ap", "main_final", "main_docx"):
    COPIES[f"tex/{n}.tex"] = PL / f"{n}.tex"
for n in ("SITUATION_MAP_full", "SITUATION_MAP_small", "DEPLOY_MAP_full", "DEPLOY_MAP_small"):
    COPIES[f"tex/{n}.jpg"] = PL / f"{n}.jpg"
for p in (BASE / "scripts/piptex").glob("*.py"):
    COPIES[f"tex/piptex/{p.name}"] = p


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="reports/TA_ARCHIVE_XSA_2026-10")
    ap.add_argument("--no-zip", action="store_true")
    a = ap.parse_args()
    out = BASE / a.out
    rep = out / "report"
    if not (out / "SOURCES.md").exists() or not rep.exists():
        sys.exit(f"{out} is not a finalized archive (run ta_archive_finalize.py first)")
    missing = [str(s) for s in COPIES.values() if not s.exists()]
    if missing:
        sys.exit(f"sources missing: {missing}")
    n = 0
    for dst, src in COPIES.items():
        d = rep / dst
        d.parent.mkdir(parents=True, exist_ok=True)
        if not d.exists() or sha(d) != sha(src):
            shutil.copyfile(src, d); n += 1
    for old in rep.glob("PIP_Pongo-Wau-Numatinna_*"): old.unlink()   # pre-rename copies
    txt = rep / "PIP_Radom_Numatina_Southern_v3_2026-10-16_final.txt"
    subprocess.run(["pdftotext", "-layout", str(rep / "PIP_Radom_Numatina_Southern_v3_2026-10-16_final.pdf"), str(txt)], check=True)
    if txt.stat().st_size < 50_000:
        sys.exit("pdftotext produced a suspiciously small file")
    print(f"report/: {n} files refreshed of {len(COPIES)} (+ pdftotext)")

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
    print(f"{total/1e9:.2f} GB total, {len(rows)} files -> {man}")
    if a.no_zip:
        return
    zp = out.with_suffix(".zip")
    if zp.exists():
        zp.unlink()
    subprocess.run(["zip", "-q", "-r", "-9", str(zp), out.name], cwd=out.parent, check=True)
    subprocess.run(["unzip", "-tq", str(zp)], check=True)
    print(f"zip: {zp} {zp.stat().st_size/1e9:.2f} GB (tested)")


if __name__ == "__main__":
    main()
