"""Discrete quarters, YTD, annual and TTM from state/intel_financials.json -> state/intel_periods.json (PKR m).
Rules: quarter column picked as the smaller-revenue pair; Q4 = annual - 9M; comparatives fill gaps (lower priority);
unlabeled-period (dps) reports without period_end are listed as unplaced."""
import json, sys, datetime as dt, collections, calendar
FL = ["revenue","cost_of_sales","gross_profit","finance_cost","other_income","pbt_before_levy","levy","pbt","tax","pat","eps","cfo","cfi","cff","capex","dividends_paid","depreciation"]
BSF = ["total_assets","total_equity_liab","total_equity","current_assets","current_liab","cash","inventory","receivables"]
FIN = len(sys.argv) > 1 and sys.argv[1] == "fin"
if FIN:
    FL = ["markup_earned","markup_expensed","net_markup_income","non_markup_income","non_markup_expenses","provisions","net_premium","net_claims","underwriting_result","investment_income","income","pbt","tax","pat","eps"]
    BSF = ["total_assets","total_liabilities","total_equity","total_equity_liab","deposits","advances","investments","borrowings","cash"]
fin = json.load(open("state/intel_financials_fin.json" if FIN else "state/intel_financials.json"))
def eom(y, m): return dt.date(y, m, calendar.monthrange(y, m)[1])
def shift(d, months):
    m = d.month - 1 + months; return eom(d.year + m // 12, m % 12 + 1)
def mm(v, f): return None if v is None else (v if f == "eps" else v / 1e6)
out = {}; unplaced = []
for sym, recs in fin.items():
    ann = [dt.date.fromisoformat(r["period_end"]) for r in recs if r["kind"] == "annual" and r["period_end"]]
    if not ann: continue
    fye = collections.Counter(a.month for a in ann).most_common(1)[0][0]
    res = {}
    for basis in ("consolidated", "standalone", "single_entity"):
        ytd = {}; qd = {}; bs = {}; prov = {}
        def put(tab, key, f, val, src, prio):
            if val is None: return
            cur = tab.setdefault(key, {})
            if f not in cur or prio < cur[f][1]: cur[f] = (val, prio, src)
        for r in recs:
            if r["basis"] != basis: continue
            if not r["period_end"]: unplaced.append({"sym": sym, "report": r["report"], "basis": basis}); continue
            pe = dt.date.fromisoformat(r["period_end"]); src = r["report"] + (" OCR" if r["ocr"] else "")
            n = ((pe.month - fye) % 12) // 3 or 4
            F = r["fields"]
            for f in FL:
                x = F.get(f)
                if not x: continue
                v = x["values"]; k = len(v)
                pr = 0 if not r["ocr"] else 1
                if r["kind"] == "annual" or n == 4 and k == 2:
                    put(ytd, (pe, 4), f, mm(v[0], f), src, pr)
                    if k >= 2: put(ytd, (shift(pe, -12), 4), f, mm(v[1], f), src + " comparative", pr + 2)
                elif k == 4:
                    rv = F.get("revenue", {}).get("values") or F.get("gross_profit", {}).get("values") or F.get("pat", {}).get("values")
                    qi = 0 if (rv is None or abs(rv[0]) <= abs(rv[2])) else 2; yi = 2 - qi
                    put(qd, (pe, n), f, mm(v[qi], f), src, pr); put(qd, (shift(pe, -12), n), f, mm(v[qi + 1], f), src + " comparative", pr + 2)
                    put(ytd, (pe, n), f, mm(v[yi], f), src, pr); put(ytd, (shift(pe, -12), n), f, mm(v[yi + 1], f), src + " comparative", pr + 2)
                else:   # 2 cols: year-to-date (== quarter when n==1)
                    put(ytd, (pe, n), f, mm(v[0], f), src, pr); put(ytd, (shift(pe, -12), n), f, mm(v[1], f), src + " comparative", pr + 2)
                    if n == 1: put(qd, (pe, n), f, mm(v[0], f), src, pr); put(qd, (shift(pe, -12), n), f, mm(v[1], f), src + " comparative", pr + 2)
            for f in BSF:
                x = F.get(f)
                if x:
                    put(bs, pe, f, mm(x["values"][0], f), src, 0 if not r["ocr"] else 1)
                    if r["kind"] == "annual" and len(x["values"]) > 1: put(bs, shift(pe, -12), f, mm(x["values"][1], f), src + " comparative", 3)
        if not (ytd or qd): continue
        # discrete quarters
        keys = sorted({k for k in list(ytd) + list(qd)})
        disc = {}; notes = []
        for (pe, n) in keys:
            for f in FL:
                if f in qd.get((pe, n), {}):
                    v, pr, src = qd[(pe, n)][f]; how = "reported quarter column"
                elif f in ytd.get((pe, n), {}):
                    cur = ytd[(pe, n)][f]
                    if n == 1: v, pr, src, how = cur[0], cur[1], cur[2], "Q1 = YTD"
                    else:
                        prev = ytd.get((shift(pe, -3), n - 1), {}).get(f)
                        if not prev: continue
                        v, pr, src, how = cur[0] - prev[0], max(cur[1], prev[1]), cur[2] + " minus " + prev[2], f"YTD{n} - YTD{n-1}"
                else: continue
                disc.setdefault((pe, n), {})[f] = {"v": v, "src": src, "how": how, "prio": pr}
                # cross-check: explicit quarter + earlier YTD vs YTD
                if how == "reported quarter column" and n > 1 and f in ytd.get((pe, n), {}):
                    prev = ytd.get((shift(pe, -3), n - 1), {}).get(f)
                    if prev and f != "eps":
                        d = (v + prev[0]) - ytd[(pe, n)][f][0]
                        if abs(d) > 0.01 * max(abs(ytd[(pe, n)][f][0]), 1): notes.append({"period": str(pe), "field": f, "issue": "quarter + earlier YTD != YTD", "diff_m": round(d, 1)})
        # TTM: latest 4 consecutive discrete quarters
        ttm = None
        pes = sorted({p for p, _ in disc})
        for end in reversed(pes):
            qs = [shift(end, -3 * i) for i in range(4)]
            if all(any(p == q for p, _ in disc) for q in qs):
                ttm = {"end": str(end), "fields": {}}
                for f in FL:
                    vals = []
                    for q in qs:
                        dd = [disc[k] for k in disc if k[0] == q and f in disc[k]]
                        vals.append(dd[0][f]["v"] if dd else None)
                    if all(v is not None for v in vals): ttm["fields"][f] = round(sum(vals), 3)
                break
        res[basis] = {"fye_month": fye,
            "quarters": {f"{p}|Q{n}": {f: {"v": round(x["v"], 3), "src": x["src"], "how": x["how"]} for f, x in d.items()} for (p, n), d in sorted(disc.items())},
            "ytd": {f"{p}|Q{n}": {f: {"v": round(x[0], 3), "src": x[2]} for f, x in d.items()} for (p, n), d in sorted(ytd.items())},
            "balance_sheet": {str(p): {f: {"v": round(x[0], 3), "src": x[2]} for f, x in d.items()} for p, d in sorted(bs.items())},
            "ttm": ttm, "consistency_notes": notes}
    out[sym] = res
json.dump({"unit": "PKR million (eps in PKR)", "unplaced_reports": unplaced, "data": out}, open("state/intel_periods_fin.json" if FIN else "state/intel_periods.json", "w"), indent=1)
for s, r in out.items():
    for b, x in r.items(): print(s, b[:5], "quarters", len(x["quarters"]), "TTM", x["ttm"] and x["ttm"]["end"], "notes", len(x["consistency_notes"]))
print("unplaced", len(unplaced))
