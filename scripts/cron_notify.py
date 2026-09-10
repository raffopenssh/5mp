"""Shared cron status notifications — mirrors daily_fire_update.py's
SYSTEM notifications so all daily jobs report into the notification panel.

Usage:
    from cron_notify import notify_status
    notify_status('turbidity_scan_success', 'Turbidity Scan Complete',
                  'CAF_Chinko: 4 alerts, 21 rivers scanned')
"""
import os
import sqlite3
import sys

_DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "db.sqlite3")


def wal_size_gb(db_path=None):
    """Size of the SQLite -wal file in GiB (0.0 if absent)."""
    try:
        return os.path.getsize((db_path or _DB) + "-wal") / (1 << 30)
    except OSError:
        return 0.0


def wal_suffix(db_path=None):
    """' · WAL 3.2 GB' when the WAL is worth mentioning, else ''.

    Appended to every cron status so the bell shows which nightly job the
    WAL grew behind. On 2026-09-04 the -wal reached 28.9 GB (db 22.7 GB)
    over several nights and nothing surfaced it until the disk hit 95%.
    Below 0.5 GB it is normal churn and stays out of the message.
    """
    gb = wal_size_gb(db_path)
    return f" · WAL {gb:.1f} GB" if gb >= 0.5 else ""


def checkpoint_wal(db_path=None, log=print):
    """Checkpoint + truncate the WAL after a batch writer finishes.

    A nightly job that appends gigabytes to the -wal must leave the file
    behind it reclaimed; relying on the server's hourly worker means the
    WAL sits at full size for up to an hour, and if any reader is pinning a
    snapshot the job's writes are never reclaimed at all. Returns
    (busy, wal_frames, checkpointed_frames, wal_gb_after). busy=1 with
    frames left means a reader is holding a snapshot: the job reports it
    (the notification carries the WAL size) and the server's worker, which
    can recycle its own idle connections, takes over. Never raises.
    """
    try:
        conn = sqlite3.connect(db_path or _DB, timeout=60)
        conn.execute("PRAGMA busy_timeout=60000")
        busy, frames, ckpt = conn.execute(
            "PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        conn.close()
        gb = wal_size_gb(db_path)
        state = "ok" if busy == 0 else f"BLOCKED ({ckpt}/{frames} frames)"
        log(f"WAL checkpoint(TRUNCATE): {state}, wal now {gb:.2f} GB")
        return busy, frames, ckpt, gb
    except Exception as ex:  # noqa: BLE001
        log(f"WAL checkpoint failed: {ex}")
        return 1, -1, -1, wal_size_gb(db_path)


def notify_status(ntype, title, message, park_id="SYSTEM", db_path=None):
    """Insert a status notification; never raises (cron must not die on this)."""
    try:
        message = message[:480] + wal_suffix(db_path)
        conn = sqlite3.connect(db_path or _DB, timeout=30)
        conn.execute(
            """INSERT INTO notifications
               (park_id, notification_type, title, message, created_at)
               VALUES (?, ?, ?, ?, datetime('now'))""",
            (park_id, ntype, title, message[:500]))
        conn.commit()
        conn.close()
    except Exception as ex:  # noqa: BLE001
        print(f"cron_notify failed: {ex}", file=sys.stderr)
