#!/usr/bin/env python3
"""Create / inspect AOI overlays.

    python3 scripts/aoi_admin.py list
    python3 scripts/aoi_admin.py show XSA_Study_Area
    python3 scripts/aoi_admin.py create --id XSA_Study_Area \
        --name "Study Area" --geojson data/study_areas/XSA_Study_Area.geojson \
        --from 2024-01-01 --owner-pwd '$AOI_OWNER_PWD'

The owner password is turned into a principal keyed by sha256(pwd)[:16]; the
secret itself is never stored (srv/aoi.go principalRef).
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import aoi_lib  # noqa: E402


def cmd_list(args):
    conn = aoi_lib.connect(readonly=True)
    for r in conn.execute(
            "SELECT id, name, area_km2, from_date, to_date, visibility, state "
            "FROM aois ORDER BY name"):
        print(f"{r['id']:<24} {r['area_km2']:>10,.0f} km2  "
              f"{r['from_date'] or '-'}..{r['to_date'] or 'now'}  "
              f"{r['visibility']}/{r['state']}  {r['name']}")


def cmd_show(args):
    conn = aoi_lib.connect(readonly=True)
    row = aoi_lib.load_aoi(conn, args.aoi_id)
    print(f"{row['id']}  {row['name']}")
    print(f"  area     {row['area_km2']:,.0f} km2")
    print(f"  bbox     {aoi_lib.aoi_bbox(row)}")
    print(f"  window   {row['from_date']} .. {row['to_date'] or 'now'}")
    print(f"  state    {row['visibility']} / {row['state']}")
    print(f"  fires    {conn.execute('SELECT COUNT(*) FROM aoi_fires WHERE aoi_id=?', (args.aoi_id,)).fetchone()[0]:,}")
    print("  datasets:")
    for d in conn.execute(
            "SELECT * FROM aoi_datasets WHERE aoi_id=? ORDER BY priority",
            (args.aoi_id,)):
        tot = d["units_total"] or 0
        done = d["units_done"] or 0
        pct = f"{100*done/tot:5.1f}%" if tot else "    -"
        print(f"    {d['dataset']:<14} {d['state']:<8} {done:>5}/{tot:<5} {pct}"
              f"  {(d['detail'] or '')[:60]}")


def cmd_create(args):
    geom = json.loads(Path(args.geojson).read_text())
    if geom.get("type") == "FeatureCollection":
        geom = geom["features"][0]["geometry"]
    elif geom.get("type") == "Feature":
        geom = geom["geometry"]
    conn = aoi_lib.connect()
    aoi_lib.upsert_aoi(conn, args.id, args.name, geom,
                       from_date=getattr(args, "from"), to_date=args.to,
                       owner_pwd=args.owner_pwd, visibility=args.visibility,
                       notes=args.notes)
    aoi_lib.seed_datasets(conn, args.id)
    print(f"created/updated {args.id}")
    cmd_show(argparse.Namespace(aoi_id=args.id))


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list").set_defaults(fn=cmd_list)
    p = sub.add_parser("show")
    p.add_argument("aoi_id")
    p.set_defaults(fn=cmd_show)
    p = sub.add_parser("create")
    p.add_argument("--id", required=True)
    p.add_argument("--name", required=True)
    p.add_argument("--geojson", required=True)
    p.add_argument("--from", dest="from", default=None)
    p.add_argument("--to", default=None)
    p.add_argument("--owner-pwd", default=None)
    p.add_argument("--visibility", default="private",
                   choices=["private", "shared", "public"])
    p.add_argument("--notes", default=None)
    p.set_defaults(fn=cmd_create)
    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
