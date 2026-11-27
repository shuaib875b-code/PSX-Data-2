"""Re-OCR only the statement pages of OCR-read reports with an alternative recipe, then re-extract.
usage: reocr_pages.py VARIANT SHARD NSHARDS   (VARIANT v1 = 300dpi + contrast/threshold + psm6; v2 = 400dpi + psm4)
Reads state/intel_extract/*.json (needs pdf_pages per statement), writes state/intel_extract_v/<variant>/SYM.json (same schema)."""
import sys, os, json, glob, re, subprocess, tempfile, pathlib
sys.path.insert(0, "intel")
import fetch_reports_pilot as F, extract_statements as X
from PIL import Image, ImageOps, ImageFilter
from concurrent.futures import ThreadPoolExecutor
V, shard, n = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
DPI, PSM, THR = {"v1": (300, 6, 175), "v2": (400, 4, None)}[V]
OUT = pathlib.Path("state/intel_extract_v") / V; OUT.mkdir(parents=True, exist_ok=True)
files = sorted(glob.glob("state/intel_extract/*.json"))
todo = []
for f in files:
    d = json.load(open(f))
    for k, v in d.items():
        if v.get("ocr") and v.get("statements"): todo.append((os.path.basename(f)[:-5], k, v))
todo = todo[shard::n]
env = dict(os.environ, OMP_THREAD_LIMIT="1")
def ocr_page(args):
    pdf, pg, tmp = args
    base = os.path.join(tmp, f"p{pg}")
    subprocess.run(["pdftoppm", "-r", str(DPI), "-gray", "-png", "-f", str(pg), "-l", str(pg), pdf, base], check=False)
    fs = [x for x in os.listdir(tmp) if x.startswith(f"p{pg}") and x.endswith(".png") and x.split("-")[0] == f"p{pg}"]
    if not fs: return pg, ""
    im = Image.open(os.path.join(tmp, fs[0])).convert("L")
    if THR:
        im = ImageOps.autocontrast(im, cutoff=1).filter(ImageFilter.SHARPEN)
        im = im.point(lambda x: 255 if x > THR else 0)
    pth = os.path.join(tmp, f"q{pg}.png"); im.save(pth)
    t = subprocess.run(["tesseract", pth, "-", "--psm", str(PSM)], capture_output=True, env=env, timeout=300).stdout.decode("utf8", "ignore")
    return pg, t
res = {}
for sym, key, v in todo:
    try:
        pages = sorted({int(p) for s in v["statements"] for p in s.get("pdf_pages", [])})
        pages = sorted({q for p in pages for q in (p - 1, p, p + 1) if q >= 1})[:30]
        r = F.dl(v["url"])
        with tempfile.TemporaryDirectory() as tmp:
            pdf = os.path.join(tmp, "d.pdf"); open(pdf, "wb").write(r.content)
            with ThreadPoolExecutor(4) as ex: got = dict(ex.map(ocr_page, [(pdf, p, tmp) for p in pages]))
        txt = "".join(f"[[PDFPAGE {p}]]\n{got.get(p, '')}\f" for p in range(1, max(pages) + 1) if p in got)
        sts = X.extract_doc(txt)
        e = dict(v); e["statements"] = sts; e["variant"] = V
        res.setdefault(sym, {})[key] = e
        print(sym, key, V, [(s["type"], s["basis"][:4], len(s["rows"])) for s in sts], flush=True)
    except Exception as ex_: print("ERR", sym, key, repr(ex_)[:120], flush=True)
for sym, d in res.items():
    p = OUT / f"{sym}.json"; old = json.load(open(p)) if p.exists() else {}
    old.update(d); json.dump(old, open(p, "w"), separators=(",", ":"))
