#!/usr/bin/env python3
"""Stage 1: get text out of the EASY document dump (data/easy_docs/raw).

Born-digital PDFs -> pdftotext -layout. .doc -> antiword/catdoc. .docx -> XML
strip. .xlsx -> openpyxl. Scans (pdftotext yields ~nothing) and photos are left
to ocr_scans.py.

Writes txt/<filename>.txt and a manifest row per file in docs.sqlite3:
  files(name PK, ext, bytes, pages, chars, method, sha1)

A file whose text is shorter than MIN_CHARS_PER_PAGE*pages is recorded with
method='needs_ocr' and chars=0 -- never as done. (AGENTS.md invariant 1: a
no-op must not read as an answer.)

Usage: python3 extract.py [--dir data/easy_docs]
"""
import argparse, glob, hashlib, os, re, shutil, sqlite3, subprocess, sys, zipfile

MIN_CHARS_PER_PAGE = 120


def db(root):
    c = sqlite3.connect(os.path.join(root, "docs.sqlite3"), timeout=60)
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("""CREATE TABLE IF NOT EXISTS files(
        name TEXT PRIMARY KEY, ext TEXT, bytes INTEGER, pages INTEGER,
        chars INTEGER, method TEXT, sha1 TEXT, dup_of TEXT)""")
    return c


def sha1(p):
    h = hashlib.sha1()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def pdf_pages(p):
    try:
        out = subprocess.run(["pdfinfo", p], capture_output=True, timeout=120).stdout.decode("utf8", "ignore")
        m = re.search(r"^Pages:\s+(\d+)", out, re.M)
        if m:
            return int(m.group(1))
    except Exception:
        pass
    # damaged xref: count rendered pages the slow way
    try:
        out = subprocess.run(["qpdf", "--show-npages", p], capture_output=True, timeout=120).stdout.decode().strip()
        return int(out)
    except Exception:
        return 0


def docx_text(p):
    z = zipfile.ZipFile(p)
    parts = [n for n in z.namelist()
             if re.match(r"word/(document|footnotes|endnotes|header\d*|footer\d*)\.xml", n)
             or n.startswith("ppt/slides/slide") or n.startswith("ppt/notesSlides/")]
    chunks = []
    for n in sorted(parts):
        x = z.read(n).decode("utf8", "ignore")
        x = re.sub(r"</a:p>|</w:p>|</a:t><", "\n<", x)
        x = re.sub(r"<[^>]+>", "", x)
        x = re.sub(r"\n{3,}", "\n\n", x)
        chunks.append(f"[[{n}]]\n" + x)
    return "\n".join(chunks)


def xlsx_text(p):
    import openpyxl
    wb = openpyxl.load_workbook(p, data_only=True)
    lines = []
    for ws in wb:
        lines.append(f"## sheet: {ws.title}")
        for r in ws.iter_rows(values_only=True):
            if any(c is not None for c in r):
                lines.append(" | ".join("" if c is None else str(c) for c in r))
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default="/home/exedev/5mp/data/easy_docs")
    a = ap.parse_args()
    root = a.dir
    os.makedirs(os.path.join(root, "txt"), exist_ok=True)
    c = db(root)
    seen = {}
    for r in c.execute("SELECT sha1, name FROM files WHERE dup_of IS NULL"):
        seen[r[0]] = r[1]

    for p in sorted(glob.glob(os.path.join(root, "raw", "*"))):
        if not os.path.isfile(p):
            continue
        name = os.path.basename(p)
        ext = name.rsplit(".", 1)[-1].lower()
        out = os.path.join(root, "txt", name + ".txt")
        h = sha1(p)
        nbytes = os.path.getsize(p)
        pages, method, text = 0, "none", ""
        dup = seen.get(h)
        if dup and dup != name:
            c.execute("INSERT OR REPLACE INTO files VALUES(?,?,?,?,?,?,?,?)",
                      (name, ext, nbytes, 0, 0, "duplicate", h, dup))
            c.commit()
            print(f"dup     {name}  == {dup}")
            continue
        seen.setdefault(h, name)
        try:
            if ext == "pdf":
                pages = pdf_pages(p)
                subprocess.run(["pdftotext", "-layout", p, "-"], capture_output=True, timeout=600)
                r = subprocess.run(["pdftotext", "-layout", p, "-"], capture_output=True, timeout=600)
                text = r.stdout.decode("utf8", "ignore")
                method = "pdftotext"
            elif ext == "doc":
                r = subprocess.run(["antiword", "-w", "0", p], capture_output=True, timeout=300)
                text = r.stdout.decode("utf8", "ignore")
                method = "antiword"
                if len(text) < 500:
                    r = subprocess.run(["catdoc", p], capture_output=True, timeout=300)
                    if len(r.stdout) > len(text):
                        text, method = r.stdout.decode("utf8", "ignore"), "catdoc"
            elif ext in ("docx", "pptx"):
                text, method = docx_text(p), "ooxml"
            elif ext == "xlsx":
                text, method = xlsx_text(p), "openpyxl"
            elif ext in ("jpg", "jpeg", "png", "tif", "tiff", "webp"):
                pages, method = 1, "needs_ocr"
            else:
                method = "unsupported"
        except Exception as e:
            method = f"error:{type(e).__name__}"
            print(f"ERR     {name}: {e}", file=sys.stderr)

        text = text.replace("\x0c", "\n")
        nchars = len(re.sub(r"\s", "", text))
        floor = MIN_CHARS_PER_PAGE * max(pages, 1)
        if method in ("pdftotext",) and nchars < floor:
            method = "needs_ocr"
            nchars = 0
            text = ""
        if text:
            with open(out, "w") as f:
                f.write(text)
        elif os.path.exists(out):
            os.remove(out)
        c.execute("INSERT OR REPLACE INTO files VALUES(?,?,?,?,?,?,?,?)",
                  (name, ext, nbytes, pages, nchars, method, h, None))
        c.commit()
        print(f"{method:10s} {pages:4d}p {nchars:8d}ch  {name}")

    print()
    for row in c.execute("SELECT method, count(*), sum(pages) FROM files GROUP BY method ORDER BY 2 DESC"):
        print(f"  {row[0]:12s} {row[1]:3d} files {row[2] or 0:5d} pages")


if __name__ == "__main__":
    main()
