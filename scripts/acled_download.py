#!/usr/bin/env python3
"""Log in to myACLED and download the latest Africa aggregated data file.

ACLED updates that file every Monday, so this is the refresh step in front of
scripts/acled_aggregate.py. It uses cookie-based authentication (the same thing
a browser does): POST the Drupal login form, keep the session cookie, scrape the
current .xlsx href off the aggregated-data page, download it.

Credentials come from secrets.env -- ACLED_USERNAME / ACLED_PASSWORD.

Usage:
    set -a; source secrets.env; set +a
    python3 scripts/acled_download.py && python3 scripts/acled_aggregate.py

The filename carries the week, so files accumulate in data/acled/ and the
aggregate step always takes the newest. Nothing here is derived or reformatted:
what lands on disk is ACLED's own file, which is the version we can point at
when we say where a number came from.
"""
import http.cookiejar
import os
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path

BASE = "https://acleddata.com"
LOGIN = BASE + "/user/login"
PAGE = BASE + "/aggregated/aggregated-data-africa"
OUT_DIR = Path(__file__).resolve().parent.parent / "data" / "acled"
UA = "5mp-conservation-monitor/1.0 (+conservation research; ACLED data user)"


def opener():
    jar = http.cookiejar.CookieJar()
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))


def get(op, url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with op.open(req, timeout=120) as r:
        return r.read().decode("utf-8", "replace")


def main():
    user, pwd = os.environ.get("ACLED_USERNAME"), os.environ.get("ACLED_PASSWORD")
    if not user or not pwd:
        raise SystemExit("set ACLED_USERNAME / ACLED_PASSWORD "
                         "(set -a; source secrets.env; set +a)")
    op = opener()

    # Drupal form needs the build id from the rendered page
    page = get(op, LOGIN)
    m = re.search(r'name="form_build_id"\s+value="([^"]+)"', page)
    if not m:
        raise SystemExit("no form_build_id on the login page -- form changed")
    body = urllib.parse.urlencode({
        "name": user, "pass": pwd, "form_build_id": m.group(1),
        "form_id": "user_login_form", "op": "Log in",
    }).encode()
    req = urllib.request.Request(LOGIN, data=body, headers={
        "User-Agent": UA, "Content-Type": "application/x-www-form-urlencoded"})
    with op.open(req, timeout=120) as r:
        after = r.read().decode("utf-8", "replace")
    if "Unrecognized username or password" in after:
        raise SystemExit("myACLED login rejected -- check ACLED_* in secrets.env")
    if "user/logout" not in after and "Log out" not in after:
        raise SystemExit("login did not take (no logout link in response)")
    print(f"logged in as {user}")

    html = get(op, PAGE)
    hrefs = re.findall(r'href="([^"]*Africa_aggregated_data[^"]*\.xlsx)"', html)
    if not hrefs:
        raise SystemExit(
            "no Africa_aggregated_data .xlsx link on " + PAGE +
            " -- either the page changed or this account's access level no "
            "longer includes aggregated files")
    url = urllib.parse.urljoin(BASE, hrefs[0])
    name = url.rsplit("/", 1)[-1]
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    dest = OUT_DIR / name
    if dest.exists():
        print(f"{name} already present ({dest.stat().st_size} bytes) -- up to date")
        return 0
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with op.open(req, timeout=600) as r:
        data = r.read()
    # A short body here is an error page, not a spreadsheet: say so rather than
    # let the aggregate step parse silence into zero rows (AGENTS.md invariant 1)
    if len(data) < 100_000 or data[:2] != b"PK":
        raise SystemExit(f"{name} came back as {len(data)} bytes of non-xlsx "
                         f"-- access denied or link expired")
    dest.write_bytes(data)
    print(f"{name}: {len(data)} bytes -> {dest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
