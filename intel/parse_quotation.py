"""Parse a PSX Daily Quotations PDF into a seed list of Main Board / GEM symbols.
usage: python intel/parse_quotation.py QUOTE.pdf OUT.json
The PDF is the official exchange list; names in it are letter-spaced ("A bhi") so they are kept only as a rough label.
Sections used: Section 4 MAIN BOARD (NORMAL COUNTER), GEM BOARD (NORMAL COUNTER), Section 13 NON-COMPLIANT SEGMENT (main board)."""
import hashlib, json, re, subprocess, sys, datetime

HDR = re.compile(r"^[A-Z][A-Z &/.,'()-]{4,}$")
ROW = re.compile(r"^\s*(\d+)\s+(.+?)\s{2,}([A-Z0-9][A-Z0-9]{0,9})\s+(\S+)\s")
JUNK = {"JUNE", "DEC", "MAR", "SEP", "TF", "NT", "NIL"}


def main(pdf, out):
    raw = open(pdf, "rb").read()
    txt = subprocess.run(["pdftotext", "-layout", pdf, "-"], capture_output=True, check=True).stdout.decode("utf8", "ignore")
    L = txt.split("\n")
    m = re.search(r"(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),\s+(\w+ \d+, \d{4})", txt[:3000])
    qdate = datetime.datetime.strptime(m.group(2), "%b %d, %Y").date().isoformat() if m else None
    marks = {}
    for i, s in enumerate(L):
        t = s.strip()
        if t.startswith("MAIN BOARD (NORMAL COUNTER)"): marks.setdefault("main", i)
        elif t.startswith("GEM BOARD (NORMAL COUNTER)"): marks.setdefault("gem", i)
        elif t.startswith("PUBLICLY ISSUED DEBT SECURITIES") and "main" in marks and "gem" in marks and "gem_end" not in marks: marks["gem_end"] = i
        elif t.startswith("SECTION 13: NON-COMPLIANT"): marks["nc"] = i
        elif t.startswith("SECTION 14: SUSPENDED") and "nc" in marks and "nc_end" not in marks: marks["nc_end"] = i
    spans = [("main_normal", marks["main"], marks["gem"]), ("gem", marks["gem"], marks["gem_end"]), ("main_noncompliant", marks["nc"], marks["nc_end"])]
    rows, sec = [], None
    for seg, a, b in spans:
        sec = None
        for i in range(a, b):
            s = L[i].rstrip()
            if HDR.match(s.strip()) and not s.startswith(" "):
                sec = s.strip(); continue
            s = re.sub(r"5\.11\.\d\.?\([^)]*\)(,\s*5\.11\.\d+\([^)]*\))?", " ", s)
            r = ROW.match(s)
            if r and sec and r.group(3) not in JUNK:
                rows.append({"symbol": r.group(3), "name_rough": re.sub(r"\s+", " ", r.group(2)).strip(), "sector": sec, "segment": seg})
    seen, uniq = set(), []
    for r in rows:
        if r["symbol"] in seen: continue
        seen.add(r["symbol"]); uniq.append(r)
    doc = {"source": "PSX Daily Quotations", "quotation_date": qdate, "file": pdf.split("/")[-1], "sha256": hashlib.sha256(raw).hexdigest(),
           "parsed_at": datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None).isoformat() + "Z", "rows": uniq,
           "counts": {k: sum(1 for r in uniq if r["segment"] == k) for k in ("main_normal", "gem", "main_noncompliant")}}
    json.dump(doc, open(out, "w"), indent=1)
    print(doc["quotation_date"], doc["counts"], len(uniq))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
