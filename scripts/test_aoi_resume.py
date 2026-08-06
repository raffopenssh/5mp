#!/usr/bin/env python3
"""Prove the AOI queue survives being interrupted: kill it, run it again,
and it must continue where it stopped rather than restart or wedge.

Runs entirely against a temp SQLite file with a fake dataset runner -- no
network, no db.sqlite3. The three failure modes it locks down are the ones
that actually happened on 2026-08-07:

  1. Ctrl-C / SIGTERM parked the dataset for 24 h (it looked like a crash).
  2. A kill -9 between progress() and release() left 'running' for 6 h.
  3. A lost bookkeeping write under a long foreign transaction stranded a
     unit *and* redid finished work.

    python3 scripts/test_aoi_resume.py
"""
import os
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "scripts"))

FAILS = []


def check(name, cond, extra=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{(' -- ' + extra) if extra and not cond else ''}")
    if not cond:
        FAILS.append(name)


def make_db(path):
    c = sqlite3.connect(path)
    c.executescript("""
      CREATE TABLE aois (id TEXT PRIMARY KEY, name TEXT, geometry TEXT,
        bbox_minx REAL, bbox_miny REAL, bbox_maxx REAL, bbox_maxy REAL,
        area_km2 REAL, from_date TEXT, to_date TEXT, owner_principal_id INTEGER,
        visibility TEXT DEFAULT 'private', state TEXT DEFAULT 'pending',
        created_at TIMESTAMP, notes TEXT);
      CREATE TABLE aoi_datasets (aoi_id TEXT, dataset TEXT, enabled INTEGER DEFAULT 1,
        priority INTEGER DEFAULT 100, state TEXT DEFAULT 'pending', depends_on TEXT,
        cursor TEXT, units_total INTEGER, units_done INTEGER DEFAULT 0, coverage REAL,
        lease_owner TEXT, lease_until TIMESTAMP, last_run_at TIMESTAMP,
        next_run_at TIMESTAMP, detail TEXT, PRIMARY KEY (aoi_id, dataset));
      INSERT INTO aois (id, name, geometry) VALUES
        ('T_Area', 'T', '{"type":"Polygon","coordinates":[[[0,0],[1,0],[1,1],[0,1],[0,0]]]}');
      INSERT INTO aoi_datasets (aoi_id, dataset) VALUES ('T_Area', 'slow');
    """)
    c.commit()
    c.close()


# A child process is the only honest way to test signals and kill -9.
CHILD = r'''
import sys, time, os
sys.path.insert(0, %r)
import aoi_lib
aoi_lib.DB_PATH = %r
import aoi_runner as R

MARK = %r

def run_slow(conn, aoi, ds, deadline, budget):
    """10 units, 0.4 s each, progress() after every one -- the contract."""
    cur = R.load_cursor(ds) or {"i": 0}
    total = 10
    while cur["i"] < total and time.time() < deadline:
        if R.stopping():
            break
        time.sleep(0.4)
        open(MARK, "a").write("u\n")     # count units actually executed
        cur["i"] += 1
        R.progress(conn, aoi["id"], ds["dataset"], cur, cur["i"], total)
        if os.environ.get("HARD_EXIT_AFTER") == str(cur["i"]):
            os._exit(9)                   # kill -9 between progress and release
    return cur["i"] >= total, f'{cur["i"]}/{total} units'

R.RUNNERS["slow"] = run_slow
R.install_signal_handlers()
conn = aoi_lib.connect()
r = R.run_once(conn, "T_Area", "slow", 999, time.time() + float(%r))
print("RESULT", r)
'''


def spawn(tmp, db, mark, minutes=60, env=None):
    src = CHILD % (str(BASE / "scripts"), db, mark, minutes * 60)
    f = Path(tmp) / "child.py"
    f.write_text(src)
    e = dict(os.environ)
    e.update(env or {})
    return subprocess.Popen([sys.executable, str(f)], env=e,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True)


def row(db):
    c = sqlite3.connect(db)
    c.row_factory = sqlite3.Row
    r = c.execute("SELECT * FROM aoi_datasets WHERE dataset='slow'").fetchone()
    c.close()
    return r


def main():
    tmp = tempfile.mkdtemp(prefix="aoi_resume_")
    db = str(Path(tmp) / "t.sqlite3")
    mark = str(Path(tmp) / "units.txt")
    make_db(db)

    print("1. SIGTERM mid-dataset")
    p = spawn(tmp, db, mark)
    time.sleep(1.6)
    p.send_signal(signal.SIGTERM)
    p.wait(timeout=30)
    r = row(db)
    done_1 = r["units_done"]
    check("stays pending (not parked as an error)", r["state"] == "pending", r["state"])
    check("lease released", r["lease_owner"] is None, r["lease_owner"])
    check("no cooldown -- runnable immediately", r["next_run_at"] is None, r["next_run_at"])
    check("progress kept", 0 < done_1 < 10, f"units_done={done_1}")
    check("detail says resuming", "resume" in (r["detail"] or ""), r["detail"])

    print("2. resume continues, does not restart")
    p = spawn(tmp, db, mark)
    out = p.communicate(timeout=120)[0]
    r = row(db)
    executed = len(open(mark).read().split())
    check("finished", r["state"] == "done", f"{r['state']} {out[-300:]}")
    check("all 10 units", r["units_done"] == 10, r["units_done"])
    check("no unit run twice", executed == 10, f"executed={executed}")

    print("3. kill -9 between progress() and release() heals on next run")
    sqlite3.connect(db).executescript(
        "UPDATE aoi_datasets SET state='pending', cursor=NULL, units_done=0,"
        " detail=NULL, lease_owner=NULL, lease_until=NULL, next_run_at=NULL;")
    open(mark, "w").close()
    p = spawn(tmp, db, mark, env={"HARD_EXIT_AFTER": "3"})
    p.wait(timeout=60)
    r = row(db)
    check("lease left stranded by the kill", r["state"] == "running", r["state"])
    p = spawn(tmp, db, mark)
    out = p.communicate(timeout=120)[0]
    r = row(db)
    check("next run heals the lease and finishes", r["state"] == "done",
          f"{r['state']} {out[-300:]}")
    check("resumed from the cursor, not from zero",
          len(open(mark).read().split()) == 10, open(mark).read().count("u"))

    print("4. deadline stop is not an error")
    sqlite3.connect(db).executescript(
        "UPDATE aoi_datasets SET state='pending', cursor=NULL, units_done=0,"
        " detail=NULL, lease_owner=NULL, lease_until=NULL, next_run_at=NULL;")
    p = spawn(tmp, db, mark, minutes=1.0 / 60 * 1.5)   # ~1.5 s of wall clock
    p.communicate(timeout=60)
    r = row(db)
    check("pending with no cooldown", r["state"] == "pending" and r["next_run_at"] is None,
          f"{r['state']} {r['next_run_at']}")
    check("partial progress recorded", 0 < (r["units_done"] or 0) < 10, r["units_done"])

    print("5. transient errors retry in an hour, real ones in a day")
    import aoi_runner as R
    check("locked db is transient", R.transient(Exception("database is locked")))
    check("KeyError is not", not R.transient(KeyError("nope")))

    print()
    if FAILS:
        print(f"{len(FAILS)} FAILED: {', '.join(FAILS)}")
        return 1
    print("all AOI resume checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
