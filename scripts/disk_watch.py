#!/usr/bin/env python3
"""Disk growth monitor with attribution and quiet alerting.

    */15 * * * *  python3 scripts/disk_watch.py          (cron)
    python3 scripts/disk_watch.py --status                (human summary)
    python3 scripts/disk_watch.py --test-email            (one test mail)

Why this exists: on 2026-09-04 and 2026-09-10 the nightly fire job left a
13–29 GB -wal behind a leaked reader and the only signal was the disk graph.
The fix for that is in db/db.go + srv/wal_checkpoint.go; this script is the
independent watcher that says so if it ever happens again — for any cause.

Design: sample every 15 min (df, db, -wal, data/*, logs, /tmp, shared files),
keep 30 days of samples, and email ONLY on these conditions, each deduped:

  overnight   growth 20:00→08:00 UTC > OVERNIGHT_MB (default 100) that is
              NOT attributable to a user action (AOI run, upload). Sent once,
              at the first tick after 08:00 UTC. A quiet night sends nothing.
  user        one 15-min window grows > USER_MB (default 1024) while an AOI
              dataset or upload was running. Once per AOI per day. This is
              the "a user asked for a lot" case — informational, not a fault.
  capacity    used ≥ 80 % (warn) / ≥ 90 % (critical), or the 24 h growth rate
              fills the disk in < 7 days. Alert on crossing; re-alert every
              24 h (warn) / 6 h (critical); one "recovered" mail.
  wal         -wal > WAL_GB (default 2) on two consecutive ticks. Once / 24 h.
  cap         at most MAX_MAILS_PER_DAY (4); beyond that, bell only.

Every alert also writes a `disk_watch_alert` row into the notification bell;
a daily `disk_watch_success` row (at the 08:00 tick) says "watched, quiet" so
silence in the bell means the watcher did not run (docs/agents/ops.md).

Recipient: ALERT_EMAIL in secrets.env (falls back to the VM owner if the
exe.dev gateway knows it). Mail is sent via the exe.dev email gateway.
"""
import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "db.sqlite3")
STATE_DIR = os.path.join(ROOT, "data", "disk_watch")
SAMPLES = os.path.join(STATE_DIR, "samples.jsonl")
STATE = os.path.join(STATE_DIR, "state.json")

OVERNIGHT_MB = float(os.environ.get("DISK_WATCH_OVERNIGHT_MB", 100))
USER_MB = float(os.environ.get("DISK_WATCH_USER_MB", 1024))
WAL_GB = float(os.environ.get("DISK_WATCH_WAL_GB", 2))
WARN_PCT, CRIT_PCT = 80, 90
DAYS_TO_FULL = 7
MAX_MAILS_PER_DAY = 4
KEEP_DAYS = 30
NIGHT_START_H, NIGHT_END_H = 20, 8   # UTC

# Paths whose growth we attribute individually. Everything else on / is "other".
WATCHED = {
    "db": DB,
    "wal": DB + "-wal",
    "data": os.path.join(ROOT, "data"),
    "logs": os.path.join(ROOT, "logs"),
    "tmp": "/tmp",
    "journal": "/var/log/journal",
}

sys.path.insert(0, os.path.join(ROOT, "scripts"))
from cron_notify import notify_status  # noqa: E402


def load_secrets():
    p = os.path.join(ROOT, "secrets.env")
    try:
        for line in open(p):
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    except OSError:
        pass


def utcnow():
    return datetime.now(timezone.utc)


def du_bytes(path):
    """Byte size of a path (du -sb; files via stat). 0 if absent."""
    if not os.path.exists(path):
        return 0
    if os.path.isfile(path):
        return os.path.getsize(path)
    try:
        out = subprocess.run(["du", "-sbx", path], capture_output=True, text=True, timeout=120).stdout
        return int(out.split()[0])
    except Exception:  # noqa: BLE001
        return 0


def du_children(path, top=6):
    """Largest immediate children of a directory, {name: bytes}."""
    try:
        out = subprocess.run(["du", "-sbx"] + [os.path.join(path, c) for c in os.listdir(path)],
                             capture_output=True, text=True, timeout=300).stdout
    except Exception:  # noqa: BLE001
        return {}
    sizes = {}
    for line in out.splitlines():
        parts = line.split("\t", 1)
        if len(parts) == 2:
            sizes[os.path.basename(parts[1])] = int(parts[0])
    return sizes


def running_jobs():
    """Repo scripts currently running (what could be writing right now)."""
    try:
        out = subprocess.run(["ps", "-eo", "pid,etimes,args"], capture_output=True, text=True).stdout
    except Exception:  # noqa: BLE001
        return []
    jobs = []
    for line in out.splitlines()[1:]:
        if ("scripts/" in line or "analysis/" in line) and ".py" in line and "disk_watch" not in line:
            parts = line.split(None, 2)
            if len(parts) == 3:
                jobs.append({"pid": int(parts[0]), "secs": int(parts[1]),
                             "cmd": parts[2][:120]})
    return jobs


def user_activity(since):
    """User-initiated work since `since`: which AOIs (name + owner label) ran,
    who uploaded, who shared files. Owner is the principal label (first
    letters of the login), never the login itself."""
    act = {"aoi": [], "uploads": [], "shared": [], "who": set()}
    try:
        c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=10)
        c.execute("PRAGMA busy_timeout=10000")
        iso = since.strftime("%Y-%m-%d %H:%M:%S")
        for name, label, ds in c.execute(
                """SELECT a.name, COALESCE(p.label, '?'), d.dataset
                   FROM aoi_datasets d JOIN aois a ON a.id = d.aoi_id
                   LEFT JOIN principals p ON p.id = a.owner_principal_id
                   WHERE d.last_run_at >= ? OR (d.state='running' AND d.lease_until >= datetime('now'))""",
                (iso,)):
            act["aoi"].append((name, label, ds))
            act["who"].add(label)
        for email, n in c.execute(
                "SELECT user_email, COUNT(*) FROM upload_queue WHERE created_at >= ? GROUP BY 1", (iso,)):
            act["uploads"].append((email, n)); act["who"].add(email)
        for ref, n, b in c.execute(
                "SELECT pwd_ref, COUNT(*), COALESCE(SUM(size_bytes),0) FROM shared_files "
                "WHERE created_at >= ? GROUP BY 1", (since.strftime("%Y-%m-%dT%H:%M:%S"),)):
            label = c.execute("SELECT label FROM principals WHERE ref=?", (ref,)).fetchone()
            label = label[0] if label else ref[:4]
            act["shared"].append((label, n, b)); act["who"].add(label)
        c.close()
    except Exception as ex:  # noqa: BLE001
        act["error"] = str(ex)[:120]
    act["any"] = bool(act["aoi"] or act["uploads"] or act["shared"])
    return act


def short_login(label):
    """First three characters of the login + ellipsis. The principals.label
    column is already stored this way; enforce it here so a mail can never
    carry a password even if a label were ever stored in full."""
    label = (label or "?").rstrip("…")
    return label[:3] + "…"


def describe_activity(act):
    """'user tes… ran AOI "Chinko East" (ghsl, fire_v5); user apn… uploaded 3 GPX files'."""
    parts = []
    by_aoi = {}
    for name, label, ds in act["aoi"]:
        by_aoi.setdefault((name, label), []).append(ds)
    for (name, label), dss in by_aoi.items():
        parts.append(f'login {short_login(label)} ran AOI "{name}" ({", ".join(sorted(dss))})')
    for email, n in act["uploads"]:
        parts.append(f"{email} uploaded {n} GPX file{'s' if n != 1 else ''}")
    for label, n, b in act["shared"]:
        parts.append(f"login {short_login(label)} shared {n} file{'s' if n != 1 else ''} ({fmt_abs(b)})")
    return "; ".join(parts) or "no user activity"


def sample():
    st = shutil.disk_usage("/")
    s = {"t": utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
         "total": st.total, "used": st.used, "free": st.free,
         "paths": {k: du_bytes(p) for k, p in WATCHED.items()},
         "data_children": du_children(os.path.join(ROOT, "data")),
         "jobs": running_jobs()}
    return s


def load_samples():
    out = []
    try:
        for line in open(SAMPLES):
            try:
                out.append(json.loads(line))
            except ValueError:
                pass
    except OSError:
        pass
    return out


def save_samples(samples):
    cutoff = (utcnow() - timedelta(days=KEEP_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    os.makedirs(STATE_DIR, exist_ok=True)
    tmp = SAMPLES + ".tmp"
    with open(tmp, "w") as f:
        for s in samples:
            if s["t"] >= cutoff:
                f.write(json.dumps(s, separators=(",", ":")) + "\n")
    os.replace(tmp, SAMPLES)


def load_state():
    try:
        return json.load(open(STATE))
    except (OSError, ValueError):
        return {"sent": {}, "mails": [], "level": "ok", "wal_high_ticks": 0,
                "last_night_report": None, "last_daily_bell": None}


def save_state(st):
    os.makedirs(STATE_DIR, exist_ok=True)
    tmp = STATE + ".tmp"
    json.dump(st, open(tmp, "w"), indent=1)
    os.replace(tmp, STATE)


def parse_t(s):
    return datetime.strptime(s["t"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def nearest(samples, when):
    """Sample closest to `when` (None if none within 2 h)."""
    best, bd = None, timedelta(hours=2)
    for s in samples:
        d = abs(parse_t(s) - when)
        if d < bd:
            best, bd = s, d
    return best


def fmt(b):
    b = float(b)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(b) < 1024 or unit == "TB":
            return f"{b:+.1f} {unit}" if unit != "B" else f"{b:+.0f} B"
        b /= 1024


def fmt_abs(b):
    return fmt(b).lstrip("+")


def breakdown(a, b, top=3):
    """'the SQLite WAL (+11.2 GB), data/osm_geofabrik (+0.9 GB) and logs (+120 MB)'."""
    names = {"db": "the database file", "wal": "the SQLite WAL (db.sqlite3-wal)",
             "logs": "logs/", "tmp": "/tmp", "journal": "the system journal"}
    items = []
    for k in WATCHED:
        if k == "data":
            continue
        d = b["paths"].get(k, 0) - a["paths"].get(k, 0)
        if abs(d) >= 10 << 20:
            items.append((abs(d), f"{names[k]} ({fmt(d)})"))
    for name, size in b.get("data_children", {}).items():
        d = size - a.get("data_children", {}).get(name, 0)
        if abs(d) >= 10 << 20:
            items.append((abs(d), f"data/{name} ({fmt(d)})"))
    accounted = sum(b["paths"].get(k, 0) - a["paths"].get(k, 0) for k in WATCHED)
    other = (b["used"] - a["used"]) - accounted
    if abs(other) >= 50 << 20:
        items.append((abs(other), f"files outside the watched paths ({fmt(other)})"))
    items = [t for _, t in sorted(items, reverse=True)][:top]
    if not items:
        return "no single path grew by 10 MB or more"
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + " and " + items[-1]


def jobs_sentence(jobs):
    if not jobs:
        return "No scheduled job is running right now."
    names = [os.path.basename(j["cmd"].split()[1]) if len(j["cmd"].split()) > 1 else j["cmd"]
             for j in jobs]
    return "Running right now: " + ", ".join(names) + "."


# ---------------------------------------------------------------- alerting

def send_email(subject, body):
    to = os.environ.get("ALERT_EMAIL")
    if not to:
        print("ALERT_EMAIL not set; not sending", file=sys.stderr)
        return False
    req = urllib.request.Request(
        "http://169.254.169.254/gateway/email/send",
        data=json.dumps({"to": to, "subject": subject, "body": body}).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return 200 <= r.status < 300
    except Exception as ex:  # noqa: BLE001
        print(f"email failed: {ex}", file=sys.stderr)
        return False


def alert(st, key, cooldown_h, subject, body, bell_title):
    """Send one alert unless `key` fired within cooldown_h or the daily cap is hit.
    Always writes the bell row. Returns True if mailed."""
    now = utcnow()
    last = st["sent"].get(key)
    if last and now - datetime.fromisoformat(last) < timedelta(hours=cooldown_h):
        return False
    st["sent"][key] = now.isoformat()
    day_ago = (now - timedelta(days=1)).isoformat()
    st["mails"] = [m for m in st["mails"] if m > day_ago]
    capped = len(st["mails"]) >= MAX_MAILS_PER_DAY
    notify_status("disk_watch_alert", bell_title,
                  body.splitlines()[0][:400] + (" · mail capped" if capped else ""))
    if capped:
        print(f"[cap] not mailing: {subject}")
        return False
    body += ("\n\n— 5MP disk watch. Live numbers: "
             "https://five-megapixel-conservation.exe.xyz:8000/api/admin/db-health")
    if send_email(f"[5MP] {subject}", body):
        st["mails"].append(now.isoformat())
        print(f"[mail] {subject}")
        return True
    return False


def check(samples, st, cur):
    now = parse_t(cur)
    prev = samples[-2] if len(samples) >= 2 else None
    pct = 100.0 * cur["used"] / cur["total"]
    state = (f"The disk is {pct:.0f}% full ({fmt_abs(cur['used'])} of {fmt_abs(cur['total'])}, "
             f"{fmt_abs(cur['free'])} free); the database is {fmt_abs(cur['paths']['db'])} "
             f"and its WAL {fmt_abs(cur['paths']['wal'])}.")
    footer = "\n\n" + jobs_sentence(cur["jobs"]) + "\n" + state

    # --- capacity (level transitions + projection)
    level = "critical" if pct >= CRIT_PCT else "warn" if pct >= WARN_PCT else "ok"
    day = nearest(samples, now - timedelta(days=1))
    proj = ""
    if day and cur["used"] > day["used"]:
        rate = (cur["used"] - day["used"]) / max((now - parse_t(day)).total_seconds(), 1)
        days_left = cur["free"] / rate / 86400
        if days_left < DAYS_TO_FULL:
            proj = (f" At the current rate ({fmt(cur['used'] - day['used'])} in the last 24 h) "
                    f"it will be full in about {days_left:.1f} days.")
            if level == "ok":
                level = "warn"
    if level != "ok":
        cooldown = 6 if level == "critical" else 24
        body = (f"The disk is {pct:.0f}% full.{proj} Over the last 24 h the growth came from "
                + (breakdown(day, cur) if day else "an unknown source (no sample from 24 h ago)") + "." + footer)
        alert(st, f"capacity_{level}", cooldown, f"disk {level}: {pct:.0f}% used", body,
              f"Disk {level}: {pct:.0f}% used")
    elif st.get("level") in ("warn", "critical"):
        alert(st, "capacity_recovered", 1, f"disk recovered: {pct:.0f}% used",
              f"Disk usage is back to {pct:.0f}%." + footer, f"Disk recovered: {pct:.0f}% used")
    st["level"] = level

    # --- WAL stuck
    wal_gb = cur["paths"]["wal"] / (1 << 30)
    st["wal_high_ticks"] = st.get("wal_high_ticks", 0) + 1 if wal_gb >= WAL_GB else 0
    if st["wal_high_ticks"] >= 2:
        alert(st, "wal_stuck", 24, f"WAL stuck at {wal_gb:.1f} GB",
              f"The SQLite write-ahead log is {wal_gb:.1f} GB and has not been reclaimed for at least "
              f"30 minutes. Something inside the server is holding an old read snapshot, so the "
              f"checkpoint cannot truncate it; a restart of the 5mp service frees it, and the cause "
              f"is in `journalctl -u 5mp | grep 'wal checkpoint'`." + footer,
              f"WAL stuck at {wal_gb:.1f} GB")

    # --- user request: a single window with large growth while user work ran
    if prev:
        d = cur["used"] - prev["used"]
        if d > USER_MB * (1 << 20):
            act = user_activity(parse_t(prev))
            if act["any"]:
                who = short_login(sorted(act["who"])[0])
                key = "user_" + who
                mins = int((now - parse_t(prev)).total_seconds() // 60)
                alert(st, key, 24, f"user request added {fmt_abs(d)}",
                      f"In the last {mins} minutes the disk grew by {fmt_abs(d)} while "
                      f"{describe_activity(act)}. The growth is in {breakdown(prev, cur)}. "
                      f"This is user-generated and expected; no action needed unless it repeats." + footer,
                      f"User request added {fmt_abs(d)}")

    # --- overnight: first tick after 08:00 UTC, once per day
    today = now.strftime("%Y-%m-%d")
    if now.hour >= NIGHT_END_H and st.get("last_night_report") != today:
        st["last_night_report"] = today
        start = nearest(samples, now.replace(hour=NIGHT_START_H, minute=0, second=0) - timedelta(days=1))
        end = nearest(samples, now.replace(hour=NIGHT_END_H, minute=0, second=0))
        if start and end:
            d = end["used"] - start["used"]
            act = user_activity(parse_t(start))
            if d > OVERNIGHT_MB * (1 << 20) and not act["any"]:
                alert(st, f"overnight_{today}", 20, f"nightly jobs added {fmt_abs(d)}",
                      f"Between {start['t'][11:16]} and {end['t'][11:16]} UTC the disk grew by {fmt_abs(d)}, "
                      f"more than the {OVERNIGHT_MB:g} MB a normal night costs. No user ran an AOI, "
                      f"uploaded or shared anything in that window, so this came from the scheduled "
                      f"cron jobs. The growth is in {breakdown(start, end)}. "
                      f"The job logs for that window are in logs/*.log." + footer,
                      f"Nightly jobs added {fmt_abs(d)}")
            else:
                why = f" ({describe_activity(act)})" if act["any"] and d > OVERNIGHT_MB * (1 << 20) else ""
                notify_status("disk_watch_success", "Disk watch: quiet night",
                              f"{fmt(d)} overnight{why} · {pct:.0f}% used")
        else:
            notify_status("disk_watch_failed", "Disk watch: no overnight samples",
                          "cron may not have run overnight · " + state)


def status(samples, st):
    if not samples:
        print("no samples yet")
        return
    cur = samples[-1]
    now = parse_t(cur)
    pct = 100.0 * cur["used"] / cur["total"]
    print(f"{cur['t']}  used {fmt_abs(cur['used'])} / {fmt_abs(cur['total'])} ({pct:.0f}%)  "
          f"db {fmt_abs(cur['paths']['db'])}  wal {fmt_abs(cur['paths']['wal'])}  level={st.get('level')}")
    for label, dt in (("1 h", timedelta(hours=1)), ("24 h", timedelta(days=1)), ("7 d", timedelta(days=7))):
        a = nearest(samples, now - dt)
        if a:
            print(f"{label}: {fmt(cur['used'] - a['used'])} — {breakdown(a, cur)}")
    print("\nlast alerts:", json.dumps(st.get("sent", {}), indent=1))
    print("mails last 24h:", len([m for m in st.get('mails', []) if m > (utcnow()-timedelta(days=1)).isoformat()]))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--test-email", action="store_true")
    a = ap.parse_args()
    load_secrets()
    if a.test_email:
        cur = sample()
        pct = 100.0 * cur["used"] / cur["total"]
        ok = send_email("[5MP] disk_watch test",
                        f"If you read this, alerting works. The disk is {pct:.0f}% full "
                        f"({fmt_abs(cur['used'])} of {fmt_abs(cur['total'])}); the database is "
                        f"{fmt_abs(cur['paths']['db'])} and its WAL {fmt_abs(cur['paths']['wal'])}.")
        print("sent" if ok else "FAILED")
        return
    samples = load_samples()
    st = load_state()
    if a.status:
        status(samples, st)
        return
    cur = sample()
    samples.append(cur)
    try:
        check(samples, st, cur)
    finally:
        save_samples(samples)
        save_state(st)


if __name__ == "__main__":
    main()
