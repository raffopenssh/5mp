"""Transparent gzip for the nightly JSON stores (data/fire_groups_v5,
data/export/fire_narratives).

Those directories are rewritten every night and gzip 6-11x (1.1 GB + 381 MB
raw -> ~230 MB), so since 2026-09-24 they are stored as `{area}.json.gz`.
Every reader and writer goes through this module so a *logical* path
(`.../COD_Virunga.json`) keeps working whichever form is on disk:

    from gzjson import gz
    groups = gz.load(GROUPS_DIR / f"{park}.json")      # reads .json or .json.gz
    gz.dump(GROUPS_DIR / f"{park}.json", groups)        # writes .json.gz atomically,
                                                        # removes a stale plain .json
    gz.exists(p); gz.path(p)                            # which form is on disk
    for p in gz.glob(GROUPS_DIR): area = gz.area_id(p)  # `*.json` + `*.json.gz`

Plain `.json` still wins when both exist (a hand-dropped file for a test
beats last night's archive), and a writer removes the other form so the two
can never disagree about one area.  `dump` is atomic (tmp + rename) because
the readers run concurrently with the 03:00 writer.
"""
import gzip
import json
import os
from pathlib import Path

GZ_LEVEL = 6


def _p(lp):
    p = Path(lp)
    if p.suffix == ".gz":
        p = p.with_suffix("")   # logical name is always the .json one
    return p


def path(lp):
    """Return the Path that actually exists for a logical `.json` path,
    or None. Plain `.json` takes precedence over `.json.gz`."""
    p = _p(lp)
    if p.exists():
        return p
    g = p.with_name(p.name + ".gz")
    if g.exists():
        return g
    return None


def exists(lp):
    return path(lp) is not None


def open_read(lp):
    real = path(lp)
    if real is None:
        raise FileNotFoundError(str(_p(lp)))
    if real.suffix == ".gz":
        return gzip.open(real, "rt", encoding="utf-8")
    return open(real, "r", encoding="utf-8")


def load(lp):
    with open_read(lp) as fh:
        return json.load(fh)


def read_bytes(lp):
    """Decompressed bytes (for callers that hash or shell out)."""
    real = path(lp)
    if real is None:
        raise FileNotFoundError(str(_p(lp)))
    if real.suffix == ".gz":
        with gzip.open(real, "rb") as fh:
            return fh.read()
    return real.read_bytes()


def dump(lp, obj, **kw):
    """Write `obj` as `{path}.gz` atomically and drop a stale plain `.json`.
    Extra kwargs go to json.dumps (indent, default, ...)."""
    p = _p(lp)
    p.parent.mkdir(parents=True, exist_ok=True)
    g = p.with_name(p.name + ".gz")
    tmp = g.with_name(g.name + f".tmp{os.getpid()}")
    kw.setdefault("separators", (",", ":"))
    with gzip.open(tmp, "wt", encoding="utf-8", compresslevel=GZ_LEVEL) as fh:
        json.dump(obj, fh, **kw)
    os.replace(tmp, g)
    if p.exists():
        p.unlink()
    return g


def remove(lp):
    """Delete whichever forms exist. Returns number removed."""
    p = _p(lp)
    n = 0
    for c in (p, p.with_name(p.name + ".gz")):
        if c.exists():
            c.unlink()
            n += 1
    return n


def area_id(lp):
    """`.../COD_Virunga.json.gz` -> `COD_Virunga`."""
    name = Path(lp).name
    for suf in (".json.gz", ".json"):
        if name.endswith(suf):
            return name[: -len(suf)]
    return Path(lp).stem


def glob(directory, pattern="*"):
    """Sorted logical `.json` Paths for every `{pattern}.json` /
    `{pattern}.json.gz` in `directory`, de-duplicated by area."""
    d = Path(directory)
    seen = {}
    for real in list(d.glob(pattern + ".json")) + list(d.glob(pattern + ".json.gz")):
        seen.setdefault(area_id(real), d / (area_id(real) + ".json"))
    return [seen[k] for k in sorted(seen)]


def mtime(lp):
    real = path(lp)
    return real.stat().st_mtime if real else None


# `from gzjson import gz` gives a namespace object so call sites read as gz.load()
import sys as _sys
gz = _sys.modules[__name__]
