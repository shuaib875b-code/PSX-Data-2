"""Second-source check: extracted report figures vs Ask Analyst (PKR m). Read-only on AA files."""
import json, sys, datetime as dt
sys.path.insert(0, "model"); import compute_data as c
RAW = sys.argv[2] if len(sys.argv) > 2 else "/tmp/aa/raw"
fin = json.load(open("state/intel_financials.json")); out = {}
def pick(s, *keys):
    for k in keys:
        for l, v in s.items():
            if l.lower().startswith(k): return v
res = []
for sym in sys.argv[1].split(","):
    IS = c.load_series(RAW, sym, "IS"); BS = c.load_series(RAW, sym, "BS")
    aa = {"revenue": pick(IS, "net sales", "net revenue", "revenue", "sales", "rental income"),
          "pat": pick(IS, "profit after tax", "net profit", "profit for"), "total_assets": pick(BS, "total assets")}
    for r in fin.get(sym, []):
        if not r["period_end"]: continue
        q = dt.date.fromisoformat(r["period_end"])
        for f, ser in aa.items():
            x = r["fields"].get(f)
            if not x or not ser: continue
            v0 = x["values"][0]
            if f == "total_assets": mine = v0 / 1e6; ref = ser.get(q)
            elif r["kind"] == "annual":
                qs = [c.qshift(q, -k) for k in range(4)]; ref = sum(ser[a] for a in qs) if all(a in ser for a in qs) else None; mine = v0 / 1e6
            else:
                if len(x["values"]) < 4: continue
                mine = v0 / 1e6; ref = ser.get(q)
            if ref is None: continue
            d = (mine - ref) / ref if ref else None
            res.append({"sym": sym, "report": r["report"], "basis": r["basis"], "period_end": r["period_end"], "field": f, "mine_m": round(mine, 1), "aa_m": ref, "diff_pct": None if d is None else round(100 * d, 2), "ocr": r["ocr"]})
json.dump(res, open("state/intel_compare_aa.json", "w"), indent=1)
# per (sym,report,field) keep best-matching basis
best = {}
for x in res:
    k = (x["sym"], x["report"], x["field"])
    if k not in best or abs(x["diff_pct"]) < abs(best[k]["diff_pct"]): best[k] = x
import collections
st = collections.defaultdict(lambda: [0, 0])
for x in best.values():
    ok = abs(x["diff_pct"]) <= 1.0; st[x["sym"]][0] += ok; st[x["sym"]][1] += 1
for s, (a, b) in st.items(): print(s, f"{a}/{b} within 1% of Ask Analyst")
for x in best.values():
    if abs(x["diff_pct"]) > 1.0: print("  diff", x["sym"], x["report"], x["period_end"], x["field"], x["basis"][:4], x["mine_m"], x["aa_m"], x["diff_pct"], "OCR" if x["ocr"] else "")
