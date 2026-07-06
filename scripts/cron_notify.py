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


def notify_status(ntype, title, message, park_id="SYSTEM", db_path=None):
    """Insert a status notification; never raises (cron must not die on this)."""
    try:
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
