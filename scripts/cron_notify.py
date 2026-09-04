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
