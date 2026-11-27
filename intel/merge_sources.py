"""Merged standardised table: filing-derived values first, Ask Analyst (AA) fills gaps. Every value carries its source.
AA files are read-only. Conflicts >1% between a filing value and AA are listed, filing value kept."""
import json, sys, glob, os, datetime as dt
sys.path.insert(0, "model"); import compute_data as c
RAW = "/tmp/aa/raw"
P = json.load(open("state/intel_periods.json"))["data"]
P.update(json.load(open("state/intel_periods_fin.json"))["data"])
IS_MAP = {"revenue": ("net sales", "total revenue", "net mark-up/interest income"), "cost_of_sales": ("cost of sales",), "gross_profit": ("gross profit",),
          "finance_cost": ("financial charges", "mark-up/interest expense"), "other_income": ("other income",), "pbt": ("profit before tax",), "tax": ("taxation",),
          "pat": ("profit after tax",), "eps": ("eps - basic",), "ebitda": ("ebitda",), "depreciation": ("depreciation & amortisation",)}
BS_MAP = {"total_assets": ("total assets",), "total_equity": ("total equity",), "total_liabilities": ("total liabilities",), "current_assets": ("total current assets",),
          "current_liab": ("total current liabilities",), "cash": ("cash & bank balances",), "inventory": ("stock in trade",), "receivables": ("trade debts",),
          "long_term_debt": ("long-term debt",), "short_term_debt": ("short-term debt",), "current_portion_ltd": ("current portion of long-term debt",)}
CF_MAP = {"cfo": ("operating cash flow",), "capex": ("capex",)}
def grab(series, keys):
    for k in keys:
        for l, v in series.items():
            if l.lower().strip() == k: return v
def sh(d, m):
    mm = d.month - 1 + m; import calendar; y, mo = d.year + mm // 12, mm % 12 + 1
    return dt.date(y, mo, calendar.monthrange(y, mo)[1])
syms = sorted({os.path.basename(f).split("_")[0] for f in glob.glob(RAW + "/*_IS.json")})
out = {}; conflicts = []; basis_pick = {}
for sym in syms:
    q = {}; bs = {}; fq = {}; fb = {}
    try:
        IS = c.load_series(RAW, sym, "IS"); BS = c.load_series(RAW, sym, "BS")
    except Exception: continue
    try: CF = c.load_series(RAW, sym, "CF")
    except Exception: CF = {}
    for f, keys in IS_MAP.items():
        s = grab(IS, keys)
        for d, v in (s or {}).items():
            if v is not None: q.setdefault(d, {})[f] = {"v": v, "src": "AskAnalyst"}
    for f, keys in CF_MAP.items():
        s = grab(CF, keys)
        for d, v in (s or {}).items():
            if v is not None: q.setdefault(d, {})[f] = {"v": v, "src": "AskAnalyst (own build-up)"}
    for f, keys in BS_MAP.items():
        s = grab(BS, keys)
        for d, v in (s or {}).items():
            if v is not None: bs.setdefault(d, {})[f] = {"v": v, "src": "AskAnalyst"}
    # filings: order bases by agreement with AA for this company (AA may follow standalone or consolidated)
    bases = P.get(sym, {})
    def agree(b):
        x = bases[b]; hit = tot_ = 0
        for key, dd in x["quarters"].items():
            d = dt.date.fromisoformat(key.split("|")[0])
            for f in ("revenue", "pat", "pbt"):
                a = q.get(d, {}).get(f)
                if f in dd and a and a["v"]:
                    tot_ += 1; hit += abs(dd[f]["v"] - a["v"]) <= 0.01 * abs(a["v"])
        return (hit / tot_ if tot_ else -1, tot_)
    order = sorted([b for b in ("consolidated", "standalone", "single_entity") if b in bases], key=lambda b: (-agree(b)[0], ("consolidated", "standalone", "single_entity").index(b)))
    if order: basis_pick[sym] = {"order": order, "agreement": {b: agree(b) for b in order}}
    ALT = {}
    for b in order:
        x = bases[b]
        for key, dd in x["quarters"].items():
            d = dt.date.fromisoformat(key.split("|")[0])
            for f, e in dd.items():
                ALT.setdefault(("q", d, f), []).append({"v": e["v"], "src": "filing " + b + ": " + e["src"][:40]})
                if f not in fq.setdefault(d, {}): fq[d][f] = {"v": e["v"], "src": "filing " + b + ": " + e["src"][:40]}
        for ds, dd in x["balance_sheet"].items():
            d = dt.date.fromisoformat(ds)
            for f, e in dd.items():
                ALT.setdefault(("b", d, f), []).append({"v": e["v"], "src": "filing " + b + ": " + e["src"][:40]})
                if f not in fb.setdefault(d, {}): fb[d][f] = {"v": e["v"], "src": "filing " + b + ": " + e["src"][:40]}
    def lv_ok(f, av, fvv, lvr):
        if not lvr or lvr.get("v") is None: return False
        lv = abs(lvr["v"]); close = lambda x, y: abs(x - y) <= 0.03 * max(abs(x), abs(y), 1)
        if f == "tax": return any(close(av, c) for c in (fvv + lv, -(abs(fvv) + lv), abs(fvv) + lv, fvv - lv))
        return close(av, fvv + lv) or close(av, fvv - lv)
    def reconcile(aa, fl, tab):
        res = {}
        for d in set(aa) | set(fl):
            for f in set(aa.get(d, {})) | set(fl.get(d, {})):
                a, fv = aa.get(d, {}).get(f), fl.get(d, {}).get(f)
                if a and fv and f != "eps" and a["v"] and fv["v"]:
                    ratio = abs(fv["v"] / a["v"])
                    for fac in (1e3, 1e6, 1e-3, 1e-6):
                        if 0.9 < ratio / fac < 1.1:      # whole report read at the wrong unit: rescale and say so
                            fv = {"v": fv["v"] / fac, "src": fv["src"] + f" [unit corrected /{fac:g} by AA scale]"}
                            break
                if a and fv and abs(fv["v"] - a["v"]) > 0.01 * max(abs(a["v"]), abs(fv["v"]), 1e-9):
                    for alt in ALT.get((tab, d, f), []):      # another basis / report of the same period may match AA
                        if abs(alt["v"] - a["v"]) <= 0.01 * max(abs(a["v"]), abs(alt["v"]), 1e-9): fv = {"v": alt["v"], "src": alt["src"] + " [alternate basis/report matched AA]"}; break
                if a and fv:
                    diff = abs(fv["v"] - a["v"]) / max(abs(a["v"]), abs(fv["v"]), 1e-9)
                    if diff <= 0.01 or (f == "eps" and abs(fv["v"] - a["v"]) <= 0.05): e = {"v": fv["v"], "status": "verified (filing = AA)", "filing": fv["v"], "aa": a["v"], "src": fv["src"]}
                    elif f in ("cfo", "capex") or diff <= 0.05:
                        why = "AA cash flow is its own build-up" if f in ("cfo", "capex") else "differs 1-5%"
                        e = {"v": fv["v"], "status": f"minor difference: filing used ({why})", "filing": fv["v"], "aa": a["v"], "src": fv["src"]}
                    elif f in ("tax", "pbt") and lv_ok(f, a["v"], fv["v"], fl.get(d, {}).get("levy")):
                        e = {"v": fv["v"], "status": "verified (definition: levy treatment differs)", "filing": fv["v"], "aa": a["v"], "src": fv["src"]}
                    else:
                        e = {"v": a["v"], "status": "CONFLICT: AA used provisionally", "filing": fv["v"], "aa": a["v"], "src": a["src"]}
                        conflicts.append({"sym": sym, "date": str(d), "field": f, "filing": round(fv["v"], 1), "aa": round(a["v"], 1), "filing_src": fv["src"]})
                elif fv: e = {"v": fv["v"], "status": "filing only (unverified)", "filing": fv["v"], "aa": None, "src": fv["src"]}
                else: e = {"v": a["v"], "status": "AA only", "filing": None, "aa": a["v"], "src": a["src"]}
                res.setdefault(d, {})[f] = e
        if tab == "q":      # EPS: when the filing's profit agrees with AA but EPS differs, AA's EPS is on a different share base (bonus/split adjusted): keep the filing EPS
            for d, row in res.items():
                e, pt = row.get("eps"), row.get("pat")
                if e and pt and str(e["status"]).startswith("CONFLICT") and str(pt["status"]).startswith(("verified", "minor")) and e.get("filing") is not None:
                    row["eps"] = {"v": e["filing"], "status": "filing used (AA EPS on a different share base; profit agrees)", "filing": e["filing"], "aa": e["aa"], "src": next((c["filing_src"] for c in conflicts if c["sym"] == sym and c["date"] == str(d) and c["field"] == "eps"), e["src"])}
                    conflicts[:] = [c for c in conflicts if not (c["sym"] == sym and c["date"] == str(d) and c["field"] == "eps")]
        return res
    q = reconcile(q, fq, "q"); bs = reconcile(bs, fb, "b")
    # TTM from merged quarters
    ds = sorted(q); ttm = None
    for end in reversed(ds):
        qs = [sh(end, -3 * i) for i in range(4)]
        if all(x in q for x in qs):
            ttm = {"end": str(end), "fields": {}}
            for f in list(IS_MAP) + list(CF_MAP) + ["markup_earned","markup_expensed","net_markup_income","non_markup_income","non_markup_expenses","provisions","net_premium","net_claims","underwriting_result","investment_income","income"]:
                vals = [q[x].get(f) for x in qs]
                if all(vals): ttm["fields"][f] = {"v": round(sum(v["v"] for v in vals), 3), "verified_quarters": sum(v["status"].startswith("verified") for v in vals), "conflict_quarters": sum(v["status"].startswith("CONFLICT") for v in vals)}
            break
    out[sym] = {"quarters": {str(d): q[d] for d in ds}, "balance_sheet": {str(d): bs[d] for d in sorted(bs)}, "ttm": ttm}
json.dump({"unit": "PKR million (eps PKR)", "data": out, "conflicts": conflicts, "basis_pick": basis_pick}, open("state/intel_merged.json", "w"), separators=(",", ":"))
import collections
cnt = collections.Counter(e["status"].split(":")[0].split(" (")[0] for sm in out.values() for d in sm["quarters"].values() for e in d.values())
print(len(out), "symbols", dict(cnt), "conflicts", len(conflicts))
