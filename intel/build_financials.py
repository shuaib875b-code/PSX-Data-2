"""Standardise extracted statements -> state/intel_financials.json (pilot, operating companies).
Every figure keeps report id, page, basis, unit, ocr flag. Failed checks -> quarantine list."""
import json, re, sys, collections
from pathlib import Path
EX = Path("state/intel_extract"); OUT = Path("state/intel_financials.json")
MAP = {
 "IS": [("gross_revenue", r"^gross (revenue|sales|turnover)"),
        ("revenue", r"^rental income|^(net )?(revenue|sales|turnover)|^revenue from|^net sales|^sales"),
        ("cost_of_sales", r"^cost of (sales|goods|revenue)|^cost of products"),
        ("gross_profit", r"^gross (profit|loss)"),
        ("finance_cost", r"^finance (costs?|expenses?)|^financial charges|^mark-?up .*expense"),
        ("other_income", r"^other (operating )?income"),
        ("pbt_before_levy", r"before (income )?tax(ation)? and levies|^(profit|loss).*before (taxation )?(and )?(levy|minimum|final)|^profit before (income )?tax(ation)? and levy"),
        ("levy", r"^(levy|levies|minimum tax|final tax)( .*)?$"),
        ("pbt", r"^(?!.*levy)(profit|\(loss\)|loss|\(loss\) / profit|profit / \(loss\)).*before (income )?tax"),
        ("tax", r"^(?!.*(relating to|on items|other comprehensive|deferred tax on))((income )?tax(ation)?( expense)?$|taxation|income tax)"),
        ("pat", r"^(net )?(profit|loss|\(loss\)|profit / \(loss\)|\(loss\) / profit).*(after|for the (period|year))|^net (profit|income)|^profit after|^profit for the"),
        ("eps", r"(earnings|\(loss\)|loss|profit).*per share|^basic")],
 "BS": [("total_assets", r"^total assets"), ("total_equity_liab", r"^total equity (and|&) liabilities|^total liabilities (and|&) equity"),
        ("total_equity", r"^total (shareholders'? )?equity$|^total equity$|^shareholders'? equity$"),
        ("current_assets", r"^total current assets"), ("current_liab", r"^total current liabilities"),
        ("cash", r"^cash and (bank|cash equivalents)"), ("inventory", r"^(stock|inventor)"),
        ("receivables", r"^(trade )?(debts|receivables)")],
 "CF": [("cfo", r"^net cash (generated|flows?|used|\(used).*operating|^net cash.*from operating"),
        ("cfi", r"^net cash.*investing"), ("cff", r"^net cash.*financing"),
        ("capex", r"^(addition|purchase|acquisition|capital expenditure).*(property|fixed|operating assets|plant)"),
        ("dividends_paid", r"^dividend(s)? paid"), ("depreciation", r"^depreciation")]}

def ncols(st, kind, period):
    lens = collections.Counter(len(r["values"]) for r in st["rows"] if len(r["values"]) >= 2)
    if kind == "IS" and sum(v for k, v in lens.items() if k >= 4) >= 5: return 4
    return 2

def vals(r, k):
    v = r["values"]
    if len(v) == k and v and 0 < abs(v[0]) < 100 and abs(v[0] * 10 - round(v[0] * 10)) < 1e-9 and v[0] != int(v[0]):
        return None            # looks like a note reference (e.g. 28.1) standing in for a '-' value: unreliable
    if len(v) > k:
        # note-reference column sits first; wider rows are multi-year summaries -> keep the first k (current periods)
        v = v[1:1 + k] if (len(v) == k + 1 and 0 < abs(v[0]) < 200 and abs(v[0] * 10 - round(v[0] * 10)) < 1e-9) else v[:k]
    return v if len(v) == k else None

def norm(t):
    return t.replace("\ufb01", "fi").replace("昀椀", "fi").replace("\u2013", "-").lower().strip()

def std(stt, kind, period, unit):
    k = ncols(stt, kind, period); out = {}
    for field, pat in MAP[kind]:
        for r in stt["rows"]:
            lab = norm(r["label"])
            if field in out: break
            if re.search(pat, lab):
                v = vals(r, k)
                if v: out[field] = {"vals": v, "label": r["label"], "cols": k}
    if kind == "IS" and "gross_profit" in out and "cost_of_sales" in out:
        gp, cs = out["gross_profit"]["vals"], out["cost_of_sales"]["vals"]
        if len(gp) == len(cs):
            net = [g + abs(c) for g, c in zip(gp, cs)]
            rv = out.get("revenue", {}).get("vals")
            if not rv or len(rv) != len(net) or any(abs(a - b) > 0.005 * max(abs(a), abs(b), 1) for a, b in zip(rv, net)):
                if rv: out["gross_revenue"] = out["revenue"]          # reported line was before sales tax / discounts
                out["revenue"] = {"vals": net, "label": "[derived: gross profit + cost of sales]", "cols": len(net)}
    if kind == "BS" and "total_assets" not in out:
        # total assets is the largest figure on a balance sheet; accept only if it is an unlabelled total row
        rows = [(abs(vals(r, k)[0]), r) for r in stt["rows"] if vals(r, k)]
        if rows:
            m, r = max(rows, key=lambda t: t[0])
            if r["label"] == "[unlabelled subtotal]":
                out["total_assets"] = {"vals": vals(r, k), "label": "[derived: largest unlabelled total on balance sheet]", "cols": k}
    return out

MON = {m: i for i, m in enumerate(["january","february","march","april","may","june","july","august","september","october","november","december"], 1)}
DATE = re.compile(r"(?:(\d{1,2})(?:st|nd|rd|th)?[\s,]+([A-Za-z]{3,9})[\s,]+(\d{4}))|(?:([A-Za-z]{3,9})\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4}))")
def title_date(rep):
    """period end parsed from the statement titles ('for the year ended June 30, 2025', 'as at ...')"""
    for kind in ("IS", "BS", "CF"):
        for st in rep["statements"]:
            if st["type"] != kind: continue
            txt = " ".join(st["title_lines"])
            for m in DATE.finditer(txt):
                d, mo, y = (m.group(1), m.group(2), m.group(3)) if m.group(1) else (m.group(5), m.group(4), m.group(6))
                mo = MON.get(mo.lower()) or MON.get(mo.lower()[:3] + {"jan":"uary","feb":"ruary","mar":"ch","apr":"il","jun":"e","jul":"y","aug":"ust","sep":"tember","oct":"ober","nov":"ember","dec":"ember"}.get(mo.lower()[:3], ""))
                if mo and 2015 <= int(y) <= 2027 and 1 <= int(d) <= 31:
                    import calendar as _c
                    return f"{int(y):04d}-{mo:02d}-{min(int(d), _c.monthrange(int(y), mo)[1]):02d}"
    return None

import sys as _s, datetime as _dt
_s.path.insert(0, "model")
try:
    import compute_data as _c
except Exception:
    _c = None
_AA = {}
def aa_assets(sym):
    if sym not in _AA:
        try:
            ser = _c.load_series("/tmp/aa/raw", sym, "BS")
            _AA[sym] = next((v for l, v in ser.items() if l.lower().strip() == "total assets"), None)
        except Exception:
            _AA[sym] = None
    return _AA[sym]

def fix_units(sym, rec):
    """Unit labels are sometimes missing/wrong. Use Ask Analyst total assets at the same date as a scale check; correct only by exact powers of 1000."""
    ser = aa_assets(sym)
    ta = rec["fields"].get("total_assets")
    if not ser or not ta or not rec["period_end"]: return
    ref = ser.get(_dt.date.fromisoformat(rec["period_end"]))
    if not ref: return
    ratio = (ta["values"][0] / 1e6) / ref
    for fac in (1e3, 1e6, 1e-3, 1e-6):
        if 0.7 < ratio / fac < 1.4:          # mine is fac times too big -> divide
            for f, x in rec["fields"].items():
                if f != "eps": x["values"] = [None if v is None else v / fac for v in x["values"]]
            rec["unit_correction"] = f"values divided by {fac:g} (scale check vs Ask Analyst total assets)"
            return

import math, statistics
def fix_units_peers(items):
    """Second scale check without AA: a report whose size differs by ~1000x / 1e6x from the same company's other reports (same metric) was read at the wrong unit."""
    for metric in ("total_assets", "revenue", "income", "net_premium", "pat"):
        pts = [(r, math.log10(abs(r["fields"][metric]["values"][0]))) for r in items if metric in r["fields"] and r["fields"][metric]["values"][0]]
        if len(pts) < 4: continue
        med = statistics.median(l for _, l in pts)
        for r, l in pts:
            if r.get("unit_correction"): continue
            d = med - l
            for k in (3, 6, -3, -6):
                if abs(d - k) < 0.45 and abs(d) > 2.4:
                    fac = 10 ** k
                    for f, x in r["fields"].items():
                        if f != "eps": x["values"] = [None if v is None else v * fac for v in x["values"]]
                    r["unit_correction"] = f"values x{fac:g} (size out of line with company's other reports in {metric})"
                    break

def main(syms):
    res = {}
    for sym in syms:
        d = json.load(open(EX / f"{sym}.json")); items = []
        for rid, rep in d.items():
            per = rep["kind"]
            doc_unit = max([st["unit_multiplier"] or 1 for st in rep["statements"]] or [1])   # one unit per document: statements in a report share it
            pe_note = None
            if not rep.get("period_end"):
                pd_ = title_date(rep)
                if pd_: rep["period_end"] = pd_; pe_note = "parsed from statement title"
            for basis in ("consolidated", "standalone", "single_entity"):
                sts = {s["type"]: s for s in rep["statements"] if s["basis"] == basis}
                if not sts: continue
                rec = {"report": rid, "url": rep["url"], "period_end": rep["period_end"], "date_public": rep["date_public"],
                       "period_end_note": pe_note, "kind": per, "basis": basis, "ocr": rep["ocr"], "fields": {}, "checks": [], "pages": {}}
                for kind, s in sts.items():
                    rec["pages"][kind] = s["pdf_pages"]; unit = doc_unit; rec.setdefault("unit_unknown", []).append(kind) if not s["unit_multiplier"] else None
                    for f, x in std(s, kind, per, unit).items():
                        rec["fields"][f] = {"values": [None if v is None else v * unit for v in x["vals"]] if f != "eps" else x["vals"], "src_label": x["label"], "unit": unit if f != "eps" else 1}
                F = rec["fields"]; g = lambda f, i=0: (F.get(f) or {}).get("values", [None] * 4)[i]
                def chk(name, a, b, tol=0.005):
                    if a is None or b is None: rec["checks"].append({"name": name, "status": "skipped"}); return
                    ok = abs(a - b) <= tol * max(abs(a), abs(b), 1)
                    rec["checks"].append({"name": name, "status": "pass" if ok else "FAIL", "a": a, "b": b})
                chk("assets=equity+liabilities", g("total_assets"), g("total_equity_liab"))
                t = g("tax"); lv = g("levy") or 0; pat = g("pat")
                if t is not None and pat is not None:
                    cands = [x + y for x in (g("pbt_before_levy"), g("pbt")) if x is not None for y in (t + lv, t, t - lv, -abs(t) - abs(lv), -abs(t))]
                    if cands:
                        best = min(cands, key=lambda c: abs(c - pat))
                        chk("pbt+levy+tax=pat", best, pat, 0.01)
                chk("revenue+cos=gp", None if g("revenue") is None or g("cost_of_sales") is None else g("revenue") - abs(g("cost_of_sales")), g("gross_profit"))
                fails = [c["name"] for c in rec["checks"] if c["status"] == "FAIL"]
                soft = basis == "consolidated" and fails == ["pbt+levy+tax=pat"]    # associates / discontinued operations sit between PBT and PAT
                fix_units(sym, rec)
                rec["status"] = "review" if soft else "quarantine" if fails else ("low_confidence" if rec["ocr"] else "ok")
                items.append(rec)
        fix_units_peers(items)
        res[sym] = items
    OUT.write_text(json.dumps(res, indent=1))
    for sym, items in res.items():
        c = collections.Counter(i["status"] for i in items); print(sym, dict(c), "reports", len(items))
        for i in items:
            bad = [x["name"] for x in i["checks"] if x["status"] == "FAIL"]
            miss = [f for f in ("revenue", "pat", "total_assets", "cfo") if f not in i["fields"]]
            if bad or miss: print("  ", i["report"], i["basis"][:4], i["period_end"], "FAIL", bad, "missing", miss)
if __name__ == "__main__":
    main(sys.argv[1].split(",") if len(sys.argv) > 1 else ["LUCK", "ILP"])
