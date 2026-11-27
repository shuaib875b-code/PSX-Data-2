"""Read the financial statements out of report text  ->  state/intel_extract/<SYM>.json

Input : state/intel_raw/reports_text/<SYM>/<key>.txt.gz  (+ index.json)  made by fetch_reports_pilot.py
Output: for every report, every primary statement found (profit or loss, financial position, cash flows) with
        - basis        consolidated / standalone / single_entity (read from the statement title, never assumed)
        - period       what the columns cover, read from the title and column headings
        - unit         multiplier read from the page (PKR '000 -> 1000, million -> 1e6); None when not found
        - rows         EVERY line of the statement: label, numbers, note reference, as printed
        - pdf_pages    pages the statement came from (so any figure can be traced)
        - ocr          True when the report had no text layer (numbers can be misread: confidence is lowered)
Nothing is mapped or interpreted here: standardisation and checks are in build_financials.py.
usage: python intel/extract_statements.py SYM[,SYM...]"""
import gzip
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "state" / "intel_raw" / "reports_text"
OUT = ROOT / "state" / "intel_extract"

NUM = re.compile(r"^\(?-?\d[\d,]*(\.\d+)?\)?,?$|^[-—–]$")
HEAD = {
    "IS": re.compile(r"statement of profit or loss|profit and loss account|profit or loss account|income statement|statement of comprehensive income|statement of profit and loss", re.I),
    "BS": re.compile(r"statement of financial position|balance sheet|statement of assets and liabilities", re.I),
    "CF": re.compile(r"statement of cash flows?|cash flows? statement", re.I),
}
MARK = re.compile(r"\[\[PDFPAGE ([^\]]*)\]\]")


def pnum(tok):
    t = tok.strip().rstrip(",")
    if t in ("-", "—", "–"):
        return 0.0
    neg = t.startswith("(") or t.startswith("-")
    t = t.strip("()").lstrip("-").replace(",", "")
    try:
        v = float(t)
    except ValueError:
        return None
    return -v if neg else v


def split_row(line):
    """label + trailing numbers (+ note ref). Returns (label, [numbers], note) or None."""
    toks = line.strip().split()
    vals = []
    while toks and NUM.match(toks[-1]):
        vals.append(toks.pop())
    vals.reverse()
    if vals and not toks and len(vals) >= 2 and all("," in v for v in vals):
        return "[unlabelled subtotal]", [pnum(v) for v in vals], vals
    if not vals or not toks:
        return None
    label = " ".join(toks)
    nums = [pnum(v) for v in vals]
    return label, nums, vals


def unit_of(txt):
    t = txt.lower().replace("’", "'").replace("‘", "'").replace('"', "'")
    if re.search(r"(rupees|pkr|rs\.?)[^\n]{0,20}(million|mn)\b|\bmillion\b", t):
        return 1e6, "million"
    if re.search(r"'000|000'|thousand|\bin 000", t):
        return 1e3, "thousand"
    if re.search(r"(rupees|pkr|rs\.?)\b", t):
        return 1.0, "rupees"
    return None, None


def basis_of(head):
    h = head.lower()
    if re.search(r"unconsolidated|stand.?alone|separate", h):
        return "standalone"
    if "consolidated" in h:
        return "consolidated"
    return "single_entity"


def classify(page):
    lines = [l for l in page.split("\n") if l.strip() and not MARK.match(l.strip())]
    head = " ".join(lines[:15])
    for k in ("BS", "CF", "IS"):
        if HEAD[k].search(head):
            if k == "IS" and re.search(r"other comprehensive income", head, re.I) and not re.search(r"profit or loss|profit and loss", head, re.I):
                return None, head           # the OCI statement, not the income statement
            return k, head
    return None, head


def parse_page(page):
    rows = []
    for ln in page.split("\n"):
        if not ln.strip() or MARK.match(ln.strip()):
            continue
        r = split_row(ln)
        if r:
            rows.append({"label": r[0], "values": r[1], "raw": r[2]})
    return rows


def score(kind, rows):
    labs = " | ".join(r["label"].lower() for r in rows)
    sc = min(len(rows), 40) / 4
    if kind == "IS":
        sc += 10 * ("per share" in labs) + 5 * bool(re.search(r"gross (profit|loss)|profit before|(loss|profit).*before", labs)) + 5 * bool(re.search(r"after tax|after taxation", labs))
    elif kind == "BS":
        sc += 10 * ("total assets" in labs) + 10 * bool(re.search(r"total equity and liabilities|total liabilities and equity|total equity & liabilities", labs)) + 3 * ("total current" in labs)
    else:
        sc += 10 * bool(re.search(r"operating activities", labs)) + 10 * bool(re.search(r"cash and cash equivalents|cash and bank", labs)) + 3 * bool(re.search(r"investing", labs)) + 3 * bool(re.search(r"financing", labs))
    return sc


def extract_doc(txt):
    pages = [p for p in txt.split("\f") if p.strip()]
    found = []
    for p in pages:
        m = MARK.search(p)
        pg = m.group(1).strip() if m else "?"
        kind, head = classify(p)
        nums = len(re.findall(r"\d[\d,]{3,}", p))
        if kind and nums >= 12 and not re.search(r"notes to the", head, re.I):
            found.append({"kind": kind, "head": head, "pg": pg, "text": p})
        elif not kind and found and nums >= 12 and found[-1]["kind"] == "BS" and found[-1].get("cont", 0) < 2 \
                and re.search(r"\bnote\b|audited|rupees|rs\.", head, re.I) and not re.search(r"notes to|statement|report|auditor", head, re.I):
            f0 = found[-1]; f0["cont"] = f0.get("cont", 0) + 1       # heading-less continuation of a balance sheet
            found.append({"kind": "BS", "head": f0["head"], "pg": pg, "text": p, "cont": f0["cont"]})
    # merge continuation pages (balance sheet = assets page + equity/liabilities page, etc.)
    st = []
    for f in found:
        prev = st[-1] if st else None
        num = lambda x: int(x.split()[0]) if x.split() and x.split()[0].isdigit() else -9
        if prev and prev["kind"] == f["kind"] and basis_of(prev["head"]) == basis_of(f["head"]) and 0 < num(f["pg"]) - num(prev["pg_last"]) <= 1 and prev["n"] < 3:
            prev["texts"].append(f["text"]); prev["pgs"].append(f["pg"]); prev["pg_last"] = f["pg"]; prev["n"] += 1
        else:
            st.append({"kind": f["kind"], "head": f["head"], "pgs": [f["pg"]], "pg_last": f["pg"], "texts": [f["text"]], "n": 1})
    cands = []
    for s in st:
        full = "\n".join(s["texts"])
        u, un = unit_of(full[:3500])
        rows = []
        for t in s["texts"]:
            rows += parse_page(t)
        title_lines = [l.strip() for l in s["texts"][0].split("\n") if l.strip() and not MARK.match(l.strip())][:9]
        cands.append({"type": s["kind"], "basis": basis_of(s["head"]), "title_lines": title_lines, "pdf_pages": s["pgs"], "unit_multiplier": u, "unit_text": un, "rows": rows, "score": round(score(s["kind"], rows), 1)})
    # an unlabelled set that sits beside a 'consolidated' set is the standalone one
    if any(c["basis"] == "consolidated" for c in cands):
        for c in cands:
            if c["basis"] == "single_entity":
                c["basis"] = "standalone"
                c["basis_note"] = "unlabelled set next to a consolidated set: taken as standalone"
    best = {}
    for c in cands:
        k = (c["type"], c["basis"])
        if k not in best or c["score"] > best[k]["score"]:
            best[k] = c
    return sorted(best.values(), key=lambda c: (c["basis"], c["type"]))


def main(symbols):
    OUT.mkdir(parents=True, exist_ok=True)
    idx = json.load(open(RAW / "index.json"))
    for f_ in sorted((RAW.parent / "reports_index").glob("*.json")):
        for s_, d_ in json.load(open(f_)).items(): idx.setdefault(s_, {}).update(d_)
    for sym in symbols:
        res = json.load(open(OUT / f"{sym}.json")) if (OUT / f"{sym}.json").exists() else {}
        for key, meta in idx.get(sym, {}).items():
            if meta.get("status") != "ok":
                continue
            if not (RAW / sym / f"{key}.txt.gz").exists(): continue
            txt = gzip.open(RAW / sym / f"{key}.txt.gz", "rt", errors="ignore").read()
            import signal
            def _to(*a): raise TimeoutError()
            signal.signal(signal.SIGALRM, _to); signal.alarm(300)
            try: sts = extract_doc(txt)
            except TimeoutError: print("EXTRACT TIMEOUT", sym, key, flush=True); continue
            finally: signal.alarm(0)
            res[key] = {"kind": meta["kind"], "period_end": meta.get("period_end"), "date_public": meta.get("date_public"), "date_note": meta.get("date_note"),
                        "source": meta["source"], "url": meta["url"], "pdf_sha256": meta.get("sha256"), "ocr": meta.get("ocr", False), "statements": sts}
            print(sym, key, meta["kind"][:4], meta.get("period_end"), [(s["type"], s["basis"][:4], len(s["rows"])) for s in sts], flush=True)
        json.dump(res, open(OUT / f"{sym}.json", "w"), separators=(",", ":"))


if __name__ == "__main__":
    main([s.strip().upper() for s in sys.argv[1].split(",")])
